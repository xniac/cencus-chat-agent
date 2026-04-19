import asyncio
import logging
import time
from typing import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.app.models.schemas import ChatRequest, HealthResponse
from backend.app.services.guardrails import (
    quick_topic_check,
    llm_topic_check,
    validate_sql_safety,
)
from backend.app.services.sql_generator import generate_sql, fix_sql, SQLGenResult
from backend.app.services.response_generator import generate_response_stream
from backend.app.services.snowflake_client import SnowflakeConnectionError, SnowflakeQueryError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Health check cache — avoid opening a fresh Snowflake connection on every
# scheduler ping (Render hits /api/health ~every 30s). Short TTL so legitimate
# outages still surface within about a minute.
_HEALTH_TTL_SECONDS = 30
_health_cache: dict[str, float | bool] = {"ok": False, "timestamp": 0.0}


def _sse_event(event: str, data: str) -> dict:
    return {"event": event, "data": data}


def _friendly_llm_error(error_msg: str) -> str:
    lower = error_msg.lower()
    if "credit balance" in lower:
        return (
            "The LLM provider reports insufficient credits on the configured API key. "
            "Please add credits to your Anthropic/OpenAI account or switch providers in the server config."
        )
    if "rate limit" in lower or "429" in lower:
        return "The LLM provider is rate-limiting requests. Please wait a moment and try again."
    if "authentication" in lower or "api key" in lower or "401" in lower or "403" in lower:
        return "The LLM provider rejected the API key. Please check the server's API key configuration."
    # Generic fallback — trim the raw error
    trimmed = error_msg if len(error_msg) < 200 else error_msg[:200] + "..."
    return f"I couldn't generate a response for that question. ({trimmed})"


@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    snowflake = request.app.state.snowflake
    llm = request.app.state.llm
    schema_cache = request.app.state.schema_cache
    session_store = request.app.state.session_store
    db = request.app.state.database
    schema = request.app.state.schema

    session = await session_store.get_or_create(body.session_id)

    async def event_stream() -> AsyncIterator[dict]:
        # Note: we don't need manual disconnect checks. sse-starlette cancels
        # this generator (raising CancelledError) when the client disconnects,
        # which propagates through `await` points and stops the LLM/Snowflake
        # calls naturally.
        try:
            # Step 1: Topic guardrail
            yield _sse_event("thinking", "Checking if your question is about census data...")

            topic_result = quick_topic_check(body.message)
            if topic_result is None:
                # If the session has had at least one successful census query
                # (assistant message with SQL), trust follow-ups. The quick
                # check above still rejects anything matching off-topic
                # patterns (weather, sports, recipes, etc.), so this is safe.
                has_successful_prior = any(
                    m.role == "assistant" and m.sql for m in session.messages
                )
                if has_successful_prior:
                    topic_result = "on_topic"
                    logger.debug("Trusting follow-up in active census session")
                else:
                    # Fresh session: ask LLM, passing history for context if any
                    topic_history = session.get_history_for_llm() if session.messages else None
                    topic_result = "on_topic" if await llm_topic_check(
                        body.message, llm, history=topic_history
                    ) else "off_topic"

            if topic_result == "off_topic":
                yield _sse_event(
                    "error",
                    "I can only answer questions about US Census data, including "
                    "demographics, population, housing, income, education, employment, "
                    "and related topics. Please ask a census-related question!",
                )
                yield _sse_event("done", "")
                return

            # Add user message to session
            session.add_message("user", body.message)

            # Step 2: Generate SQL
            yield _sse_event("thinking", "Generating SQL query...")

            # History for SQL generation INCLUDES previous SQL for multi-turn coherence
            history_with_sql = session.get_history_for_sql_gen()
            # History for response generation omits SQL (just role+content)
            history_plain = session.get_history_for_llm()

            # Use question-aware schema context to prioritize relevant tables.
            # Concatenate the last 3 user messages so follow-ups like "break
            # that down by age" inherit keywords from earlier turns (e.g.,
            # "population", "states") when ranking tables/columns.
            recent_user_msgs = [
                m.content for m in session.messages[-6:] if m.role == "user"
            ][-3:]
            combined_for_schema = "\n".join(recent_user_msgs)
            if body.message not in combined_for_schema:
                combined_for_schema = (combined_for_schema + "\n" + body.message).strip()

            schema_ctx = await asyncio.to_thread(
                schema_cache.get_context_for_question
                if hasattr(schema_cache, "get_context_for_question")
                else (lambda _q: schema_cache.schema_context),
                combined_for_schema,
            )
            result, sql, reason = await generate_sql(
                question=body.message,
                schema_context=schema_ctx,
                history=history_with_sql[:-1],  # Exclude the current user message (passed in separately)
                llm_provider=llm,
                database=db,
                schema=schema,
            )

            if result == SQLGenResult.API_ERROR:
                friendly = _friendly_llm_error(reason or "")
                session.add_message("assistant", friendly)
                yield _sse_event("error", friendly)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return

            if result == SQLGenResult.CANNOT_ANSWER:
                msg = (
                    f"I can't answer that from the available census data. {reason}\n\n"
                    "Try rephrasing or asking about population, demographics, race/ethnicity, "
                    "age, housing, commute, education, or employment at the block-group, "
                    "county, or state level."
                )
                session.add_message("assistant", msg)
                yield _sse_event("answer", msg)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return

            # Step 3: Validate SQL safety
            is_safe, safety_error = validate_sql_safety(sql)
            if not is_safe:
                msg = f"The generated query was blocked for safety: {safety_error}"
                session.add_message("assistant", msg)
                yield _sse_event("error", msg)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return

            yield _sse_event("sql", sql)

            # Step 4: Execute query — run in a worker thread so the event loop
            # isn't blocked while Snowflake processes the query.
            # Retry once with LLM self-correction if the query fails with a
            # SQL compilation error (common text-to-SQL failure mode).
            yield _sse_event("thinking", "Querying Snowflake...")

            try:
                results = await asyncio.to_thread(snowflake.execute_query, sql)
            except SnowflakeConnectionError as e:
                msg = "I'm having trouble connecting to the database. Please try again in a moment."
                logger.error(f"Snowflake connection error: {e}")
                session.add_message("assistant", msg)
                yield _sse_event("error", msg)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return
            except SnowflakeQueryError as e:
                logger.warning(f"SQL error on first attempt, asking LLM to fix: {e}")
                yield _sse_event("thinking", "Fixing SQL error and retrying...")

                fix_result, fixed_sql, fix_reason = await fix_sql(
                    question=body.message,
                    failed_sql=sql,
                    error_message=str(e),
                    schema_context=schema_ctx,
                    llm_provider=llm,
                    database=db,
                    schema=schema,
                    history=history_with_sql[:-1],
                )

                if fix_result != SQLGenResult.OK or not fixed_sql:
                    msg = f"The query couldn't be fixed automatically. {fix_reason or str(e)}"
                    session.add_message("assistant", msg)
                    yield _sse_event("error", msg)
                    yield _sse_event("session_id", session.session_id)
                    yield _sse_event("done", "")
                    return

                # Validate the fixed SQL is still safe
                is_safe_fixed, safety_err_fixed = validate_sql_safety(fixed_sql)
                if not is_safe_fixed:
                    msg = f"The corrected query was blocked for safety: {safety_err_fixed}"
                    session.add_message("assistant", msg)
                    yield _sse_event("error", msg)
                    yield _sse_event("session_id", session.session_id)
                    yield _sse_event("done", "")
                    return

                sql = fixed_sql
                yield _sse_event("sql", sql)
                try:
                    results = await asyncio.to_thread(snowflake.execute_query, sql)
                except (SnowflakeConnectionError, SnowflakeQueryError) as e2:
                    msg = (
                        f"Even after retrying, the query failed: {str(e2)}. "
                        "Try rephrasing your question."
                    )
                    logger.error(f"Snowflake query error on retry: {e2}")
                    session.add_message("assistant", msg)
                    yield _sse_event("error", msg)
                    yield _sse_event("session_id", session.session_id)
                    yield _sse_event("done", "")
                    return

            # Step 5: Handle empty results
            if not results:
                msg = (
                    "The query returned no results. This could mean the data doesn't exist "
                    "for the specific criteria, or the question might need to be rephrased. "
                    "Try broadening your search or asking about a different geographic area."
                )
                session.add_message("assistant", msg, sql=sql)
                yield _sse_event("answer", msg)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return

            yield _sse_event("data", str(len(results)))

            # Step 6: Stream response
            yield _sse_event("thinking", "Analyzing results...")

            full_response = ""
            async for token in generate_response_stream(
                question=body.message,
                sql=sql,
                results=results,
                history=history_plain[:-1],
                llm_provider=llm,
            ):
                full_response += token
                yield _sse_event("answer_token", token)

            # Step 7: Save to session
            session.add_message("assistant", full_response, sql=sql)
            yield _sse_event("session_id", session.session_id)
            yield _sse_event("done", "")

        except Exception as e:
            logger.exception(f"Unexpected error in chat: {e}")
            yield _sse_event("error", "An unexpected error occurred. Please try again.")
            yield _sse_event("done", "")

    return EventSourceResponse(event_stream())


@router.get("/health")
async def health(request: Request):
    snowflake = request.app.state.snowflake
    now = time.time()
    # Use cached result if fresh (avoids per-probe Snowflake login under
    # Render's ~30s health check interval).
    if now - float(_health_cache["timestamp"]) < _HEALTH_TTL_SECONDS:
        connected = bool(_health_cache["ok"])
    else:
        connected = await asyncio.to_thread(snowflake.test_connection)
        _health_cache["ok"] = connected
        _health_cache["timestamp"] = now
    return HealthResponse(
        status="healthy" if connected else "degraded",
        snowflake_connected=connected,
        llm_provider=request.app.state.llm_provider_name,
    )
