import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DANGEROUS_SQL_PATTERNS = [
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bDELETE\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bTRUNCATE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bMERGE\b",
    r"\bCALL\b",
    r"\bCOPY\b",
    r"\bPUT\b",
    r"\bGET\b",
    r"\bREMOVE\b",
]

CENSUS_KEYWORDS = [
    "population", "census", "demographic", "age", "gender", "sex",
    "race", "ethnicity", "hispanic", "latino", "white", "black",
    "asian", "income", "poverty", "housing", "household", "family",
    "education", "school", "employment", "unemployment", "labor",
    "occupation", "commute", "transportation", "vehicle", "rent",
    "mortgage", "health", "insurance", "disability", "veteran",
    "citizenship", "immigration", "language", "english", "foreign",
    "state", "county", "city", "zip", "block group", "cbg",
    "tract", "neighborhood", "urban", "rural", "median",
    "average", "total", "percent", "ratio",
]

OFF_TOPIC_PATTERNS = [
    r"\b(recipe|cook|bake|ingredient)\b",
    r"\b(weather|forecast|temperature|rain)\b",
    r"\b(sports?|football|basketball|baseball|soccer|nfl|nba)\b",
    r"\b(movie|film|actor|actress|netflix|streaming)\b",
    r"\b(stock|crypto|bitcoin|trading|invest)\b",
    r"\b(music|song|album|artist|concert|spotify)\b",
    r"\b(joke|funny|humor|laugh)\b",
    r"\b(write me a|compose|creative writing|poem|story|fiction)\b",
    r"\b(hack|exploit|password|crack)\b",
]

TOPIC_GUARD_SYSTEM_PROMPT = (
    "You are a topic classifier for a US Census data chatbot. "
    "The chatbot answers questions about US population demographics, "
    "housing, economics, education, employment, and related census data. "
    "Classify the user's message as either 'on_topic' or 'off_topic'. "
    "Respond with ONLY 'on_topic' or 'off_topic', nothing else. "
    "Greetings and general questions about the chatbot's capabilities are on_topic."
)


def validate_sql_safety(sql: str) -> tuple[bool, str]:
    if not sql or not sql.strip():
        return False, "Empty SQL query"

    stripped = sql.strip().upper()

    # Must start with SELECT or WITH
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        return False, "Only SELECT queries are allowed"

    # Check for dangerous patterns
    for pattern in DANGEROUS_SQL_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            return False, f"Query contains disallowed operation"

    # Check for multi-statement (semicolons not at the very end)
    sql_trimmed = sql.strip().rstrip(";")
    if ";" in sql_trimmed:
        return False, "Multi-statement queries are not allowed"

    return True, ""


def quick_topic_check(message: str) -> Optional[str]:
    lower = message.lower().strip()

    # Short greetings are on topic
    greetings = ["hi", "hello", "hey", "help", "what can you do", "how does this work"]
    if lower in greetings or len(lower) < 10:
        return "on_topic"

    # Check off-topic patterns
    for pattern in OFF_TOPIC_PATTERNS:
        if re.search(pattern, lower):
            return "off_topic"

    # Check for census keywords (2+ matches = on_topic)
    keyword_count = sum(1 for kw in CENSUS_KEYWORDS if kw in lower)
    if keyword_count >= 2:
        return "on_topic"

    # Ambiguous — need LLM
    return None


async def llm_topic_check(message: str, llm_provider) -> bool:
    try:
        messages = [{"role": "user", "content": message}]
        response = await llm_provider.generate(TOPIC_GUARD_SYSTEM_PROMPT, messages)
        return "on_topic" in response.lower()
    except Exception as e:
        logger.warning(f"LLM topic check failed, allowing message: {e}")
        return True  # Fail open
