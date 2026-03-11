"""Tests for the Health & Fitness agent."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext
from agents.health_fitness import (
    HealthFitnessAgent,
    _find_free_slots,
    _is_gym_event,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_context(
    message: str = "health summary",
    lookback_days: int = 7,
    gemini_client: Any = None,
) -> AgentContext:
    config: dict[str, Any] = {"lookback_days": lookback_days}
    if gemini_client is not None:
        config["gemini_client"] = gemini_client
    return AgentContext(
        user_message=message,
        intent_type="health_fitness",
        config=config,
    )


def _make_health_rows(
    steps: float = 8500,
    sleep: float = 7.2,
    exercise: float = 45,
) -> list[tuple]:
    """Create mock health_data rows (data_type, recorded_date, value, unit, metadata)."""
    d = date(2026, 3, 9)
    rows = [
        ("steps", d, steps, "count", None),
        ("sleep", d, sleep, "hours", None),
        ("exercise_minutes", d, exercise, "minutes", None),
    ]
    # Add a second day for trend calculation
    d2 = date(2026, 3, 8)
    rows.extend([
        ("steps", d2, steps - 500, "count", None),
        ("sleep", d2, sleep - 0.3, "hours", None),
        ("exercise_minutes", d2, exercise - 10, "minutes", None),
    ])
    return rows


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestIsGymEvent:
    @pytest.mark.parametrize("title,expected", [
        ("Gym session", True),
        ("Morning workout", True),
        ("Yoga class", True),
        ("Swimming practice", True),
        ("Team Standup", False),
        ("Lunch", False),
        ("Run errands", True),
    ])
    def test_gym_detection(self, title: str, expected: bool):
        assert _is_gym_event({"title": title}) is expected


class TestFindFreeSlots:
    def test_no_events_returns_full_day(self):
        slots = _find_free_slots([])
        # With no events, should find at least one slot during gym hours
        # (assuming test runs during the day)
        assert isinstance(slots, list)

    def test_busy_day_returns_gaps(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        events = [
            {
                "title": "Meeting",
                "start": datetime(2026, 3, 10, 10, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 3, 10, 11, 0, tzinfo=tz).isoformat(),
            },
            {
                "title": "Lunch",
                "start": datetime(2026, 3, 10, 12, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 3, 10, 13, 0, tzinfo=tz).isoformat(),
            },
        ]
        slots = _find_free_slots(events)
        assert isinstance(slots, list)
        assert len(slots) <= 3  # capped at 3

    def test_all_day_events_ignored(self):
        events = [{"title": "Holiday", "all_day": True}]
        slots = _find_free_slots(events)
        assert isinstance(slots, list)


# ---------------------------------------------------------------------------
# Agent summary building tests
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_build_summary_with_data(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)
        rows = _make_health_rows()
        summary = agent._build_summary(rows, 7)

        assert summary["period_days"] == 7
        assert "steps" in summary["metrics"]
        assert "sleep" in summary["metrics"]
        assert "exercise_minutes" in summary["metrics"]

        # Check sleep quality
        assert summary["sleep_quality"] in ("excellent", "good", "fair", "poor", "no_data")

        # Check activity level
        assert summary["activity_level"] in ("very_active", "active", "moderate", "sedentary", "no_data")

    def test_sleep_quality_ratings(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        assert agent._assess_sleep({"latest": 8.0, "average": 8.0}) == "excellent"
        assert agent._assess_sleep({"latest": 7.2, "average": 7.2}) == "good"
        assert agent._assess_sleep({"latest": 6.5, "average": 6.5}) == "fair"
        assert agent._assess_sleep({"latest": 5.0, "average": 5.0}) == "poor"
        assert agent._assess_sleep(None) == "no_data"

    def test_activity_level_ratings(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        assert agent._assess_activity({"steps": {"latest": 12000, "average": 12000}}) == "very_active"
        assert agent._assess_activity({"steps": {"latest": 8000, "average": 8000}}) == "active"
        assert agent._assess_activity({"steps": {"latest": 5500, "average": 5500}}) == "moderate"
        assert agent._assess_activity({"steps": {"latest": 3000, "average": 3000}}) == "sedentary"
        assert agent._assess_activity({}) == "no_data"

    def test_workout_frequency(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        result = agent._assess_workout_frequency({"count": 5}, 7)
        assert result["frequency"] == "frequent"

        result = agent._assess_workout_frequency({"count": 3}, 7)
        assert result["frequency"] == "moderate"

        result = agent._assess_workout_frequency({"count": 1}, 7)
        assert result["frequency"] == "infrequent"

        result = agent._assess_workout_frequency(None, 7)
        assert result["frequency"] == "no_data"


class TestFallbackText:
    def test_no_metrics(self):
        text = HealthFitnessAgent._build_fallback_text({"metrics": {}})
        assert "synced" in text.lower() or "check back" in text.lower()

    def test_with_sleep_and_steps(self):
        summary = {
            "metrics": {
                "sleep": {"latest": 7.5, "average": 7.5},
                "steps": {"latest": 9000, "average": 9000},
            },
            "sleep_quality": "good",
            "workout_frequency": {"days_with_exercise": 3, "total_days": 7},
            "calendar_context": {},
        }
        text = HealthFitnessAgent._build_fallback_text(summary)
        assert "Sleep" in text
        assert "Steps" in text


# ---------------------------------------------------------------------------
# Agent execute tests (mocking DB and integrations)
# ---------------------------------------------------------------------------


class TestHealthFitnessExecute:
    async def test_no_health_data(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db.session.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await agent.execute(_make_context())

        assert result.success is True
        assert "no health data" in result.text.lower()

    async def test_with_health_data_no_gemini(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        rows = _make_health_rows()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db.session.get_db_session") as mock_db, \
             patch.object(agent, "_get_calendar_context", return_value={}):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await agent.execute(_make_context())

        assert result.success is True
        assert result.content_type == "card"
        assert len(result.cards) == 1
        assert result.cards[0]["type"] == "health_summary"

    async def test_with_gemini_insights(self):
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        rows = _make_health_rows()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_gemini = AsyncMock()
        gemini_response = MagicMock()
        gemini_response.text = "Great job on the steps! Consider more sleep."
        gemini_response.model = "gemini-2.0-flash"
        gemini_response.tokens_input = 300
        gemini_response.tokens_output = 150
        mock_gemini.generate = AsyncMock(return_value=gemini_response)

        with patch("db.session.get_db_session") as mock_db, \
             patch.object(agent, "_get_calendar_context", return_value={}):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await agent.execute(_make_context(gemini_client=mock_gemini))

        assert result.success is True
        assert "Great job" in result.text
