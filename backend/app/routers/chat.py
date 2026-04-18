import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.app.models.schemas import ChatRequest, HealthResponse
from backend.app.services.guardrails import (
    quick_topic_check,
    llm_topic_check,
    validate_sql_safety,
)
from backend.app.services.sql_generator import generate_sql, SQLGenResult
from backend.app.services.response_generator import generate_response_stream
from backend.app.services.snowflake_client import SnowflakeConnectionError, SnowflakeQueryError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _sse_event(event: str, data: str) -> dict:
    return {"event": event, "data": data}


def _is_llm_api_error(error_msg: str) -> bool:
    lower = error_msg.lower()
    return any(k in lower for k in ["credit balance", "invalid_request_error", "401", "403", "authentication", "api key", "rate limit", "429"])


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
        async def check_disconnect() -> bool:
            """Return True if the client has disconnected (e.g., hit stop)."""
            try:
                return await request.is_disconnected()
            except Exception:
                return False

        try:
            # Step 1: Topic guardrail
            yield _sse_event("thinking", "Checking if your question is about census data...")

            if await check_disconnect():
                logger.info("Client disconnected before topic check — aborting")
                return

            topic_result = quick_topic_check(body.message)
            if topic_result is None:
                topic_result = "on_topic" if await llm_topic_check(body.message, llm) else "off_topic"

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

            if await check_disconnect():
                logger.info("Client disconnected before SQL generation — aborting")
                return

            # Step 2: Generate SQL
            yield _sse_event("thinking", "Generating SQL query...")

            history = session.get_history_for_llm()
            # Use question-aware schema context to prioritize relevant tables
            schema_ctx = (
                schema_cache.get_context_for_question(body.message)
                if hasattr(schema_cache, "get_context_for_question")
                else schema_cache.schema_context
            )
            result, sql, reason = await generate_sql(
                question=body.message,
                schema_context=schema_ctx,
                history=history[:-1],  # Exclude the current message (already in prompt)
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

            if await check_disconnect():
                logger.info("Client disconnected before Snowflake query — aborting")
                return

            # Step 4: Execute query
            yield _sse_event("thinking", "Querying Snowflake...")

            try:
                results = snowflake.execute_query(sql)
            except SnowflakeConnectionError as e:
                msg = "I'm having trouble connecting to the database. Please try again in a moment."
                logger.error(f"Snowflake connection error: {e}")
                session.add_message("assistant", msg)
                yield _sse_event("error", msg)
                yield _sse_event("session_id", session.session_id)
                yield _sse_event("done", "")
                return
            except SnowflakeQueryError as e:
                msg = f"The query encountered an error: {str(e)}. Let me try to help differently."
                logger.error(f"Snowflake query error: {e}")
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
            token_count = 0
            async for token in generate_response_stream(
                question=body.message,
                sql=sql,
                results=results,
                history=history[:-1],
                llm_provider=llm,
            ):
                # Check for client disconnect every 5 tokens (balance between
                # responsiveness and overhead)
                token_count += 1
                if token_count % 5 == 0 and await check_disconnect():
                    logger.info("Client disconnected during streaming — aborting")
                    return
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
    connected = snowflake.test_connection()
    return HealthResponse(
        status="healthy" if connected else "degraded",
        snowflake_connected=connected,
        llm_provider=request.app.state.llm_provider_name,
    )
