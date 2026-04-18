# Reflection

## Development Process

### Phase 1: Architecture & Design
I began by analyzing the assignment requirements and the US Open Census dataset structure on Snowflake. The core challenge was building a system that could translate arbitrary natural language questions into correct SQL against a schema with hundreds of columns — without hardcoding queries for specific question types.

I chose a **text-to-SQL approach** over a pre-built query library because it maximizes the range of questions the agent can answer. The tradeoff is that LLM-generated SQL can sometimes be incorrect or suboptimal, but with proper schema context and safety validation, this approach scales far better than manually mapping question patterns to queries.

### Phase 2: Backend Implementation
The backend follows a pipeline architecture:

```
Input → Guardrails → SQL Generation → Safety Validation → Execution → Response Generation → Output
```

Each stage is a separate service with a single responsibility. This makes testing straightforward (each service can be tested in isolation with mocks) and makes it easy to swap components (e.g., switching LLM providers).

Key implementation decisions:
- **Dynamic schema caching** rather than hardcoded schema strings — the schema is fetched from Snowflake's INFORMATION_SCHEMA at startup and refreshed hourly. This means the agent adapts if tables or columns change.
- **Two-phase topic guardrails** — a fast keyword/regex check handles obvious on-topic and off-topic messages (~90% of cases) without an LLM call. Only ambiguous messages go to the LLM classifier. This saves latency and cost while maintaining accuracy.
- **SSE streaming** over WebSocket — for a request-response chat pattern, SSE is simpler to implement, debug, and proxy. The user sees tokens as they're generated, keeping perceived latency low.

### Phase 3: Frontend & Integration
The React frontend is intentionally simple — a single-page chat interface with no routing or state management library. The `useChat` hook manages all state and SSE parsing. I prioritized a clean, responsive UI with a dark theme and clear visual hierarchy (user vs. assistant messages, expandable SQL, error states).

## Key Decisions

### Two-Phase LLM Architecture
The system makes two LLM calls per successful query: one for SQL generation and one for response generation. I considered a single-call approach (LLM generates both SQL and explanation), but separating them provides:
- Better SQL quality (the SQL generation prompt is focused and constrained)
- Ability to validate SQL before executing it
- Streaming of the response while the user sees the SQL immediately

### Dynamic Schema Discovery
Rather than hardcoding schema information, the system queries Snowflake's INFORMATION_SCHEMA on startup. This adds a startup cost but means:
- No schema drift — the agent always has current metadata
- The system works with any Snowflake database, not just a specific snapshot
- Column comments and row counts provide additional context for the LLM

### Keyword + LLM Guardrails
Pure keyword matching would miss nuanced off-topic queries; pure LLM classification would add latency to every request. The two-tier approach handles the common cases fast and falls back to the LLM only when needed. The system also fails open — if the LLM classifier errors, the message is allowed through rather than blocked, prioritizing availability over false rejections.

### In-Memory Sessions
For a demo deployment, in-memory sessions with TTL expiration are sufficient. Redis or a database-backed session store would be needed for production scale, but adding that dependency for a single-instance demo would be overengineering. The code is structured so that `SessionStore` could be replaced with a Redis-backed implementation without changing the rest of the application.

## What I Would Improve With More Time

### Schema-Aware RAG
For databases with hundreds of columns, including the full schema in every prompt is token-expensive. A retrieval-augmented approach would:
1. Embed column descriptions and sample values
2. Retrieve only the most relevant tables/columns for each question
3. Reduce prompt size and improve SQL accuracy for large schemas

### Query Caching
Many users ask similar questions. Caching generated SQL (keyed by normalized question + schema hash) would eliminate redundant LLM calls and Snowflake queries for common questions.

### Conversation Summarization
Currently, the system sends the last N messages as history. For long conversations, this loses early context. A summarization step could compress older messages while preserving key facts (e.g., "the user is interested in California demographics").

### Evaluation Framework
A structured eval suite with:
- Known question → expected SQL pairs
- Accuracy metrics (does the SQL return correct results?)
- Latency tracking per pipeline stage
- LLM cost tracking

### Observability
Structured logging, distributed tracing, and metrics for:
- SQL generation success rate
- Snowflake query latency distribution
- Guardrail classification accuracy
- Session usage patterns

### Geographic Resolver
A lookup layer that maps informal place names ("Bay Area", "the Midwest") to specific FIPS codes or state abbreviations for more accurate queries.

### Data Visualizations
Simple charts (bar, line, map) rendered from query results. The tabular data in the current response format works but isn't as immediately readable as a chart for comparative questions.

## Edge Cases

### Handled
- **Off-topic queries**: Two-tier guardrail (keyword + LLM) with clear rejection messages
- **SQL injection**: Regex-based safety validator blocks INSERT/UPDATE/DELETE/DROP and multi-statement queries
- **Empty results**: Clear explanation with suggestions for broadening the search
- **Connection failures**: Graceful degradation with user-friendly error messages
- **Query timeouts**: Snowflake statement timeout configured (default 50s)
- **Ambiguous queries**: LLM can respond with CANNOT_ANSWER when it determines the question can't be mapped to available data
- **Multi-turn conversations**: Session history is passed to both SQL generation and response generation

### Identified But Not Fully Addressed
- **Very broad queries**: "Tell me everything about the US" would generate expensive queries. LIMIT 100 provides a safety net, but query cost estimation would be better.
- **Conflicting data interpretations**: The same question could be answered at CBG, county, or state level — the system currently lets the LLM decide, but user disambiguation would be more reliable.
- **Rate limiting**: No per-user or per-session rate limiting implemented. In production, this would be essential to prevent abuse and manage LLM costs.
- **Concurrent sessions at scale**: In-memory sessions don't survive restarts and don't scale horizontally. Production would need Redis or database-backed sessions.
- **LLM provider outages**: The system catches errors but doesn't implement retry logic or fallback between providers. A circuit-breaker pattern would be more robust.

## Testing Strategy

### What's Tested
- **Guardrails** (18 tests): SQL safety validation covers all dangerous operations, multi-statement detection, and edge cases. Topic classification tests cover keywords, patterns, greetings, and LLM fallback including error handling.
- **Sessions** (12 tests): Session lifecycle (create, retrieve, expire), history management (trimming, LLM-format serialization), and store-level operations (cleanup, concurrency).
- **Schema Cache** (8 tests): Context generation format, column truncation, fallback behavior on errors, refresh mechanics.
- **Chat API** (9 tests): End-to-end SSE flow, off-topic rejection (both keyword and LLM paths), error handling (connection errors, empty results, unsafe SQL), session continuity, and CANNOT_ANSWER handling.

### Testing Philosophy
I focused tests on the **business logic boundaries** — the guardrails, session management, and API integration flow — rather than trying to achieve 100% code coverage. These are the areas where bugs would have the most impact on user experience and security.

The test suite uses mock objects for external dependencies (Snowflake, LLM providers) to keep tests fast, deterministic, and runnable without credentials. The mocks are designed to be simple and predictable rather than comprehensive simulations.

### What I'd Add
- **SQL generation quality tests**: A set of (question, expected SQL pattern) pairs to catch regressions in prompt engineering
- **Frontend tests**: React Testing Library tests for the chat UI components
- **Load testing**: k6 or Locust scripts to verify the 60-second response time requirement under concurrent users
- **LLM response quality tests**: Automated checks that the response actually references the query results and doesn't hallucinate data
