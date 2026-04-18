import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tests.conftest import MockLLMProvider, MockSnowflakeClient, MockSchemaCache
from backend.app.services.session import SessionStore
from backend.app.services.snowflake_client import SnowflakeConnectionError
from backend.app.routers.chat import router as chat_router


def _create_test_app(llm=None, snowflake=None, schema_cache=None, session_store=None):
    """Create a fresh FastAPI app without lifespan for testing."""
    test_app = FastAPI()
    test_app.include_router(chat_router)
    test_app.state.snowflake = snowflake or MockSnowflakeClient()
    test_app.state.llm = llm or MockLLMProvider(
        generate_response="SELECT STATE, POPULATION FROM US_OPEN_CENSUS_DATA.PUBLIC.TEST_TABLE LIMIT 10",
        stream_tokens=["California", " has", " 39M", " people"],
    )
    test_app.state.schema_cache = schema_cache or MockSchemaCache()
    test_app.state.session_store = session_store or SessionStore()
    test_app.state.database = "US_OPEN_CENSUS_DATA"
    test_app.state.schema = "PUBLIC"
    test_app.state.llm_provider_name = "openai"
    return test_app


def _parse_sse_events(response_text: str) -> list[dict]:
    events = []
    # Normalize line endings — sse-starlette uses \r\n
    normalized = response_text.replace("\r\n", "\n")
    blocks = normalized.split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        event_type = ""
        data_parts = []
        for line in block.strip().split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_parts.append(line[5:].strip())
        if event_type:
            events.append({"event": event_type, "data": "\n".join(data_parts)})
    return events


class TestChatAPI:
    def test_successful_chat_flow(self):
        app = _create_test_app()
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population by state?"},
            )
            assert response.status_code == 200
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "thinking" in event_types
            assert "sql" in event_types
            assert "data" in event_types
            assert "answer_token" in event_types
            assert "session_id" in event_types
            assert "done" in event_types

    def test_off_topic_rejection_keyword(self):
        app = _create_test_app()
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "How do I cook a recipe for pasta?"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "error" in event_types
            error_event = next(e for e in events if e["event"] == "error")
            assert "census" in error_event["data"].lower()

    def test_off_topic_via_llm(self):
        llm = MockLLMProvider(generate_response="off_topic")
        app = _create_test_app(llm=llm)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "Tell me about the latest trends in technology"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "error" in event_types

    def test_snowflake_connection_error(self):
        snowflake = MockSnowflakeClient(
            side_effect=SnowflakeConnectionError("Connection failed")
        )
        llm = MockLLMProvider(
            generate_response="SELECT * FROM US_OPEN_CENSUS_DATA.PUBLIC.TEST_TABLE LIMIT 10"
        )
        app = _create_test_app(llm=llm, snowflake=snowflake)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population of each state?"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "error" in event_types
            error_event = next(e for e in events if e["event"] == "error")
            assert "trouble connecting" in error_event["data"].lower()

    def test_empty_results(self):
        snowflake = MockSnowflakeClient(results=[])
        llm = MockLLMProvider(
            generate_response="SELECT * FROM US_OPEN_CENSUS_DATA.PUBLIC.TEST_TABLE WHERE STATE = 'Narnia'"
        )
        app = _create_test_app(llm=llm, snowflake=snowflake)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population of Narnia state?"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "answer" in event_types
            answer_event = next(e for e in events if e["event"] == "answer")
            assert "no results" in answer_event["data"].lower()

    def test_sql_safety_rejection(self):
        llm = MockLLMProvider(generate_response="DROP TABLE users")
        app = _create_test_app(llm=llm)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population by state?"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "error" in event_types
            error_event = next(e for e in events if e["event"] == "error")
            assert "safety" in error_event["data"].lower()

    def test_session_id_returned(self):
        app = _create_test_app()
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population by state?"},
            )
            events = _parse_sse_events(response.text)
            session_events = [e for e in events if e["event"] == "session_id"]
            assert len(session_events) == 1
            assert session_events[0]["data"]  # Not empty

    def test_session_continuity(self):
        session_store = SessionStore()
        app = _create_test_app(session_store=session_store)
        with TestClient(app) as client:
            # First request
            response1 = client.post(
                "/api/chat",
                json={"message": "What is the population by state?"},
            )
            events1 = _parse_sse_events(response1.text)
            session_id = next(e for e in events1 if e["event"] == "session_id")["data"]

            # Second request with same session (uses census keywords to pass guardrail)
            response2 = client.post(
                "/api/chat",
                json={"message": "Show me more population details by county", "session_id": session_id},
            )
            events2 = _parse_sse_events(response2.text)
            session_id2 = next(e for e in events2 if e["event"] == "session_id")["data"]
            assert session_id == session_id2

    def test_cannot_answer_handling(self):
        llm = MockLLMProvider(
            generate_response="CANNOT_ANSWER: The dataset does not contain weather information"
        )
        app = _create_test_app(llm=llm)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "What is the population and income in California?"},
            )
            events = _parse_sse_events(response.text)
            event_types = [e["event"] for e in events]
            assert "answer" in event_types
            answer_event = next(e for e in events if e["event"] == "answer")
            assert "weather" in answer_event["data"].lower()
