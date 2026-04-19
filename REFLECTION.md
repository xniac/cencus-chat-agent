# Reflection

## Process & Key Decisions

I chose a **text-to-SQL** approach over a pre-built query library to maximize the range of answerable questions. The pipeline is a series of small, single-responsibility services:

```
Input → Topic Guardrail → Schema-Aware SQL Gen → Safety Validator
      → Snowflake (in thread) → [retry on error] → Response Stream
```

### Key architectural choices
- **Two-phase topic guardrail** — fast keyword/regex check handles ~90% of cases; LLM fallback only for ambiguous inputs. Fails open on LLM errors (availability over false rejections).
- **Question-aware schema context** — SafeGraph's schema has 70+ tables with 5000+ columns and cryptic ACS codes (`B19013e1`). I load the `METADATA_CBG_FIELD_DESCRIPTIONS` table at startup and filter it by question keywords at request time, with prefix-based matching (so "populated" matches "population"). This keeps prompts under 15K tokens vs. 42K for the full schema.
- **Retry-on-SQL-error** — LLMs occasionally hallucinate column names (e.g., `B01002ae1`). When Snowflake returns a compilation error, I feed the error back to the LLM and retry once. This recovers most hallucinations without blowing the latency budget.
- **Deterministic UNION wrapping** — LLMs inconsistently follow Snowflake's requirement to wrap each UNION arm in parentheses. A regex post-processor (`_wrap_union_arms`) handles this reliably, no matter what the LLM outputs.
- **`asyncio.to_thread` for Snowflake** — the connector is synchronous; wrapping calls in a thread pool keeps the event loop responsive under concurrent load.
- **Per-step timeouts** (`asyncio.wait_for`) — topic check 15s, SQL gen 45s, SQL fix 30s. Streaming itself has no cap: the 60s requirement is about *first content*, and the user sees `thinking`/`sql`/`data` events within seconds.
- **SSE over WebSocket** — simpler to implement, debug, and proxy for a request-response chat. Users see tokens as they stream.
- **In-memory sessions** — TTL-based; `SessionStore` is swappable for Redis without touching callers.
- **Pluggable LLM provider** — OpenAI and Anthropic share a `Protocol`; switching is one env var.

## What I'd Improve With More Time

- **Vector-based schema retrieval** — the current pipeline is keyword-matching plus a small hand-maintained `CENSUS_SYNONYMS` dict (racial→race/ethnic, housing→house/household, kids→child/children, etc.) that covers the synonym gap between user phrasing and ACS field descriptions. It works, but it's a moving target: new user phrasings need new entries. Embedding column descriptions + sample values would remove the dict entirely and handle synonyms, related concepts (e.g., "commute" → travel-time-to-work columns), and phrasing we haven't anticipated — at the cost of an embedding-model dependency and per-startup cost to embed ~5000 field descriptions.
- **Query caching** — cache (normalized question + schema hash) → SQL to eliminate LLM calls for repeated questions.
- **Conversation summarization** — truncating history at N messages loses early context; a summarization step would preserve facts like "the user is interested in California demographics."
- **Evaluation framework** — curated (question, expected-SQL-pattern) pairs with accuracy + latency + cost metrics. Essential for iterating on prompts without regressions.
- **Observability** — structured logging, metrics on SQL success rate, Snowflake latency percentiles, guardrail accuracy. Currently only basic logging.
- **Geographic resolver** — map informal place names ("Bay Area", "the Midwest", "NYC") to FIPS codes. Today users have to say "California" not "CA" (though the LLM handles that reasonably).
- **Data visualizations** — simple charts for comparative questions (bar/map). The markdown tables work but charts read faster.
- **Rate limiting** — no per-user limits yet. In production this would be essential for cost control.
- **Multi-provider failover** — circuit-breaker pattern across OpenAI/Anthropic when one provider has an outage.

## Edge Cases

### Handled
- **Off-topic queries** — two-tier guardrail with clear, census-themed rejection
- **SQL injection / destructive ops** — regex validator blocks everything but SELECT/WITH, multi-statement, and dangerous keywords. Accepts UNION-wrapped arms.
- **LLM hallucinated columns** — retry loop feeds Snowflake errors back to the LLM for self-correction
- **LLM API errors** (auth, rate limit, credit balance) — categorized into user-friendly messages, not raw JSON dumps
- **LLM timeouts** — `asyncio.wait_for` on every non-streaming LLM call; fail gracefully
- **Snowflake syntax quirks** — deterministic UNION-arm wrapping; explicit rules for `ILIKE` vs `LIKE`, `IFF` vs `IF`, `::FLOAT` for percentage casts
- **Empty results** — clear explanation + rephrasing suggestions
- **Snowflake connection / query errors** — typed exceptions surface distinct user messages; sensitive words sanitized from error strings
- **Client cancellation** — frontend `AbortController` aborts fetch; backend's async generator receives `CancelledError` via `sse-starlette` and stops LLM/Snowflake work
- **Event loop blocking** — Snowflake calls wrapped in `asyncio.to_thread`; scheduled schema refreshes run in a background task (same mechanism) so the hourly refresh never blocks request handling
- **Stateful multi-turn continuity** — `ConversationSession` persists all messages for the session lifetime (30-min sliding TTL). Two history accessors: `get_history_for_llm()` for response generation and `get_history_for_sql_gen()` for SQL generation (the latter embeds prior SQL in the assistant message content). Follow-ups like "what about the bottom 5?" or "break that down by age" see the previous SQL and reason from it. `session_id` round-trips via SSE; the frontend echoes it on every subsequent request automatically.
- **Ambiguous "cannot answer"** — `CANNOT_ANSWER:` LLM response is distinguished from API errors and shown with a helpful suggestion
- **SSE line-ending mismatch** — caught during integration testing: `sse-starlette` emits `\r\n\r\n` event separators, but my frontend/test parsers originally split on `\n\n`. Fixed by normalizing `\r\n` → `\n` before splitting, and also fixed the data-line parser to preserve leading spaces per the SSE spec (otherwise streamed tokens like `" Median"` would lose their leading space)
- **False-positive SQL safety blocks** — initial validator blocked any query containing the word `GET` or `PUT` (for Snowflake's staging commands), which rejected legitimate queries with `'GET'`/`'PUT'` string literals. Removed those patterns from the denylist; the `must start with SELECT/WITH` check still blocks real `GET`/`PUT` commands

### Identified but not fully addressed
- **Very broad queries** — "Tell me everything about the US" would still generate expensive aggregates. `LIMIT 100` is a safety net but query-cost estimation would be better.
- **Conflicting granularity** — the same question could be answered at CBG, county, or state level. Currently the LLM picks; explicit disambiguation UI would be more reliable.
- **Concurrent sessions at scale** — in-memory sessions don't survive restarts or scale horizontally. Redis-backed sessions for production.
- **Margin-of-error columns** — ACS provides both `e` (estimate) and `m` (margin) columns. The system uses estimates only. A research-grade tool should surface margins.
- **"Median of medians" approximation** — averaging per-CBG medians to get state medians is statistically imperfect. The prompt mentions this but users may not notice the caveat.

## Testing Strategy

### Coverage (104 tests)
- **Guardrails (37 tests)** — SQL safety (dangerous operations blocked, multi-statement, wrapped UNION accepted, string-literal false positives eliminated), topic classification (keywords, patterns, greetings, LLM fallback with history, fail-open)
- **Sessions (15 tests)** — lifecycle (create / retrieve / expire), history (trimming, plain vs. SQL-included), store-level concurrency, eager cleanup on expired access
- **Schema Cache (14 tests)** — context generation, truncation, fallback on errors, refresh mechanics, synonym expansion (racial, housing, job, kids, plurals), stop-word filtering
- **SQL Generator (19 tests)** — `generate_sql` (success, `CANNOT_ANSWER`, API errors, history propagation, markdown cleanup, UNION wrapping applied), `fix_sql` (retry success/failure, error context, history propagation), `_wrap_union_arms` (UNION/INTERSECT/EXCEPT, already-wrapped, multi-arm)
- **Response Generator (8 tests)** — `_format_results` (empty, small, >20 truncation, non-JSON-serializable), `generate_response_stream` (tokens, prompt content, history), `generate_response`
- **Chat API (11 tests)** — full SSE flow end-to-end, off-topic rejection (keyword + LLM paths), connection errors, empty results, unsafe SQL, session continuity, `CANNOT_ANSWER` handling, ambiguous-follow-up trust-in-active-session, still-blocks-off-topic-in-active-session

### Philosophy
Tests target **business logic boundaries** — guardrails, session management, SQL-cleanup, and API integration — rather than chasing 100% coverage. Mocks for Snowflake and LLM keep tests fast, deterministic, and runnable without credentials.

### What I'd add
- **SQL-generation quality regression suite** — curated (question, expected SQL pattern) pairs to catch prompt regressions
- **Frontend tests** — React Testing Library for SSE parsing, cancel flow, message rendering
- **Load tests** — k6 / Locust to verify first-byte latency stays under 60s with concurrent users
- **Disconnect-propagation test** — simulate client abort mid-stream, assert backend stops issuing LLM calls
- **Response-grounding check** — automated assertion that streamed answers reference actual column values, not hallucinations
