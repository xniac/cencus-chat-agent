# US Census Chat Agent

An interactive, chat-based agent that answers natural language questions about US population data, powered by the **SafeGraph US Open Census** dataset on Snowflake Marketplace.

## Live Demo

> **URL:** _(to be added after deployment)_

## Architecture

```
┌──────────────┐     SSE      ┌─────────────────────────────────┐
│  React SPA   │◄────────────►│         FastAPI Backend          │
│  (Vite/TS)   │   POST/SSE   │                                 │
└──────────────┘              │  ┌──────────┐  ┌─────────────┐  │
                              │  │Guardrails│  │ Session Mgmt │  │
                              │  └────┬─────┘  └──────┬──────┘  │
                              │       │               │         │
                              │  ┌────▼─────────────────────┐   │
                              │  │    LLM (OpenAI/Anthropic) │   │
                              │  │  ┌──────────┐ ┌────────┐ │   │
                              │  │  │SQL Gen   │ │Response│ │   │
                              │  │  └──────────┘ │  Gen   │ │   │
                              │  │               └────────┘ │   │
                              │  └──────────┬───────────────┘   │
                              │             │                   │
                              │  ┌──────────▼──────────┐        │
                              │  │   Snowflake Client   │        │
                              │  │   (Schema Cache)     │        │
                              │  └──────────┬──────────┘        │
                              └─────────────┼───────────────────┘
                                            │
                              ┌─────────────▼──────────┐
                              │  Snowflake Marketplace  │
                              │  US Open Census Data    │
                              └────────────────────────┘
```

## How It Works

1. **User sends a question** via the React chat UI
2. **Topic guardrail** checks relevance (keyword matching → LLM classification if ambiguous)
3. **SQL generation** — LLM converts the natural language question to a Snowflake SQL query using cached schema context + conversation history
4. **SQL safety validation** — regex-based validator ensures only SELECT/WITH queries pass through
5. **Snowflake execution** — query runs against the US Open Census dataset
6. **Response generation** — LLM streams a natural language explanation of the results
7. **Session management** — conversation history is preserved for multi-turn interactions

## Design Decisions

| Decision | Rationale |
|---|---|
| **Text-to-SQL** approach | Allows the agent to answer any question the data can support, rather than pre-defining a limited set of queries |
| **Dynamic schema discovery** | Schema is fetched from Snowflake at startup and cached (1hr TTL) — no hardcoded column names that can drift |
| **Two-phase guardrails** | Fast keyword check handles obvious cases; LLM classification only fires for ambiguous inputs (saves latency & cost) |
| **SSE streaming** | Users see tokens as they arrive — better UX than waiting for full response. Simpler than WebSocket for request-response. |
| **In-memory sessions** | Acceptable for demo scale; avoids external dependency (Redis). Sessions have TTL-based expiration. |
| **Pluggable LLM provider** | Supports both OpenAI and Anthropic via a common Protocol interface — easy to switch or add providers |

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Pydantic, sse-starlette
- **Frontend:** React 18, TypeScript, Vite
- **Database:** Snowflake (via snowflake-connector-python)
- **LLM:** OpenAI GPT-4o or Anthropic Claude 3.5 Sonnet
- **Deployment:** Docker, Render

## Project Structure

```
.
├── backend/
│   ├── app/
│   │   ├── config.py              # Pydantic Settings
│   │   ├── main.py                # FastAPI app + lifespan
│   │   ├── models/
│   │   │   └── schemas.py         # Request/response models
│   │   ├── routers/
│   │   │   └── chat.py            # /api/chat, /api/health
│   │   └── services/
│   │       ├── guardrails.py      # Topic + SQL safety checks
│   │       ├── llm.py             # OpenAI/Anthropic providers
│   │       ├── response_generator.py
│   │       ├── schema_cache.py    # Snowflake schema caching
│   │       ├── session.py         # In-memory session store
│   │       ├── snowflake_client.py
│   │       └── sql_generator.py
│   ├── tests/
│   │   ├── conftest.py            # Shared mocks & fixtures
│   │   ├── test_chat_api.py       # Integration tests
│   │   ├── test_guardrails.py     # Guardrail unit tests
│   │   ├── test_schema_cache.py   # Schema cache tests
│   │   └── test_session.py        # Session management tests
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── App.tsx                # Main application
│   │   ├── App.css                # Styles (dark theme)
│   │   ├── types.ts               # TypeScript types
│   │   ├── hooks/
│   │   │   └── useChat.ts         # Chat state + SSE handling
│   │   └── components/
│   │       ├── ChatWindow.tsx     # Message list + auto-scroll
│   │       ├── InputBar.tsx       # Auto-resize textarea
│   │       └── MessageBubble.tsx  # Message rendering + markdown
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── Dockerfile                     # Multi-stage build
├── docker-compose.yml
├── render.yaml                    # Render deployment config
└── README.md
```

## Local Development

### Prerequisites
- Python 3.12+
- Node.js 20+
- Snowflake account with US Open Census Data (Marketplace)
- OpenAI or Anthropic API key

### Setup

```bash
# Clone
git clone <repo-url>
cd cencus-chat-agent

# Environment
cp .env.example .env
# Edit .env with your credentials

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install

# Run (two terminals)
# Terminal 1 - Backend:
cd backend && uvicorn app.main:app --reload --port 8000
# Terminal 2 - Frontend:
cd frontend && npm run dev
```

### Running Tests

```bash
cd backend
pytest -v
pytest --cov=app --cov-report=term-missing
```

## API Endpoints

### `POST /api/chat`

Send a chat message and receive a streaming SSE response.

**Request:**
```json
{
  "message": "What is the population of California?",
  "session_id": "optional-uuid"
}
```

**SSE Events:**

| Event | Data | Description |
|---|---|---|
| `thinking` | Status text | Progress update |
| `sql` | SQL query string | Generated SQL |
| `data` | Row count (int) | Number of result rows |
| `answer_token` | Text token | Streamed response chunk |
| `answer` | Full text | Complete answer (non-streaming fallback) |
| `session_id` | UUID string | Session identifier |
| `error` | Error message | Error description |
| `done` | Empty | Stream complete |

### `GET /api/health`

```json
{
  "status": "healthy",
  "snowflake_connected": true,
  "llm_provider": "openai"
}
```

## Example Questions

- "What are the top 10 most populated states?"
- "What is the median household income by state?"
- "Which counties have the highest percentage of college graduates?"
- "What is the racial diversity breakdown across the US?"
- "How does poverty rate vary between urban and rural areas?"
- "What percentage of households have no vehicle?"

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SNOWFLAKE_ACCOUNT` | Yes | — | Snowflake account identifier |
| `SNOWFLAKE_USER` | Yes | — | Snowflake username |
| `SNOWFLAKE_PASSWORD` | Yes | — | Snowflake password |
| `SNOWFLAKE_DATABASE` | No | `US_OPEN_CENSUS_DATA` | Database name |
| `SNOWFLAKE_SCHEMA` | No | `PUBLIC` | Schema name |
| `SNOWFLAKE_WAREHOUSE` | No | `COMPUTE_WH` | Warehouse name |
| `SNOWFLAKE_ROLE` | No | `ACCOUNTADMIN` | Role name |
| `LLM_PROVIDER` | No | `openai` | `openai` or `anthropic` |
| `OPENAI_API_KEY` | If using OpenAI | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o` | OpenAI model name |
| `ANTHROPIC_API_KEY` | If using Anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-20250514` | Anthropic model name |
| `SESSION_TTL_MINUTES` | No | `30` | Session timeout |
| `DEBUG` | No | `false` | Enable debug logging |
