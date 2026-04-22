"""Tests for DailyLifeAgent — scheduling, reminders, calendar queries."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure db.models is imported (conftest.py fakes db.session).
import db.models  # noqa: F401
from agents.base import AgentContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(message: str, intent: str = "daily_life", **config: Any) -> AgentContext:
    return AgentContext(
        user_message=message,
        intent_type=intent,
        source="ios",
        config=config,
    )


def _make_agent(caldav: Any = None) -> Any:
    """Create a DailyLifeAgent with mocked CalDAV, bypassing __init__."""
    from agents.daily_life import DailyLifeAgent

    agent = object.__new__(DailyLifeAgent)
    agent._caldav = caldav or _mock_caldav()
    return agent


def _mock_caldav(
    today_events: list[dict[str, Any]] | None = None,
    week_events: list[dict[str, Any]] | None = None,
) -> MagicMock:
    caldav = MagicMock()
    caldav.get_today_events = AsyncMock(return_value=today_events or [])
    caldav.get_events = AsyncMock(return_value=week_events or [])
    caldav.create_event = AsyncMock(return_value={"title": "test", "start": "2026-03-12T18:00:00"})
    return caldav


def _mock_gemini(response_json: dict[str, Any] | list[str] | None = None) -> MagicMock:
    """Create a mock GeminiClient that returns JSON."""
    gemini = MagicMock()
    text = json.dumps(response_json) if response_json else "{}"
    gemini.generate = AsyncMock(
        return_value=MagicMock(
            text=text,
            tokens_input=50,
            tokens_output=30,
            duration_ms=200,
        )
    )
    return gemini


# ---------------------------------------------------------------------------
# Sub-intent classification
# ---------------------------------------------------------------------------


class TestSubIntentClassification:
    def test_show_schedule(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("show schedule") == "show_schedule"
        assert _classify_sub_intent("What's on today?") == "show_schedule"
        assert _classify_sub_intent("/schedule") == "show_schedule"
        assert _classify_sub_intent("my schedule") == "show_schedule"

    def test_schedule_event(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("schedule gym Thursday 6pm") == "schedule"
        assert _classify_sub_intent("book a meeting at 3pm") == "schedule"

    def test_reminder(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("remind me to call the dentist") == "reminder"
        assert _classify_sub_intent("add task: buy groceries") == "reminder"
        assert _classify_sub_intent("todo pick up dry cleaning") == "reminder"

    def test_free_time(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("find free time this week") == "free_time"
        assert _classify_sub_intent("when am I free?") == "free_time"

    def test_grocery(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("add milk to grocery list") == "grocery"
        assert _classify_sub_intent("buy eggs and bread") == "grocery"

    def test_unknown(self) -> None:
        from agents.daily_life import _classify_sub_intent

        assert _classify_sub_intent("hello there") == "unknown"


# ---------------------------------------------------------------------------
# Show schedule
# ---------------------------------------------------------------------------


class TestShowSchedule:
    @pytest.mark.asyncio
    async def test_empty_schedule(self) -> None:
        agent = _make_agent()
        result = await agent.execute(_context("show schedule"))

        assert result.success is True
        assert "clear today" in result.text.lower()

    @pytest.mark.asyncio
    async def test_with_events(self) -> None:
        events = [
            {
                "title": "Team standup",
                "start": "2026-03-10T09:00:00-04:00",
                "end": "2026-03-10T09:30:00-04:00",
                "all_day": False,
                "location": "Zoom",
            },
            {
                "title": "Lunch",
                "start": "2026-03-10T12:00:00-04:00",
                "end": "2026-03-10T13:00:00-04:00",
                "all_day": False,
                "location": None,
            },
        ]
        agent = _make_agent(caldav=_mock_caldav(today_events=events))
        result = await agent.execute(_context("show schedule"))

        assert result.success is True
        assert "Team standup" in result.text
        assert "Lunch" in result.text
        assert "Zoom" in result.text
        assert len(result.cards) == 2

    @pytest.mark.asyncio
    async def test_calendar_unavailable(self) -> None:
        caldav = _mock_caldav()
        caldav.get_today_events = AsyncMock(side_effect=ConnectionError("timeout"))
        agent = _make_agent(caldav=caldav)

        result = await agent.execute(_context("show schedule"))

        assert result.success is False
        assert "couldn't reach" in result.text.lower()


# ---------------------------------------------------------------------------
# Schedule event (with Gemini)
# ---------------------------------------------------------------------------


class TestScheduleEvent:
    @pytest.mark.asyncio
    async def test_schedule_with_gemini(self) -> None:
        gemini = _mock_gemini(
            {
                "title": "Gym",
                "start": "2026-03-12T18:00:00-04:00",
                "end": "2026-03-12T19:00:00-04:00",
                "location": None,
                "description": None,
            }
        )
        agent = _make_agent()
        result = await agent.execute(_context("schedule gym Thursday 6pm", gemini_client=gemini))

        assert result.success is True
        assert result.has_side_effects is True
        assert result.action_type == "create_event"
        assert result.preview is not None
        assert result.preview["title"] == "Gym"
        assert "approve" in result.text.lower()

    @pytest.mark.asyncio
    async def test_schedule_without_gemini_fallback(self) -> None:
        agent = _make_agent()
        result = await agent.execute(_context("schedule gym on Thursday at 6pm"))

        # Without Gemini, basic parse extracts title but no datetime
        # Should get a parse result (possibly incomplete)
        assert result is not None

    @pytest.mark.asyncio
    async def test_schedule_parse_failure(self) -> None:
        gemini = _mock_gemini()
        gemini.generate = AsyncMock(
            return_value=MagicMock(
                text="not valid json!!!",
                tokens_input=50,
                tokens_output=30,
                duration_ms=200,
            )
        )
        agent = _make_agent()
        result = await agent.execute(_context("schedule gym Thursday 6pm", gemini_client=gemini))

        # Falls back to basic parse, may succeed or fail gracefully
        assert result is not None


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class TestReminder:
    @pytest.mark.asyncio
    async def test_reminder_with_gemini(self) -> None:
        gemini = _mock_gemini(
            {
                "task": "Call the dentist",
                "due_date": "2026-03-11",
                "priority": "normal",
            }
        )
        agent = _make_agent()
        result = await agent.execute(_context("remind me to call the dentist tomorrow", gemini_client=gemini))

        assert result.success is True
        assert result.has_side_effects is True
        assert result.action_type == "create_notion"
        assert result.preview is not None
        assert result.preview["task"] == "Call the dentist"

    @pytest.mark.asyncio
    async def test_reminder_without_gemini(self) -> None:
        agent = _make_agent()
        result = await agent.execute(_context("remind me to call the dentist"))

        assert result.success is True
        assert result.has_side_effects is True
        assert result.preview["task"] == "call the dentist"


# ---------------------------------------------------------------------------
# Free time
# ---------------------------------------------------------------------------


class TestFreeTime:
    @pytest.mark.asyncio
    async def test_free_time_empty_calendar(self) -> None:
        agent = _make_agent()
        result = await agent.execute(_context("find free time this week"))

        assert result.success is True
        # Empty calendar means lots of free time
        assert "free slots" in result.text.lower() or "packed" in result.text.lower()

    @pytest.mark.asyncio
    async def test_free_time_with_events(self) -> None:
        today = date.today()
        events = [
            {
                "title": "Meeting",
                "start": datetime.combine(today, datetime.min.time()).replace(hour=9).isoformat(),
                "end": datetime.combine(today, datetime.min.time()).replace(hour=17).isoformat(),
                "all_day": False,
            },
        ]
        agent = _make_agent(caldav=_mock_caldav(week_events=events))
        result = await agent.execute(_context("find free time this week"))

        assert result.success is True


# ---------------------------------------------------------------------------
# Grocery
# ---------------------------------------------------------------------------


class TestGrocery:
    @pytest.mark.asyncio
    async def test_grocery_with_gemini(self) -> None:
        gemini = _mock_gemini(["milk", "eggs", "bread"])
        agent = _make_agent()
        result = await agent.execute(_context("add milk, eggs and bread to grocery list", gemini_client=gemini))

        assert result.success is True
        assert result.has_side_effects is True
        assert result.action_type == "create_notion"
        assert result.preview is not None
        assert "milk" in result.preview["items"]

    @pytest.mark.asyncio
    async def test_grocery_without_gemini(self) -> None:
        agent = _make_agent()
        result = await agent.execute(_context("add milk and eggs to grocery list"))

        assert result.success is True
        assert "milk" in result.preview["items"]
        assert "eggs" in result.preview["items"]


# ---------------------------------------------------------------------------
# Approved action execution
# ---------------------------------------------------------------------------


class TestApprovedActions:
    @pytest.mark.asyncio
    async def test_execute_create_event(self) -> None:
        agent = _make_agent()
        preview = {
            "title": "Gym",
            "start": "2026-03-12T18:00:00-04:00",
            "end": "2026-03-12T19:00:00-04:00",
            "location": None,
            "description": None,
        }
        result = await agent.execute_approved_action("create_event", preview)

        assert result.success is True
        assert "added to your calendar" in result.text.lower()
        agent._caldav.create_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_create_event_failure(self) -> None:
        caldav = _mock_caldav()
        caldav.create_event = AsyncMock(side_effect=RuntimeError("CalDAV error"))
        agent = _make_agent(caldav=caldav)

        preview = {
            "title": "Gym",
            "start": "2026-03-12T18:00:00-04:00",
            "end": "2026-03-12T19:00:00-04:00",
        }
        result = await agent.execute_approved_action("create_event", preview)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_execute_create_notion_reminder(self) -> None:
        agent = _make_agent()
        preview = {"task": "Call dentist", "due_date": "2026-03-11", "priority": "normal"}
        result = await agent.execute_approved_action("create_notion", preview)

        assert result.success is True
        assert "dentist" in result.text.lower()

    @pytest.mark.asyncio
    async def test_execute_create_notion_grocery(self) -> None:
        agent = _make_agent()
        preview = {"items": ["milk", "eggs"], "list": "grocery", "display": "milk, eggs"}
        result = await agent.execute_approved_action("create_notion", preview)

        assert result.success is True
        assert "2 items" in result.text


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_event_time(self) -> None:
        from agents.daily_life import _format_event_time

        assert _format_event_time({"all_day": True}) == "All day"
        assert "AM" in _format_event_time(
            {"start": "2026-03-10T09:00:00-04:00", "end": "2026-03-10T10:00:00-04:00", "all_day": False}
        )

    def test_format_duration(self) -> None:
        from agents.daily_life import _format_duration

        assert _format_duration(30) == "30min"
        assert _format_duration(60) == "1h"
        assert _format_duration(90) == "1h 30min"
        assert _format_duration(120) == "2h"

    def test_basic_grocery_parse(self) -> None:
        from agents.daily_life import _basic_grocery_parse

        items = _basic_grocery_parse("add milk, eggs and bread to grocery list")
        assert "milk" in items
        assert "eggs" in items
        assert "bread" in items

    def test_basic_reminder_parse(self) -> None:
        from agents.daily_life import _basic_reminder_parse

        result = _basic_reminder_parse("remind me to call the dentist")
        assert result is not None
        assert result["task"] == "call the dentist"

    def test_find_free_slots_empty_calendar(self) -> None:
        from agents.daily_life import _find_free_slots

        today = date.today()
        gaps = _find_free_slots([], today, days=1)
        # Entire day should be one free slot (8am-10pm = 14h)
        assert len(gaps) == 1
        assert "14h" in gaps[0]["duration"]
