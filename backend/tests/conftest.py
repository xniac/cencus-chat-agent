from typing import AsyncIterator

import pytest
from sse_starlette.sse import AppStatus


@pytest.fixture(autouse=True)
def reset_sse_app_status():
    """Reset sse-starlette's AppStatus between tests to avoid event loop conflicts."""
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None


class MockLLMProvider:
    def __init__(self, generate_response: str = "on_topic", stream_tokens: list[str] | None = None):
        self.generate_response = generate_response
        self.stream_tokens = stream_tokens or ["Hello", " from", " Census", " Agent"]
        self.calls: list[dict] = []

    async def generate(self, system: str, messages: list[dict]) -> str:
        self.calls.append({"method": "generate", "system": system, "messages": messages})
        return self.generate_response

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        self.calls.append({"method": "stream", "system": system, "messages": messages})
        for token in self.stream_tokens:
            yield token


class MockSnowflakeClient:
    def __init__(
        self,
        results: list[dict] | None = None,
        tables: list[dict] | None = None,
        columns: list[dict] | None = None,
        side_effect: Exception | None = None,
    ):
        self.results = results if results is not None else [{"STATE": "California", "POPULATION": 39538223}]
        self.tables = tables if tables is not None else [
            {"TABLE_NAME": "TEST_TABLE", "ROW_COUNT": 1000, "COMMENT": "Test table"}
        ]
        self.columns = columns if columns is not None else [
            {
                "TABLE_NAME": "TEST_TABLE",
                "COLUMN_NAME": "STATE",
                "DATA_TYPE": "TEXT",
                "IS_NULLABLE": "NO",
                "COMMENT": "State name",
            }
        ]
        self.side_effect = side_effect
        self.queries: list[str] = []
        self._connection_params = {"account": "test", "user": "test"}

    def execute_query(self, sql: str, timeout: int | None = None) -> list[dict]:
        self.queries.append(sql)
        if self.side_effect:
            raise self.side_effect
        return self.results

    def test_connection(self) -> bool:
        return True

    def get_tables(self) -> list[dict]:
        return self.tables

    def get_all_columns(self) -> list[dict]:
        return self.columns

    def get_columns(self, table_name: str) -> list[dict]:
        return [c for c in self.columns if c["TABLE_NAME"] == table_name]


class MockSchemaCache:
    def __init__(self):
        self._schema_context = (
            "=== US OPEN CENSUS DATA — SNOWFLAKE SCHEMA ===\n"
            "Table: TEST_TABLE (rows: 1000)\n"
            "  Columns: STATE (TEXT), POPULATION (NUMBER)"
        )

    @property
    def schema_context(self) -> str:
        return self._schema_context

    def refresh(self) -> None:
        pass


@pytest.fixture
def mock_llm():
    return MockLLMProvider()


@pytest.fixture
def mock_snowflake():
    return MockSnowflakeClient()


@pytest.fixture
def mock_schema_cache():
    return MockSchemaCache()


@pytest.fixture
def session_store():
    from backend.app.services.session import SessionStore
    return SessionStore()


