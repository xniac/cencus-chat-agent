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
11. Always generate a single query. Never use semicolons to separate multiple statements.
12. When combining queries with UNION / UNION ALL / INTERSECT / EXCEPT, Snowflake requires EACH arm to be wrapped in its own parentheses (required whenever an arm contains ORDER BY or LIMIT). Example:
    (SELECT state, pop FROM t ORDER BY pop DESC LIMIT 1)
    UNION ALL
    (SELECT state, pop FROM t ORDER BY pop ASC LIMIT 1)
13. For percentages and ratios, cast to FLOAT to avoid integer division. E.g. ROUND(SUM(col)::FLOAT / NULLIF(SUM(total), 0) * 100, 2)
14. Use ILIKE (not LIKE) for case-insensitive string matching.
15. Use IFF(condition, true_val, false_val) instead of IF().

CRITICAL — COLUMN NAMES:
- Use ONLY column names that appear VERBATIM in the schema above. Do NOT invent variants.
- ACS column codes follow a strict pattern: B + 5 digits + (e|m) + number. Examples: "B01001e1", "B01002e1", "B19013e1".
- If you cannot find a matching column in the schema for the user's question, respond CANNOT_ANSWER.
- Do NOT add extra letters like 'a' to codes (e.g., "B01002ae1" is WRONG; "B01002e1" is correct).

HANDLING FOLLOW-UP QUESTIONS:
- If the user's message is a short follow-up (e.g., "what about the bottom 5?", "now break that down by age",
  "same for 2019", "just for California"), treat it as a MODIFICATION of the PREVIOUS SQL in conversation history.
- Do NOT return CANNOT_ANSWER for a follow-up just because it's terse. Build on the prior query.
- "Break down by age" typically means adding age-group columns (B01001e3..B01001e49 for 5-year buckets) or
  median age ("B01002e1"). Prefer median age for simplicity unless the user asks for distributions.
- "By X" means GROUP BY X. "For X" means add a WHERE filter.
"""


SQL_FIX_SYSTEM_PROMPT = """You are a SQL expert. The SQL query below failed with an error when query from Snowflake.
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
    history: list[dict] | None = None,
) -> tuple[SQLGenResult, Optional[str], Optional[str]]:
    """Ask the LLM to fix a SQL query that failed with a Snowflake error.

    `history` is optional conversation history so follow-up fixes (e.g.,
    "the bottom 5") retain the context of earlier turns.

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
    # Include last 6 history messages so the fix LLM knows about prior turns
    recent_history = (history or [])[-6:]
    messages = recent_history + [{"role": "user", "content": user_msg}]
    try:
        response = await asyncio.wait_for(
            llm_provider.generate(system_prompt, messages),
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


_SET_OP_PATTERN = re.compile(r"\b(UNION\s+ALL|UNION|INTERSECT|EXCEPT)\b", re.IGNORECASE)


def _wrap_union_arms(sql: str) -> str:
    """Ensure each arm of a UNION/INTERSECT/EXCEPT is wrapped in parentheses.

    Snowflake requires parenthesized arms when any arm contains ORDER BY or
    LIMIT. LLMs often omit the parentheses. This post-processor makes the fix
    deterministically.

    Skips the transformation if the arms already appear to be wrapped.
    """
    if not _SET_OP_PATTERN.search(sql):
        return sql

    # re.split with a capturing group keeps the operators in the output.
    parts = _SET_OP_PATTERN.split(sql)
    if len(parts) < 3:
        return sql

    # parts alternates: [query, op, query, op, query, ...]
    rebuilt: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Query arm
            stripped = part.strip()
            if not stripped:
                continue
            # Already wrapped? Leave alone.
            if stripped.startswith("(") and stripped.endswith(")"):
                rebuilt.append(stripped)
            else:
                rebuilt.append(f"({stripped})")
        else:
            # Set operator
            rebuilt.append(part.strip().upper())

    return "\n".join(rebuilt)


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

    # Deterministically wrap UNION/INTERSECT/EXCEPT arms in parentheses
    cleaned = _wrap_union_arms(cleaned)

    return cleaned
