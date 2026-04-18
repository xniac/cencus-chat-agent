import re
from contextlib import contextmanager
from typing import Any

import snowflake.connector
from snowflake.connector import DictCursor

from backend.app.config import settings


class SnowflakeQueryError(Exception):
    pass


class SnowflakeConnectionError(Exception):
    pass


class SnowflakeClient:
    def __init__(self):
        self._connection_params = {
            "account": settings.snowflake_account,
            "user": settings.snowflake_user,
            "password": settings.snowflake_password,
            "database": settings.snowflake_database,
            "schema": settings.snowflake_schema,
            "warehouse": settings.snowflake_warehouse,
            "role": settings.snowflake_role,
        }

    @contextmanager
    def _get_connection(self):
        conn = None
        try:
            conn = snowflake.connector.connect(**self._connection_params)
            yield conn
        finally:
            if conn:
                conn.close()

    def execute_query(self, sql: str, timeout: int | None = None) -> list[dict[str, Any]]:
        timeout = timeout or settings.snowflake_query_timeout
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor(DictCursor)
                cursor.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout}")
                cursor.execute(sql)
                return cursor.fetchall()
        except snowflake.connector.errors.ProgrammingError as e:
            raise SnowflakeQueryError(self._sanitize_error(str(e))) from e
        except snowflake.connector.errors.OperationalError as e:
            raise SnowflakeConnectionError(self._sanitize_error(str(e))) from e
        except snowflake.connector.errors.DatabaseError as e:
            raise SnowflakeQueryError(self._sanitize_error(str(e))) from e

    def test_connection(self) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return True
        except Exception:
            return False

    def get_tables(self) -> list[dict[str, Any]]:
        sql = (
            f"SELECT TABLE_NAME, ROW_COUNT, COMMENT "
            f"FROM {settings.snowflake_database}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = '{settings.snowflake_schema}' "
            f"AND TABLE_TYPE = 'BASE TABLE' "
            f"ORDER BY TABLE_NAME"
        )
        return self.execute_query(sql)

    def get_columns(self, table_name: str) -> list[dict[str, Any]]:
        sql = (
            f"SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT "
            f"FROM {settings.snowflake_database}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{settings.snowflake_schema}' "
            f"AND TABLE_NAME = '{table_name}' "
            f"ORDER BY ORDINAL_POSITION"
        )
        return self.execute_query(sql)

    def get_all_columns(self) -> list[dict[str, Any]]:
        sql = (
            f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT "
            f"FROM {settings.snowflake_database}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{settings.snowflake_schema}' "
            f"ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        return self.execute_query(sql)

    def get_sample_values(self, table: str, column: str, limit: int = 5) -> list[dict[str, Any]]:
        sql = (
            f"SELECT DISTINCT {column} "
            f"FROM {settings.snowflake_database}.{settings.snowflake_schema}.{table} "
            f"WHERE {column} IS NOT NULL "
            f"LIMIT {limit}"
        )
        return self.execute_query(sql)

    @staticmethod
    def _sanitize_error(msg: str) -> str:
        sensitive_words = ["password", "token", "secret", "credential", "auth"]
        sanitized = msg
        for word in sensitive_words:
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized
