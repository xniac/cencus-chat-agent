import re
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SQLGenResult(str, Enum):
    OK = "ok"
    CANNOT_ANSWER = "cannot_answer"
    API_ERROR = "api_error"

SQL_GENERATION_SYSTEM_PROMPT = """You are a SQL expert that converts natural language questions into Snowflake SQL queries.

{schema_context}

DATABASE: {database}
SCHEMA: {schema}

RULES:
1. Generate ONLY SELECT queries — never INSERT, UPDATE, DELETE, DROP, or any DDL/DML.
2. Always use fully qualified table names: {database}.{schema}.<TABLE_NAME>
3. Census data is at the Census Block Group (CBG) level. For county, state, or national questions, aggregate CBG-level data using SUM, AVG, COUNT, etc.
4. Always include LIMIT 100 unless the user explicitly asks for more.
5. Use clear column aliases with AS for readability.
6. When geographic context is needed, JOIN with geographic/metadata tables using CENSUS_BLOCK_GROUP as the key.
7. ROUND percentages and averages to 2 decimal places.
8. Use COALESCE for columns that may have NULL values.
9. Output ONLY the raw SQL query — no explanation, no markdown, no backticks.
10. If the question cannot be answered with the available data, respond with: CANNOT_ANSWER: <brief reason>
"""


async def generate_sql(
    question: str,
    schema_context: str,
    history: list[dict],
    llm_provider,
    database: str,
    schema: str,
) -> tuple[SQLGenResult, Optional[str], Optional[str]]:
    """Returns (result, sql, reason).

    - OK: sql is populated, reason is None
    - CANNOT_ANSWER: sql is None, reason is the LLM's explanation
    - API_ERROR: sql is None, reason is the raw exception message
    """
    system_prompt = SQL_GENERATION_SYSTEM_PROMPT.format(
        schema_context=schema_context,
        database=database,
        schema=schema,
    )

    # Include last 6 history messages for context
    recent_history = history[-6:] if len(history) > 6 else history
    messages = recent_history + [{"role": "user", "content": question}]

    try:
        response = await llm_provider.generate(system_prompt, messages)
        response = response.strip()

        if response.startswith("CANNOT_ANSWER:"):
            reason = response[len("CANNOT_ANSWER:"):].strip()
            return SQLGenResult.CANNOT_ANSWER, None, reason

        sql = _clean_sql_response(response)
        return SQLGenResult.OK, sql, None

    except Exception as e:
        logger.error(f"SQL generation failed: {e}")
        return SQLGenResult.API_ERROR, None, str(e)


def _clean_sql_response(response: str) -> str:
    cleaned = response.strip()

    # Remove markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```sql or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # Strip trailing semicolons
    cleaned = cleaned.rstrip(";").strip()

    return cleaned
