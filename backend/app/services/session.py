import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.app.config import settings
from backend.app.models.schemas import Message


class ConversationSession:
    def __init__(self):
        self.session_id: str = str(uuid.uuid4())
        self.messages: list[Message] = []
        self.last_active: datetime = datetime.now(timezone.utc)

    def add_message(self, role: str, content: str, sql: str | None = None) -> None:
        self.messages.append(Message(role=role, content=content, sql=sql))
        self.last_active = datetime.now(timezone.utc)
        # Trim to max history
        if len(self.messages) > settings.max_history:
            self.messages = self.messages[-settings.max_history :]

    def get_history_for_llm(self) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def get_history_for_sql_gen(self) -> list[dict]:
        """Like get_history_for_llm, but includes SQL in assistant messages
        so the LLM can see previous queries when generating follow-ups.
        """
        result = []
        for m in self.messages:
            if m.role == "assistant" and m.sql:
                content = f"[Previous SQL]\n{m.sql}\n\n[Response]\n{m.content}"
            else:
                content = m.content
            result.append({"role": m.role, "content": content})
        return result

    def is_expired(self) -> bool:
        ttl = timedelta(minutes=settings.session_ttl_minutes)
        return datetime.now(timezone.utc) - self.last_active > ttl


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, ConversationSession] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get_or_create(self, session_id: Optional[str] = None) -> ConversationSession:
        async with self._get_lock():
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                if not session.is_expired():
                    return session
            # Create new session
            session = ConversationSession()
            self._sessions[session.session_id] = session
            return session

    async def get(self, session_id: str) -> Optional[ConversationSession]:
        async with self._get_lock():
            session = self._sessions.get(session_id)
            if session and session.is_expired():
                del self._sessions[session_id]
                return None
            return session

    async def cleanup_expired(self) -> None:
        async with self._get_lock():
            expired = [
                sid for sid, session in self._sessions.items() if session.is_expired()
            ]
            for sid in expired:
                del self._sessions[sid]

    @property
    def active_count(self) -> int:
        return len(self._sessions)
