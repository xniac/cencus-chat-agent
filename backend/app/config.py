from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings

# Look for .env in the project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_PATHS = [_PROJECT_ROOT / ".env", Path(".env")]


class Settings(BaseSettings):
    # Snowflake
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_database: str = "US_OPEN_CENSUS_DATA"
    snowflake_schema: str = "PUBLIC"
    snowflake_warehouse: str = "COMPUTE_WH"
    snowflake_role: str = "ACCOUNTADMIN"
    snowflake_query_timeout: int = 50

    # LLM
    llm_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Session
    session_ttl_minutes: int = 30
    max_history: int = 20

    # App
    app_title: str = "US Census Chat Agent"
    debug: bool = False
    # Comma-separated allowed origins for CORS. Leave empty to serve only
    # same-origin requests (the FastAPI backend serving the built React app).
    # Example for separate frontend/backend: "https://my-frontend.com,https://localhost:3000"
    cors_origins: str = ""

    model_config = {"env_file": [str(p) for p in _ENV_PATHS if p.exists()] or ".env"}


settings = Settings()
