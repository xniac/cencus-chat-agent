from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None


class ChatEvent(BaseModel):
    event: str
    data: str


class Message(BaseModel):
    role: str
    content: str
    sql: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseModel):
    status: str
    snowflake_connected: bool
    llm_provider: str
