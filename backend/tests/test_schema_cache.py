import pytest

from backend.app.services.schema_cache import SchemaCache
from backend.app.services.snowflake_client import SnowflakeConnectionError
from backend.tests.conftest import MockSnowflakeClient


class TestSchemaCache:

    def test_context_includes_table_info(self):
        client = MockSnowflakeClient()
        cache = SchemaCache(client)
        cache.refresh()
        ctx = cache.schema_context
        assert "US OPEN CENSUS DATA" in ctx
        assert "TEST_TABLE" in ctx
        assert "STATE" in ctx
        assert "1000" in ctx  # row count

    def test_column_truncation_for_large_tables(self):
        many_cols = [
            {
                "TABLE_NAME": "BIG_TABLE",
                "COLUMN_NAME": f"COL_{i}",
                "DATA_TYPE": "TEXT",
                "IS_NULLABLE": "YES",
                "COMMENT": None,
            }
            for i in range(120)
        ]
        tables = [{"TABLE_NAME": "BIG_TABLE", "ROW_COUNT": 5000, "COMMENT": None}]
        client = MockSnowflakeClient(tables=tables, columns=many_cols)
        cache = SchemaCache(client)
        cache.refresh()
        ctx = cache.schema_context
        # With 120 cols and default 15/table, we expect "+105 more" (or similar note)
        assert "more" in ctx
        assert "cols=120" in ctx

    def test_fallback_on_connection_error(self):
        client = MockSnowflakeClient(side_effect=SnowflakeConnectionError("down"))
        # Override get_tables to raise
        def get_tables():
            raise SnowflakeConnectionError("down")
        client.get_tables = get_tables
        cache = SchemaCache(client)
        cache.refresh()
        assert "OFFLINE MODE" in cache.schema_context

    def test_fallback_on_generic_error(self):
        client = MockSnowflakeClient()
        def get_tables():
            raise RuntimeError("unexpected")
        client.get_tables = get_tables
        cache = SchemaCache(client)
        cache.refresh()
        assert "OFFLINE MODE" in cache.schema_context

    def test_refresh_updates_context(self):
        client = MockSnowflakeClient()
        cache = SchemaCache(client)
        cache.refresh()
        ctx1 = cache.schema_context

        client.tables = [{"TABLE_NAME": "NEW_TABLE", "ROW_COUNT": 500, "COMMENT": None}]
        client.columns = [
            {
                "TABLE_NAME": "NEW_TABLE",
                "COLUMN_NAME": "ID",
                "DATA_TYPE": "NUMBER",
                "IS_NULLABLE": "NO",
                "COMMENT": None,
            }
        ]
        cache.refresh()
        ctx2 = cache.schema_context
        assert "NEW_TABLE" in ctx2
        assert ctx1 != ctx2

    def test_property_triggers_refresh(self):
        client = MockSnowflakeClient()
        cache = SchemaCache(client)
        # Access property — should trigger refresh
        ctx = cache.schema_context
        assert "TEST_TABLE" in ctx

    def test_short_keywords_kept(self):
        """Keywords like 'age', 'sex', 'pop' (3 chars) must be extracted
        — they are critical for ACS schema filtering."""
        keywords = SchemaCache._extract_keywords("What is the age distribution?")
        assert "age" in keywords

    def test_follow_up_stop_words_filtered(self):
        """Common follow-up words don't pollute the keyword set."""
        keywords = SchemaCache._extract_keywords("Now break that down by age")
        # 'now', 'break', 'down', 'that' should all be stop words
        assert "now" not in keywords
        assert "break" not in keywords
        assert "down" not in keywords
        assert "that" not in keywords
        # But 'age' (3 chars) should be kept
        assert "age" in keywords

    def test_synonym_expansion_racial(self):
        """'racial' should expand to race-related terms."""
        keywords = SchemaCache._extract_keywords("What is the racial diversity breakdown?")
        assert "racial" in keywords
        assert "race" in keywords
        assert "ethnic" in keywords

    def test_synonym_expansion_housing(self):
        """'housing' should expand to house/household."""
        keywords = SchemaCache._extract_keywords("Tell me about housing costs")
        assert "housing" in keywords
        assert "house" in keywords
        assert "household" in keywords

    def test_synonym_expansion_job(self):
        """'jobs' should expand to employment terms."""
        keywords = SchemaCache._extract_keywords("How many jobs are in California?")
        assert "jobs" in keywords
        assert "employ" in keywords
        assert "labor" in keywords

    def test_synonym_expansion_kids(self):
        """'kids' should expand to child/children."""
        keywords = SchemaCache._extract_keywords("Number of kids per household")
        assert "kids" in keywords
        assert "child" in keywords
        assert "children" in keywords

    def test_word_without_synonyms_passes_through(self):
        """Words not in the synonym map should just appear as themselves."""
        keywords = SchemaCache._extract_keywords("Medicare recipients nationwide")
        assert "medicare" in keywords
        # No synonyms for this; should be the only census-y term
        assert "nationwide" in keywords

    def test_plural_synonyms_work(self):
        """Plural forms should also expand (graduates, kids, jobs, colleges)."""
        keywords = SchemaCache._extract_keywords(
            "Counties with the highest percentage of college graduates"
        )
        assert "graduates" in keywords
        assert "college" in keywords
        # Both should bring in education-related terms
        assert "education" in keywords
        assert "degree" in keywords
        assert "bachelor" in keywords
