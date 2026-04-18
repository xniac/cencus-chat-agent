import json
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

RESPONSE_SYSTEM_PROMPT = """You are a helpful assistant that explains US Census data query results in clear, natural language.

GUIDELINES:
- Format large numbers with commas (e.g., 1,234,567)
- Provide context for statistics (e.g., "This is higher than the national average")
- Never make up or hallucinate data — only reference what's in the results
- Use markdown formatting for readability:
  - **Bold** for key figures
  - Tables for structured data (use | header | format |)
  - Bullet points for lists
  - Headers for organizing longer responses
- If results are empty, explain what was queried and suggest alternatives
- Keep responses concise but informative
- When showing percentages, round to reasonable precision
"""


async def generate_response_stream(
    question: str,
    sql: str,
    results: list[dict],
    history: list[dict],
    llm_provider,
) -> AsyncIterator[str]:
    formatted = _format_results(results)

    user_content = (
        f"User question: {question}\n\n"
        f"SQL query executed:\n{sql}\n\n"
        f"Query results:\n{formatted}\n\n"
        f"Please provide a clear, helpful answer based on these results."
    )

    # Include last 4 history messages for context
    recent_history = history[-4:] if len(history) > 4 else history
    messages = recent_history + [{"role": "user", "content": user_content}]

    async for token in llm_provider.stream(RESPONSE_SYSTEM_PROMPT, messages):
        yield token


async def generate_response(
    question: str,
    sql: str,
    results: list[dict],
    history: list[dict],
    llm_provider,
) -> str:
    formatted = _format_results(results)

    user_content = (
        f"User question: {question}\n\n"
        f"SQL query executed:\n{sql}\n\n"
        f"Query results:\n{formatted}\n\n"
        f"Please provide a clear, helpful answer based on these results."
    )

    recent_history = history[-4:] if len(history) > 4 else history
    messages = recent_history + [{"role": "user", "content": user_content}]

    return await llm_provider.generate(RESPONSE_SYSTEM_PROMPT, messages)


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No results returned."

    if len(results) <= 20:
        return json.dumps(results, indent=2, default=str)

    truncated = results[:20]
    return (
        json.dumps(truncated, indent=2, default=str)
        + f"\n\n... and {len(results) - 20} more rows (showing first 20 of {len(results)})"
    )
