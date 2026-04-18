from backend.app.services.sql_generator import _clean_sql_response, _wrap_union_arms


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
