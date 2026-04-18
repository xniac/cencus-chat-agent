import pytest

from backend.app.services.guardrails import validate_sql_safety, quick_topic_check, llm_topic_check
from backend.tests.conftest import MockLLMProvider


# === SQL Safety Tests ===

class TestValidateSqlSafety:
    def test_allows_select(self):
        ok, _ = validate_sql_safety("SELECT * FROM table1")
        assert ok

    def test_allows_with_cte(self):
        ok, _ = validate_sql_safety("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert ok

    def test_allows_trailing_semicolon(self):
        ok, _ = validate_sql_safety("SELECT * FROM table1;")
        assert ok

    def test_blocks_insert(self):
        ok, msg = validate_sql_safety("INSERT INTO table1 VALUES (1)")
        assert not ok

    def test_blocks_update(self):
        ok, msg = validate_sql_safety("UPDATE table1 SET col = 1")
        assert not ok

    def test_blocks_delete(self):
        ok, msg = validate_sql_safety("DELETE FROM table1")
        assert not ok

    def test_blocks_drop(self):
        ok, msg = validate_sql_safety("DROP TABLE table1")
        assert not ok

    def test_blocks_alter(self):
        ok, msg = validate_sql_safety("ALTER TABLE table1 ADD col INT")
        assert not ok

    def test_blocks_create(self):
        ok, msg = validate_sql_safety("CREATE TABLE table1 (id INT)")
        assert not ok

    def test_blocks_truncate(self):
        ok, msg = validate_sql_safety("TRUNCATE TABLE table1")
        assert not ok

    def test_blocks_grant(self):
        ok, msg = validate_sql_safety("GRANT SELECT ON table1 TO user1")
        assert not ok

    def test_blocks_execute(self):
        ok, msg = validate_sql_safety("EXECUTE sp_name")
        assert not ok

    def test_blocks_merge(self):
        ok, msg = validate_sql_safety("MERGE INTO t1 USING t2 ON t1.id = t2.id")
        assert not ok

    def test_blocks_multi_statement(self):
        ok, msg = validate_sql_safety("SELECT 1; SELECT 2")
        assert not ok

    def test_blocks_empty(self):
        ok, msg = validate_sql_safety("")
        assert not ok

    def test_blocks_non_select_start(self):
        ok, msg = validate_sql_safety("SHOW TABLES")
        assert not ok

    def test_allows_wrapped_union(self):
        sql = "(SELECT a FROM t1 LIMIT 1) UNION ALL (SELECT b FROM t2 LIMIT 1)"
        ok, msg = validate_sql_safety(sql)
        assert ok

    def test_allows_wrapped_with_cte_union(self):
        sql = "(WITH c AS (SELECT 1) SELECT * FROM c) UNION (SELECT 2)"
        ok, msg = validate_sql_safety(sql)
        assert ok

    def test_allows_get_as_string_literal(self):
        # Previously blocked by \bGET\b; legitimate queries using GET as data must pass.
        sql = "SELECT * FROM requests WHERE method = 'GET'"
        ok, msg = validate_sql_safety(sql)
        assert ok

    def test_allows_put_as_string_literal(self):
        sql = "SELECT * FROM requests WHERE method = 'PUT'"
        ok, msg = validate_sql_safety(sql)
        assert ok

    def test_blocks_snowflake_get_command(self):
        # Actual Snowflake GET command starts with GET at the statement level
        # — still blocked because the query doesn't start with SELECT or WITH.
        ok, msg = validate_sql_safety("GET @stage/file.csv")
        assert not ok

    def test_blocks_snowflake_put_command(self):
        ok, msg = validate_sql_safety("PUT file://local.csv @stage")
        assert not ok


# === Quick Topic Check Tests ===

class TestQuickTopicCheck:
    def test_census_keywords_on_topic(self):
        result = quick_topic_check("What is the population and income in California?")
        assert result == "on_topic"

    def test_off_topic_recipe(self):
        result = quick_topic_check("How do I cook a recipe for pasta?")
        assert result == "off_topic"

    def test_off_topic_weather(self):
        result = quick_topic_check("What is the weather forecast for tomorrow?")
        assert result == "off_topic"

    def test_off_topic_sports(self):
        result = quick_topic_check("Who won the football game last night?")
        assert result == "off_topic"

    def test_greeting_on_topic(self):
        result = quick_topic_check("hello")
        assert result == "on_topic"

    def test_ambiguous_returns_none(self):
        result = quick_topic_check("Tell me about trends in the United States")
        assert result is None


# === LLM Topic Check Tests ===

class TestLLMTopicCheck:
    @pytest.mark.asyncio
    async def test_on_topic(self):
        llm = MockLLMProvider(generate_response="on_topic")
        result = await llm_topic_check("What is the population?", llm)
        assert result is True

    @pytest.mark.asyncio
    async def test_off_topic(self):
        llm = MockLLMProvider(generate_response="off_topic")
        result = await llm_topic_check("How to bake a cake?", llm)
        assert result is False

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self):
        llm = MockLLMProvider()
        # Make generate raise
        async def failing_generate(system, messages):
            raise RuntimeError("LLM is down")
        llm.generate = failing_generate
        result = await llm_topic_check("Some question", llm)
        assert result is True  # Fail open
