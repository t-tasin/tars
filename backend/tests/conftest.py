"""Shared test fixtures for T.A.R.S. backend tests.

Provides:
- test_db: in-memory session (or real PG via TEST_DATABASE_URL)
- mock_gemini / mock_claude / mock_gmail / mock_caldav / mock_weather
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Early patching: intercept config.get_settings AND db.session before any
# production code triggers module-level initialization.
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = MagicMock()
_MOCK_SETTINGS.database_url = "postgresql+asyncpg://test:test@localhost:5432/tars_test"
_MOCK_SETTINGS.debug = False
_MOCK_SETTINGS.redis_url = "redis://localhost:6379/0"
_MOCK_SETTINGS.tars_api_key = "test-api-key"
_MOCK_SETTINGS.gemini_api_key = "test-gemini-key"
_MOCK_SETTINGS.telegram_bot_token = "test-bot-token"
_MOCK_SETTINGS.telegram_chat_id = ""
_MOCK_SETTINGS.gmail_personal_credentials = "dGVzdA=="
_MOCK_SETTINGS.gmail_professional_credentials = "dGVzdA=="
_MOCK_SETTINGS.icloud_caldav_user = "test@icloud.com"
_MOCK_SETTINGS.icloud_caldav_password = "test-password"
_MOCK_SETTINGS.github_pat = "test-pat"
_MOCK_SETTINGS.notion_token = "test-notion"
_MOCK_SETTINGS.feature_new_router = False
_MOCK_SETTINGS.teller_access_token = "test-teller-token"
_MOCK_SETTINGS.teller_cert_path = "/tmp/test-cert.pem"
_MOCK_SETTINGS.teller_key_path = "/tmp/test-key.pem"
_MOCK_SETTINGS.teller_env = "sandbox"
_MOCK_SETTINGS.openweathermap_api_key = "test-key"
_MOCK_SETTINGS.default_location = "Wooster,OH,US"
_MOCK_SETTINGS.picovoice_access_key = "test"
_MOCK_SETTINGS.wake_word_model_paths = []
_MOCK_SETTINGS.wake_word_sensitivity = 0.6
_MOCK_SETTINGS.wake_word_silence_threshold = 500
_MOCK_SETTINGS.wake_word_silence_duration = 1.5
_MOCK_SETTINGS.wake_word_max_record_seconds = 15.0
_MOCK_SETTINGS.whisper_model = "base"
_MOCK_SETTINGS.homepod_host = ""
_MOCK_SETTINGS.usb_mic_device_index = None
_MOCK_SETTINGS.node_role = "brain"
_MOCK_SETTINGS.log_level = "INFO"
_MOCK_SETTINGS.spotify_client_id = ""
_MOCK_SETTINGS.spotify_client_secret = ""
_MOCK_SETTINGS.spotify_refresh_token = ""
_MOCK_SETTINGS.tailscale_api_key = "test-tailscale-key"

# Patch config.get_settings before any production module calls it
_settings_patcher = patch("config.get_settings", return_value=_MOCK_SETTINGS)
_settings_patcher.start()


# Create a fake db.session module so importing it never triggers the real
# module-level engine creation (which would need a real PostgreSQL).
_fake_db_session = types.ModuleType("db.session")
_fake_db_session.__file__ = "fake"


@asynccontextmanager
async def _noop_get_db_session():
    """Default placeholder — yields a basic AsyncMock session.

    Tests that need specific DB behaviour should patch ``db.session.get_db_session``
    with their own factory (see ``_db_session_factory`` in test_integration_briefing).
    """
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    result.scalar_one.return_value = 0
    result.scalar.return_value = 0
    result.scalars.return_value = result
    session.execute = AsyncMock(return_value=result)
    yield session


_fake_db_session.get_db_session = _noop_get_db_session  # type: ignore[attr-defined]
_fake_db_session.init_db = AsyncMock()  # type: ignore[attr-defined]
_fake_db_session.close_db = AsyncMock()  # type: ignore[attr-defined]
_fake_db_session.async_session_factory = MagicMock(return_value=AsyncMock())  # type: ignore[attr-defined]
sys.modules["db.session"] = _fake_db_session

# Also alias for the import path db/session.py uses internally
sys.modules["src.db.session"] = _fake_db_session

# Now we can safely import production code
from db.models import Base
from models.gemini_client import GeminiResponse

# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture()
async def test_db() -> AsyncGenerator[Any]:
    """Yield a test DB session.

    If TEST_DATABASE_URL is set (PostgreSQL), creates real tables.
    Otherwise, uses an in-memory fake session.
    """
    if TEST_DATABASE_URL:
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        engine = create_async_engine(TEST_DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        session = session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()
    else:
        yield _InMemorySession()


class _InMemorySession:
    """Minimal async session stand-in that stores ORM objects in a list."""

    def __init__(self) -> None:
        self._store: list[Any] = []
        self._pending: list[Any] = []

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if hasattr(obj, "id") and obj.id is None:
                obj.id = uuid.uuid4()
            if hasattr(obj, "created_at") and obj.created_at is None:
                obj.created_at = datetime.now(UTC)
            self._store.append(obj)
        self._pending.clear()

    async def commit(self) -> None:
        await self.flush()

    async def rollback(self) -> None:
        self._pending.clear()

    async def close(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _InMemoryResult:
        from sqlalchemy.sql.dml import Insert

        if isinstance(stmt, Insert):
            self._handle_insert(stmt)
        return _InMemoryResult(self._store, stmt)

    def _handle_insert(self, stmt: Any) -> None:
        """Materialize an Insert statement (incl. pg ON CONFLICT) into the store.

        Supports the ``pg_insert(Model).values(...).on_conflict_do_update(...)``
        pattern used by BriefingAgent so the in-memory session mirrors a real DB.
        """
        table = getattr(stmt, "table", None)
        table_name = getattr(table, "name", None)
        if not table_name:
            return

        model_cls = None
        for mapper in Base.registry.mappers:
            if getattr(mapper.class_, "__tablename__", None) == table_name:
                model_cls = mapper.class_
                break
        if model_cls is None:
            return

        values: dict[str, Any] = {}
        raw_values = getattr(stmt, "_values", None)
        if raw_values:
            for col, bindparam in raw_values.items():
                col_name = getattr(col, "name", None) or getattr(col, "key", None) or str(col)
                values[col_name] = getattr(bindparam, "value", bindparam)

        # Upsert: if a row already exists matching the unique constraint, update it
        unique_cols = self._unique_constraint_cols(model_cls)
        if unique_cols and all(c in values for c in unique_cols):
            for existing in self._store:
                if type(existing) is model_cls and all(getattr(existing, c, None) == values[c] for c in unique_cols):
                    for k, v in values.items():
                        if hasattr(model_cls, k):
                            setattr(existing, k, v)
                    return

        obj = model_cls(**{k: v for k, v in values.items() if hasattr(model_cls, k)})
        self._pending.append(obj)

    @staticmethod
    def _unique_constraint_cols(model_cls: Any) -> list[str]:
        from sqlalchemy import UniqueConstraint

        table = getattr(model_cls, "__table__", None)
        if table is None:
            return []
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                return [c.name for c in constraint.columns]
        return []


class _InMemoryResult:
    """Minimal result wrapper for in-memory queries."""

    def __init__(self, store: list[Any], stmt: Any) -> None:
        self._store = store
        self._stmt = stmt
        self._matched = self._resolve()

    def _resolve(self) -> list[Any]:
        # Detect aggregate queries (func.count, func.sum, etc.) — return empty
        stmt_str = str(self._stmt)
        if "count(" in stmt_str.lower() or "sum(" in stmt_str.lower() or "coalesce(" in stmt_str.lower():
            self._is_aggregate = True
            return []

        self._is_aggregate = False
        try:
            entity = None
            if hasattr(self._stmt, "columns_clause_froms"):
                froms = self._stmt.columns_clause_froms
                if froms:
                    entity = froms[0]
            elif hasattr(self._stmt, "froms") and self._stmt.froms:
                entity = self._stmt.froms[0]

            if entity is not None:
                table_name = getattr(entity, "name", None) or getattr(entity, "__tablename__", None)
                if table_name:
                    return [obj for obj in self._store if getattr(type(obj), "__tablename__", None) == table_name]
        except Exception:
            pass
        return list(self._store)

    def scalars(self) -> _InMemoryResult:
        return self

    def all(self) -> list[Any]:
        return self._matched

    def scalar_one_or_none(self) -> Any:
        return self._matched[0] if self._matched else None

    def scalar_one(self) -> Any:
        if getattr(self, "_is_aggregate", False):
            return 0
        if not self._matched:
            raise ValueError("No rows returned")
        return self._matched[0]

    def scalar(self) -> Any:
        if getattr(self, "_is_aggregate", False):
            return 0
        return self._matched[0] if self._matched else None


# ---------------------------------------------------------------------------
# Mock: GeminiClient
# ---------------------------------------------------------------------------


def _make_gemini_response(
    text_content: str = "Good morning, Tasin. Here is your briefing.",
    model: str = "gemini-2.0-pro",
    tokens_input: int = 500,
    tokens_output: int = 300,
) -> GeminiResponse:
    return GeminiResponse(
        text=text_content,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        duration_ms=1200,
        finish_reason="STOP",
    )


@pytest.fixture()
def mock_gemini() -> AsyncMock:
    """Patch GeminiClient with predictable responses."""
    client = AsyncMock()
    client.generate = AsyncMock(return_value=_make_gemini_response())
    client.generate_with_vision = AsyncMock(return_value=_make_gemini_response())
    client.classify = AsyncMock(return_value="informational")
    return client


# ---------------------------------------------------------------------------
# Mock: ClaudeCodeSpawner
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_claude() -> AsyncMock:
    """Patch ClaudeCodeSpawner to return test responses."""
    from models.claude_spawner import ClaudeCodeResult

    spawner = AsyncMock()
    spawner.execute = AsyncMock(
        return_value=ClaudeCodeResult(
            text="Claude test response.",
            success=True,
            duration_ms=2500,
            cost_usd=0.0,
            session_id="test-session",
            num_turns=1,
        ),
    )
    return spawner


# ---------------------------------------------------------------------------
# Mock: GmailClient
# ---------------------------------------------------------------------------

_TEST_EMAILS = [
    {
        "message_id": f"msg_{i}",
        "from_address": f"sender{i}@example.com",
        "from_name": f"Sender {i}",
        "to": "tasin@example.com",
        "subject": f"Test Email {i}",
        "snippet": f"This is the snippet for test email {i}.",
        "date": "Mon, 10 Mar 2026 08:00:00 -0400",
        "labels": ["INBOX", "UNREAD"],
        "has_attachments": False,
    }
    for i in range(1, 6)
]


@pytest.fixture()
def mock_gmail() -> AsyncMock:
    """Patch GmailClient to return 5 test emails per account."""
    client = AsyncMock()
    client.get_unread_emails = AsyncMock(return_value=_TEST_EMAILS[:5])
    client.get_email_body = AsyncMock(return_value="Test email body content.")
    client.close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Mock: CalDAVClient
# ---------------------------------------------------------------------------

_TEST_EVENTS = [
    {
        "id": f"evt-{i}",
        "title": title,
        "start": f"2026-03-10T{start}:00-04:00",
        "end": f"2026-03-10T{end}:00-04:00",
        "location": location,
        "calendar": "Personal",
        "all_day": False,
        "description": None,
    }
    for i, (title, start, end, location) in enumerate(
        [
            ("Team Standup", "09:00", "09:30", "Zoom"),
            ("Lunch with Advisor", "12:00", "13:00", "Campus Cafe"),
            ("Office Hours", "15:00", "16:00", "Gault Library"),
        ],
        start=1,
    )
]


@pytest.fixture()
def mock_caldav() -> AsyncMock:
    """Patch CalDAVClient to return 3 test events."""
    client = AsyncMock()
    client.get_events = AsyncMock(return_value=_TEST_EVENTS)
    client.get_today_events = AsyncMock(return_value=_TEST_EVENTS)
    client.get_calendars = AsyncMock(
        return_value=[{"name": "Personal", "id": "cal-1"}],
    )
    return client


# ---------------------------------------------------------------------------
# Mock: WeatherClient
# ---------------------------------------------------------------------------

_TEST_CURRENT_WEATHER = {
    "temp_c": 22.0,
    "temp_f": 71.6,
    "conditions": "clear sky",
    "humidity": 45,
    "wind_mph": 5.2,
    "icon": "01d",
    "location": "Wooster",
}

_TEST_FORECAST = [
    {
        "time": f"{10 + i}:00",
        "temp_c": 20.0 + i,
        "temp_f": 68.0 + i * 1.8,
        "conditions": "clear sky",
        "rain_probability": 5,
    }
    for i in range(4)
]

_TEST_DAILY_SUMMARY = {
    "summary": "22.0°C, clear sky, no rain expected.",
    "high": 24.0,
    "low": 18.0,
    "needs_umbrella": False,
    "suggestion": "Great weather — good day for outdoor activities.",
}


@pytest.fixture()
def mock_weather() -> AsyncMock:
    """Patch WeatherClient to return sunny weather."""
    client = AsyncMock()
    client.get_current = AsyncMock(return_value=_TEST_CURRENT_WEATHER)
    client.get_forecast = AsyncMock(return_value=_TEST_FORECAST)
    client.get_daily_summary = AsyncMock(return_value=_TEST_DAILY_SUMMARY)
    client.close = AsyncMock()
    return client
