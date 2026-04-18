import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from backend.app.services.session import ConversationSession, SessionStore


class TestConversationSession:
    def test_creation(self):
        session = ConversationSession()
        assert session.session_id
        assert len(session.messages) == 0

    def test_add_message(self):
        session = ConversationSession()
        session.add_message("user", "Hello")
        assert len(session.messages) == 1
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "Hello"

    def test_get_history_for_llm_excludes_sql(self):
        session = ConversationSession()
        session.add_message("user", "Query population")
        session.add_message("assistant", "Here are the results", sql="SELECT * FROM t")
        history = session.get_history_for_llm()
        assert len(history) == 2
        assert "sql" not in history[1]
        assert history[1]["content"] == "Here are the results"

    def test_max_history_trimming(self):
        with patch("backend.app.services.session.settings") as mock_settings:
            mock_settings.max_history = 5
            mock_settings.session_ttl_minutes = 30
            session = ConversationSession()
            for i in range(10):
                session.add_message("user", f"Message {i}")
            assert len(session.messages) == 5
            assert session.messages[0].content == "Message 5"

    def test_is_expired(self):
        session = ConversationSession()
        session.last_active = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert session.is_expired()

    def test_is_not_expired(self):
        session = ConversationSession()
        assert not session.is_expired()


class TestSessionStore:
    @pytest.mark.asyncio
    async def test_create_new_session(self):
        store = SessionStore()
        session = await store.get_or_create()
        assert session.session_id
        assert store.active_count == 1

    @pytest.mark.asyncio
    async def test_get_existing_session(self):
        store = SessionStore()
        session1 = await store.get_or_create()
        session2 = await store.get_or_create(session1.session_id)
        assert session1.session_id == session2.session_id

    @pytest.mark.asyncio
    async def test_expired_session_creates_new(self):
        store = SessionStore()
        session1 = await store.get_or_create()
        session1.last_active = datetime.now(timezone.utc) - timedelta(minutes=60)
        session2 = await store.get_or_create(session1.session_id)
        assert session1.session_id != session2.session_id

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self):
        store = SessionStore()
        session = await store.get("nonexistent-id")
        assert session is None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_expired(self):
        store = SessionStore()
        session = await store.get_or_create()
        session.last_active = datetime.now(timezone.utc) - timedelta(minutes=60)
        result = await store.get(session.session_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        store = SessionStore()
        session1 = await store.get_or_create()
        session2 = await store.get_or_create()
        session1.last_active = datetime.now(timezone.utc) - timedelta(minutes=60)
        await store.cleanup_expired()
        assert store.active_count == 1
