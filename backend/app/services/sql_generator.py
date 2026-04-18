import asyncio
import re
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

SQL_GEN_TIMEOUT = 45.0  # seconds — generous cap for SQL generation
SQL_FIX_TIMEOUT = 30.0  # seconds — shorter since it's a retry


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
11. Always geneate a single query. For question asking about both extremes (top and bottom, highest and lowest), use UNION ALL or window functions. Never use semicolons to separate multiple statement.

CRITICAL — COLUMN NAMES:
- Use ONLY column names that appear VERBATIM in the schema above. Do NOT invent variants.
- ACS column codes follow a strict pattern: B + 5 digits + (e|m) + number. Examples: "B01001e1", "B01002e1", "B19013e1".
- If you cannot find a matching column in the schema for the user's question, respond CANNOT_ANSWER.
- Do NOT add extra letters like 'a' to codes (e.g., "B01002ae1" is WRONG; "B01002e1" is correct).
"""


SQL_FIX_SYSTEM_PROMPT = """You are a SQL expert. The SQL query below failed with an error.
Fix the query using only columns and tables from the schema context.

{schema_context}

DATABASE: {database}
SCHEMA: {schema}

Rules:
- Use ONLY column names that appear VERBATIM in the schema above.
- Output ONLY the corrected raw SQL — no explanation, no markdown, no backticks.
- If the question truly cannot be answered given the schema, respond with: CANNOT_ANSWER: <brief reason>
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
        response = await asyncio.wait_for(
            llm_provider.generate(system_prompt, messages),
            timeout=SQL_GEN_TIMEOUT,
        )
        response = response.strip()

        if response.startswith("CANNOT_ANSWER:"):
            reason = response[len("CANNOT_ANSWER:"):].strip()
            return SQLGenResult.CANNOT_ANSWER, None, reason

        sql = _clean_sql_response(response)
        return SQLGenResult.OK, sql, None

    except asyncio.TimeoutError:
        logger.error(f"SQL generation timed out after {SQL_GEN_TIMEOUT}s")
        return SQLGenResult.API_ERROR, None, f"SQL generation timed out after {SQL_GEN_TIMEOUT}s"
    except Exception as e:
        logger.error(f"SQL generation failed: {e}")
        return SQLGenResult.API_ERROR, None, str(e)


async def fix_sql(
    question: str,
    failed_sql: str,
    error_message: str,
    schema_context: str,
    llm_provider,
    database: str,
    schema: str,
) -> tuple[SQLGenResult, Optional[str], Optional[str]]:
    """Ask the LLM to fix a SQL query that failed with a Snowflake error.

    Returns the same tuple shape as generate_sql().
    """
    system_prompt = SQL_FIX_SYSTEM_PROMPT.format(
        schema_context=schema_context,
        database=database,
        schema=schema,
    )
    user_msg = (
        f"Original user question: {question}\n\n"
        f"Failed SQL:\n{failed_sql}\n\n"
        f"Snowflake error:\n{error_message}\n\n"
        "Please produce a corrected SQL query."
    )
    try:
        response = await asyncio.wait_for(
            llm_provider.generate(system_prompt, [{"role": "user", "content": user_msg}]),
            timeout=SQL_FIX_TIMEOUT,
        )
        response = response.strip()
        if response.startswith("CANNOT_ANSWER:"):
            return SQLGenResult.CANNOT_ANSWER, None, response[len("CANNOT_ANSWER:"):].strip()
        return SQLGenResult.OK, _clean_sql_response(response), None
    except asyncio.TimeoutError:
        logger.error(f"SQL fix timed out after {SQL_FIX_TIMEOUT}s")
        return SQLGenResult.API_ERROR, None, f"SQL fix timed out after {SQL_FIX_TIMEOUT}s"
    except Exception as e:
        logger.error(f"SQL fix failed: {e}")
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
