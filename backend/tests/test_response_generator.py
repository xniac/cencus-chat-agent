import json

import pytest

from backend.app.services.response_generator import (
    _format_results,
    generate_response,
    generate_response_stream,
)
from backend.tests.conftest import MockLLMProvider


class TestFormatResults:
    def test_empty_results(self):
        assert "No results" in _format_results([])

    def test_small_result_set(self):
        results = [{"STATE": "CA", "POP": 39_000_000}, {"STATE": "TX", "POP": 29_000_000}]
        formatted = _format_results(results)
        # Should be valid JSON containing both rows
        parsed = json.loads(formatted)
        assert len(parsed) == 2
        assert parsed[0]["STATE"] == "CA"

    def test_truncation_over_20(self):
        results = [{"i": n} for n in range(25)]
        formatted = _format_results(results)
        assert "25 rows" in formatted or "5 more rows" in formatted
        # First 20 rows should be included verbatim
        assert '"i": 0' in formatted
        assert '"i": 19' in formatted

    def test_handles_non_json_serializable(self):
        # datetime would not be JSON serializable by default; _format_results uses default=str
        from datetime import datetime
        results = [{"ts": datetime(2024, 1, 1, 12, 0, 0)}]
        # Should not raise
        formatted = _format_results(results)
        assert "2024" in formatted


class TestGenerateResponseStream:
    @pytest.mark.asyncio
    async def test_streams_tokens(self):
        llm = MockLLMProvider(stream_tokens=["Hello", ", ", "world", "!"])
        tokens = []
        async for t in generate_response_stream(
            question="test",
            sql="SELECT 1",
            results=[{"x": 1}],
            history=[],
            llm_provider=llm,
        ):
            tokens.append(t)
        assert tokens == ["Hello", ", ", "world", "!"]

    @pytest.mark.asyncio
    async def test_includes_question_sql_and_results_in_prompt(self):
        llm = MockLLMProvider(stream_tokens=["ok"])
        async for _ in generate_response_stream(
            question="What is pop?",
            sql="SELECT SUM(POP) FROM T",
            results=[{"POP": 1000}],
            history=[],
            llm_provider=llm,
        ):
            pass

        assert len(llm.calls) == 1
        messages = llm.calls[0]["messages"]
        user_content = messages[-1]["content"]
        assert "What is pop?" in user_content
        assert "SELECT SUM(POP) FROM T" in user_content
        assert "1000" in user_content  # From results JSON

    @pytest.mark.asyncio
    async def test_includes_history(self):
        llm = MockLLMProvider(stream_tokens=["ok"])
        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        async for _ in generate_response_stream(
            question="Follow-up",
            sql="SELECT 1",
            results=[{"x": 1}],
            history=history,
            llm_provider=llm,
        ):
            pass

        messages = llm.calls[0]["messages"]
        contents = [m["content"] for m in messages]
        assert "Previous question" in contents
        assert "Previous answer" in contents


class TestGenerateResponse:
    @pytest.mark.asyncio
    async def test_returns_full_response(self):
        llm = MockLLMProvider(generate_response="California has the highest population.")
        response = await generate_response(
            question="Which state has the most people?",
            sql="SELECT STATE FROM T ORDER BY POP DESC LIMIT 1",
            results=[{"STATE": "California"}],
            history=[],
            llm_provider=llm,
        )
        assert response == "California has the highest population."
