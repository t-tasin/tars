"""Tests for ContextBuilder intent-driven pre-fetch (P2.5-03)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import IntentType, ModelName

from orchestrator.context_builder import ContextBuilder
from orchestrator.intent_classifier import Intent
from orchestrator.model_router import ModelRoute


@pytest.fixture
def builder() -> ContextBuilder:
    return ContextBuilder()


def _route(model: str = ModelName.LOCAL_BRAIN) -> ModelRoute:
    return ModelRoute(model=model, node="node2")


def _mock_weather() -> dict[str, Any]:
    return {"temp": 72.0, "description": "Sunny", "humidity": 40}


def _mock_events() -> list[dict[str, Any]]:
    return [
        {"title": "Standup", "start": "2026-04-25T09:00:00", "calendar": "Work"},
        {"title": "Lunch", "start": "2026-04-25T12:00:00", "calendar": "Personal"},
    ]


def _mock_emails() -> list[dict[str, Any]]:
    return [
        {"subject": "Hello", "from": "boss@work.com", "date": "2026-04-25"},
    ]


# ---------------------------------------------------------------------------
# Test: weather pre-fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weather_keyword_triggers_prefetch(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.GENERAL)
    mock_client = MagicMock()
    mock_client.get_current = AsyncMock(return_value=_mock_weather())
    mock_client.close = AsyncMock()

    with patch("integrations.weather_client.WeatherClient", return_value=mock_client):
        ctx = await builder.build(intent, _route(), "What's the weather today?", "telegram")

    assert "weather" in ctx.system_context
    assert ctx.system_context["weather"]["temp"] == 72.0
    mock_client.get_current.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: schedule pre-fetch (DAILY_LIFE intent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_life_intent_prefetches_schedule(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.DAILY_LIFE)
    mock_client = MagicMock()
    mock_client.get_today_events = AsyncMock(return_value=_mock_events())
    mock_client.close = AsyncMock()

    with patch("integrations.caldav_client.CalDAVClient", return_value=mock_client):
        ctx = await builder.build(intent, _route(), "What's on my schedule?", "telegram")

    assert "schedule" in ctx.system_context
    assert len(ctx.system_context["schedule"]) == 2
    mock_client.get_today_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_keyword_triggers_prefetch(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.GENERAL)
    mock_client = MagicMock()
    mock_client.get_today_events = AsyncMock(return_value=_mock_events())
    mock_client.close = AsyncMock()

    with patch("integrations.caldav_client.CalDAVClient", return_value=mock_client):
        ctx = await builder.build(intent, _route(), "Do I have any appointments today?", "telegram")

    assert "schedule" in ctx.system_context
    mock_client.get_today_events.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: email pre-fetch (COMMUNICATION intent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_communication_intent_prefetches_email(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.COMMUNICATION)
    mock_client = MagicMock()
    mock_client.get_unread_emails = AsyncMock(return_value=_mock_emails())
    mock_client.close = AsyncMock()

    with patch("integrations.gmail_client.GmailClient", return_value=mock_client):
        ctx = await builder.build(intent, _route(), "How many unread emails?", "telegram")

    assert "email" in ctx.system_context
    assert len(ctx.system_context["email"]) == 1
    mock_client.get_unread_emails.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: finance pre-fetch (FINANCE intent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finance_intent_prefetches_transactions(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.FINANCE)
    mock_session = MagicMock()

    ctx = await builder.build(intent, _route(), "How much did I spend this week?", "telegram", session=mock_session)

    # finance pre-fetch uses existing _get_recent_transactions which returns []
    # when no real session, but the key should still be present
    assert "finance" in ctx.system_context


# ---------------------------------------------------------------------------
# Test: BRIEFING intent pre-fetches all sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_intent_prefetches_all(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.BRIEFING)
    mock_weather = MagicMock()
    mock_weather.get_current = AsyncMock(return_value=_mock_weather())
    mock_weather.close = AsyncMock()

    mock_caldav = MagicMock()
    mock_caldav.get_today_events = AsyncMock(return_value=_mock_events())

    mock_gmail = MagicMock()
    mock_gmail.get_unread_emails = AsyncMock(return_value=_mock_emails())
    mock_gmail.close = AsyncMock()

    with (
        patch("integrations.weather_client.WeatherClient", return_value=mock_weather),
        patch("integrations.caldav_client.CalDAVClient", return_value=mock_caldav),
        patch("integrations.gmail_client.GmailClient", return_value=mock_gmail),
    ):
        ctx = await builder.build(intent, _route(), "/briefing", "telegram")

    assert "weather" in ctx.system_context
    assert "schedule" in ctx.system_context
    assert "email" in ctx.system_context
    assert "finance" in ctx.system_context


# ---------------------------------------------------------------------------
# Test: pre-fetch failure is graceful (HC-09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_failure_is_graceful(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.GENERAL)
    mock_client = MagicMock()
    mock_client.get_current = AsyncMock(side_effect=RuntimeError("network timeout"))
    mock_client.close = AsyncMock()

    with patch("integrations.weather_client.WeatherClient", return_value=mock_client):
        ctx = await builder.build(intent, _route(), "What's the weather?", "telegram")

    # system_context should have weather key but be empty (graceful degradation)
    assert "weather" in ctx.system_context
    assert ctx.system_context["weather"] == {}


# ---------------------------------------------------------------------------
# Test: non-prefetch intent leaves system_context empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_prefetch_for_unrelated_intent(builder: ContextBuilder) -> None:
    intent = Intent(agent=IntentType.CODING)
    ctx = await builder.build(intent, _route(), "Fix the authentication bug", "telegram")
    # No weather/schedule/email/finance pre-fetch for coding
    assert "weather" not in ctx.system_context
    assert "schedule" not in ctx.system_context
