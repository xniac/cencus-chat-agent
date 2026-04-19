import pytest

from backend.app.services.sql_generator import (
    _clean_sql_response,
    _wrap_union_arms,
    generate_sql,
    fix_sql,
    SQLGenResult,
)
from backend.tests.conftest import MockLLMProvider


class TestWrapUnionArms:
    def test_wraps_unwrapped_union_all(self):
        sql = "SELECT a FROM t LIMIT 1\n\nUNION ALL\n\nSELECT b FROM t LIMIT 1"
        result = _wrap_union_arms(sql)
        assert "(SELECT a FROM t LIMIT 1)" in result
        assert "(SELECT b FROM t LIMIT 1)" in result
        assert "UNION ALL" in result

    def test_leaves_already_wrapped_alone(self):
        sql = "(SELECT a FROM t LIMIT 1) UNION ALL (SELECT b FROM t LIMIT 1)"
        result = _wrap_union_arms(sql)
        # Shouldn't double-wrap
        assert result.count("((") == 0
        assert result.count("))") == 0

    def test_no_change_without_union(self):
        sql = "SELECT * FROM t WHERE x = 1"
        assert _wrap_union_arms(sql) == sql

    def test_handles_union_intersect_except(self):
        for op in ["UNION", "INTERSECT", "EXCEPT"]:
            sql = f"SELECT a FROM t1 {op} SELECT b FROM t2"
            result = _wrap_union_arms(sql)
            assert "(SELECT a FROM t1)" in result
            assert "(SELECT b FROM t2)" in result
            assert op in result

    def test_multiple_unions(self):
        sql = "SELECT a UNION ALL SELECT b UNION ALL SELECT c"
        result = _wrap_union_arms(sql)
        assert result.count("(SELECT") == 3


class TestCleanSqlResponse:
    def test_strips_markdown_fences(self):
        sql = "```sql\nSELECT * FROM t\n```"
        assert _clean_sql_response(sql) == "SELECT * FROM t"

    def test_strips_trailing_semicolon(self):
        assert _clean_sql_response("SELECT 1;") == "SELECT 1"

    def test_wraps_union_in_response(self):
        sql = "SELECT a LIMIT 1\nUNION ALL\nSELECT b LIMIT 1"
        result = _clean_sql_response(sql)
        assert "(SELECT a LIMIT 1)" in result
        assert "(SELECT b LIMIT 1)" in result


SCHEMA_CTX = "TABLE TEST_TABLE columns STATE POPULATION"


class TestGenerateSql:
    @pytest.mark.asyncio
    async def test_successful_generation(self):
        llm = MockLLMProvider(
            generate_response="SELECT STATE, SUM(POPULATION) FROM US_OPEN_CENSUS_DATA.PUBLIC.TEST_TABLE GROUP BY STATE LIMIT 10"
        )
        result, sql, reason = await generate_sql(
            question="Population by state",
            schema_context=SCHEMA_CTX,
            history=[],
            llm_provider=llm,
            database="US_OPEN_CENSUS_DATA",
            schema="PUBLIC",
        )
        assert result == SQLGenResult.OK
        assert sql is not None and sql.upper().startswith("SELECT")
        assert reason is None

    @pytest.mark.asyncio
    async def test_cannot_answer(self):
        llm = MockLLMProvider(generate_response="CANNOT_ANSWER: Data not available")
        result, sql, reason = await generate_sql(
            question="What is the weather?",
            schema_context=SCHEMA_CTX,
            history=[],
            llm_provider=llm,
            database="US_OPEN_CENSUS_DATA",
            schema="PUBLIC",
        )
        assert result == SQLGenResult.CANNOT_ANSWER
        assert sql is None
        assert reason is not None and "Data not available" in reason

    @pytest.mark.asyncio
    async def test_api_error(self):
        llm = MockLLMProvider()

        async def failing(system, messages):
            raise RuntimeError("OpenAI is down")

        llm.generate = failing
        result, sql, reason = await generate_sql(
            question="Test",
            schema_context=SCHEMA_CTX,
            history=[],
            llm_provider=llm,
            database="US_OPEN_CENSUS_DATA",
            schema="PUBLIC",
        )
        assert result == SQLGenResult.API_ERROR
        assert sql is None
        assert reason is not None and "OpenAI is down" in reason

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        llm = MockLLMProvider(
            generate_response="```sql\nSELECT 1 FROM T\n```"
        )
        result, sql, _ = await generate_sql(
            question="Test",
            schema_context=SCHEMA_CTX,
            history=[],
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        assert result == SQLGenResult.OK
        assert sql == "SELECT 1 FROM T"

    @pytest.mark.asyncio
    async def test_applies_union_wrapping(self):
        """Verify that generate_sql output has UNION arms wrapped."""
        llm = MockLLMProvider(
            generate_response="SELECT a FROM t LIMIT 1\nUNION ALL\nSELECT b FROM t LIMIT 1"
        )
        result, sql, _ = await generate_sql(
            question="top and bottom",
            schema_context=SCHEMA_CTX,
            history=[],
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        assert result == SQLGenResult.OK
        assert sql is not None
        assert "(SELECT a FROM t LIMIT 1)" in sql
        assert "(SELECT b FROM t LIMIT 1)" in sql

    @pytest.mark.asyncio
    async def test_history_passed_to_llm(self):
        llm = MockLLMProvider(generate_response="SELECT 1")
        history = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        await generate_sql(
            question="Follow-up",
            schema_context=SCHEMA_CTX,
            history=history,
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        # The mock records messages; last 6 history + current should be sent
        assert len(llm.calls) == 1
        messages_sent = llm.calls[0]["messages"]
        # History entries (excluding last message in real router) + current user question
        contents = [m["content"] for m in messages_sent]
        assert "Previous question" in contents
        assert "Follow-up" in contents


class TestFixSql:
    @pytest.mark.asyncio
    async def test_successful_fix(self):
        llm = MockLLMProvider(generate_response="SELECT \"CORRECT_COL\" FROM T")
        result, sql, reason = await fix_sql(
            question="test",
            failed_sql="SELECT WRONG_COL FROM T",
            error_message="invalid identifier WRONG_COL",
            schema_context=SCHEMA_CTX,
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        assert result == SQLGenResult.OK
        assert sql is not None and "CORRECT_COL" in sql

    @pytest.mark.asyncio
    async def test_fix_cannot_answer(self):
        llm = MockLLMProvider(generate_response="CANNOT_ANSWER: No such column exists")
        result, sql, reason = await fix_sql(
            question="test",
            failed_sql="SELECT BOGUS FROM T",
            error_message="invalid identifier",
            schema_context=SCHEMA_CTX,
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        assert result == SQLGenResult.CANNOT_ANSWER
        assert sql is None

    @pytest.mark.asyncio
    async def test_fix_api_error(self):
        llm = MockLLMProvider()

        async def failing(system, messages):
            raise RuntimeError("LLM timeout")

        llm.generate = failing
        result, sql, reason = await fix_sql(
            question="test",
            failed_sql="SELECT X FROM T",
            error_message="err",
            schema_context=SCHEMA_CTX,
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        assert result == SQLGenResult.API_ERROR
        assert sql is None

    @pytest.mark.asyncio
    async def test_fix_passes_error_in_prompt(self):
        llm = MockLLMProvider(generate_response="SELECT 1 FROM T")
        await fix_sql(
            question="original Q",
            failed_sql="BROKEN SQL",
            error_message="some snowflake error",
            schema_context=SCHEMA_CTX,
            llm_provider=llm,
            database="DB",
            schema="S",
        )
        # The LLM should receive the error context (it's in the LAST user message)
        assert len(llm.calls) == 1
        user_msg = llm.calls[0]["messages"][-1]["content"]
        assert "BROKEN SQL" in user_msg
        assert "some snowflake error" in user_msg
        assert "original Q" in user_msg

    @pytest.mark.asyncio
    async def test_fix_includes_history(self):
        """fix_sql should pass conversation history so follow-ups retain context."""
        llm = MockLLMProvider(generate_response="SELECT 1 FROM T")
        history = [
            {"role": "user", "content": "Top 5 populated states"},
            {"role": "assistant", "content": "CA TX FL..."},
        ]
        await fix_sql(
            question="What about the bottom 5?",
            failed_sql="BROKEN SQL",
            error_message="err",
            schema_context=SCHEMA_CTX,
            llm_provider=llm,
            database="DB",
            schema="S",
            history=history,
        )
        messages = llm.calls[0]["messages"]
        contents = [m["content"] for m in messages]
        assert any("Top 5 populated states" in c for c in contents)
