"""Tests for EODSummaryAgent."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import db.models so SQLAlchemy registers types once.
import db.models  # noqa: F401
from agents.base import AgentContext

# Install or reuse a fake db.session (same pattern as test_briefing_agent.py)
if "db.session" not in sys.modules:
    _fake_db_session_module = types.ModuleType("db.session")
    _fake_db_session_module.get_db_session = None  # type: ignore[attr-defined]
    sys.modules["db.session"] = _fake_db_session_module
else:
    _fake_db_session_module = sys.modules["db.session"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(**kwargs: Any) -> AgentContext:
    defaults = {
        "user_message": "/eod",
        "intent_type": "eod_summary",
        "source": "system",
    }
    defaults.update(kwargs)
    return AgentContext(**defaults)


def _make_agent() -> Any:
    """Create an EODSummaryAgent bypassing __init__."""
    from agents.eod_summary import EODSummaryAgent

    return EODSummaryAgent()


class _FakeDbCtx:
    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _install_mock_session(
    execute_returns: list[MagicMock] | MagicMock | None = None,
) -> AsyncMock:
    mock_session = AsyncMock()
    if isinstance(execute_returns, list):
        mock_session.execute = AsyncMock(side_effect=execute_returns)
    elif execute_returns is not None:
        mock_session.execute = AsyncMock(return_value=execute_returns)
    else:
        result = MagicMock()
        result.all.return_value = []
        result.scalar_one.return_value = Decimal("0")
        result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=result)

    _fake_db_session_module.get_db_session = lambda: _FakeDbCtx(mock_session)
    return mock_session


# ---------------------------------------------------------------------------
# Tests: _compute_alarm_time
# ---------------------------------------------------------------------------


class TestComputeAlarmTime:
    def test_alarm_from_first_event(self) -> None:
        agent = _make_agent()
        events = [{"start": "09:00", "title": "Standup"}]
        alarm = agent._compute_alarm_time(events)
        assert alarm is not None
        assert "8:00 AM" in alarm

    def test_alarm_from_iso_datetime(self) -> None:
        agent = _make_agent()
        events = [{"start": "2026-03-11T10:30:00-04:00", "title": "Interview"}]
        alarm = agent._compute_alarm_time(events)
        assert alarm is not None
        assert "9:30 AM" in alarm

    def test_alarm_none_when_no_events(self) -> None:
        agent = _make_agent()
        assert agent._compute_alarm_time([]) is None

    def test_alarm_none_when_no_start_time(self) -> None:
        agent = _make_agent()
        events = [{"title": "All day", "start": ""}]
        assert agent._compute_alarm_time(events) is None


# ---------------------------------------------------------------------------
# Tests: _fetch_tasks_completed
# ---------------------------------------------------------------------------


class TestFetchTasksCompleted:
    @pytest.mark.asyncio
    async def test_returns_completed_tasks(self) -> None:
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(
                agent_type="briefing",
                status="completed",
                completed_at=datetime.now(UTC),
                duration_ms=3000,
            ),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_tasks_completed()
        assert len(result) == 1
        assert result[0]["agent_type"] == "briefing"

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self) -> None:
        agent = _make_agent()
        _install_mock_session()
        result = await agent._fetch_tasks_completed()
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _fetch_emails_classified
# ---------------------------------------------------------------------------


class TestFetchEmailsClassified:
    @pytest.mark.asyncio
    async def test_returns_counts_by_tier(self) -> None:
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(classified_tier="urgent", count=2),
            MagicMock(classified_tier="actionable", count=5),
            MagicMock(classified_tier="noise", count=10),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_emails_classified()
        assert result["total"] == 17
        assert result["by_tier"]["urgent"] == 2


# ---------------------------------------------------------------------------
# Tests: _fetch_approvals_processed
# ---------------------------------------------------------------------------


class TestFetchApprovalsProcessed:
    @pytest.mark.asyncio
    async def test_returns_approval_counts(self) -> None:
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(status="approved", count=3),
            MagicMock(status="rejected", count=1),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_approvals_processed()
        assert result["approved"] == 3
        assert result["rejected"] == 1
        assert result["total"] == 4


# ---------------------------------------------------------------------------
# Tests: _fetch_pending_items
# ---------------------------------------------------------------------------


class TestFetchPendingItems:
    @pytest.mark.asyncio
    async def test_returns_pending_counts(self) -> None:
        agent = _make_agent()

        mock_approvals_result = MagicMock()
        mock_approvals_result.scalar.return_value = 2
        mock_tasks_result = MagicMock()
        mock_tasks_result.scalar.return_value = 5
        _install_mock_session([mock_approvals_result, mock_tasks_result])

        result = await agent._fetch_pending_items()
        assert result["pending_approvals"] == 2
        assert result["open_tasks"] == 5


# ---------------------------------------------------------------------------
# Tests: _compose_narrative
# ---------------------------------------------------------------------------


class TestComposeNarrative:
    @pytest.mark.asyncio
    async def test_returns_gemini_text(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Good evening, Tasin. You accomplished a lot today."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 300
        mock_response.tokens_output = 150
        mock_response.duration_ms = 1000
        mock_gemini.generate = AsyncMock(return_value=mock_response)

        result = await agent._compose_narrative({"tasks": []}, mock_gemini)
        assert "Good evening, Tasin" in result

    @pytest.mark.asyncio
    async def test_fallback_when_no_gemini(self) -> None:
        agent = _make_agent()
        result = await agent._compose_narrative({}, None)
        assert "trouble composing" in result

    @pytest.mark.asyncio
    async def test_fallback_on_gemini_error(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_gemini.generate = AsyncMock(side_effect=RuntimeError("API down"))

        result = await agent._compose_narrative({}, mock_gemini)
        assert "trouble composing" in result


# ---------------------------------------------------------------------------
# Tests: _store_briefing
# ---------------------------------------------------------------------------


class TestStoreBriefing:
    @pytest.mark.asyncio
    async def test_stores_with_eod_type(self) -> None:
        agent = _make_agent()
        session = _install_mock_session()

        briefing_id = await agent._store_briefing({"summary": "done"}, "Good evening.")
        assert briefing_id is not None
        assert session.add.called


# ---------------------------------------------------------------------------
# Tests: execute (full flow)
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_returns_agent_result(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        mock_caldav = AsyncMock()
        mock_caldav.get_events = AsyncMock(return_value=[])

        with (
            patch.object(agent, "_fetch_tasks_completed", new_callable=AsyncMock, return_value=[]),
            patch.object(
                agent, "_fetch_emails_classified", new_callable=AsyncMock, return_value={"total": 0, "by_tier": {}}
            ),
            patch.object(
                agent,
                "_fetch_approvals_processed",
                new_callable=AsyncMock,
                return_value={"approved": 0, "rejected": 0, "total": 0},
            ),
            patch.object(agent, "_fetch_tomorrow_calendar", new_callable=AsyncMock, return_value=[]),
            patch.object(
                agent, "_fetch_ai_usage", new_callable=AsyncMock, return_value={"total_calls": 0, "by_model": {}}
            ),
            patch.object(
                agent, "_fetch_spending", new_callable=AsyncMock, return_value={"total": 0, "transactions": []}
            ),
            patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}),
            patch.object(
                agent,
                "_fetch_pending_items",
                new_callable=AsyncMock,
                return_value={"pending_approvals": 0, "open_tasks": 0},
            ),
        ):
            result = await agent.execute(_context())

        assert result.success is True
        assert result.content_type == "briefing"
        assert result.data["type"] == "end_of_day"
        assert "briefing_id" in result.data
        # Fallback narrative (no Gemini provided)
        assert "trouble composing" in result.text

    @pytest.mark.asyncio
    async def test_agent_type(self) -> None:
        agent = _make_agent()
        assert agent.agent_type == "eod_summary"

    @pytest.mark.asyncio
    async def test_individual_failures_handled(self) -> None:
        """HC-09: Individual data source failures don't crash the agent."""
        agent = _make_agent()
        _install_mock_session()

        with (
            patch.object(agent, "_fetch_tasks_completed", new_callable=AsyncMock, side_effect=RuntimeError("DB down")),
            patch.object(agent, "_fetch_emails_classified", new_callable=AsyncMock, side_effect=RuntimeError("fail")),
            patch.object(
                agent,
                "_fetch_approvals_processed",
                new_callable=AsyncMock,
                return_value={"approved": 0, "rejected": 0, "total": 0},
            ),
            patch.object(
                agent, "_fetch_tomorrow_calendar", new_callable=AsyncMock, side_effect=ConnectionError("CalDAV down")
            ),
            patch.object(
                agent, "_fetch_ai_usage", new_callable=AsyncMock, return_value={"total_calls": 0, "by_model": {}}
            ),
            patch.object(
                agent, "_fetch_spending", new_callable=AsyncMock, return_value={"total": 0, "transactions": []}
            ),
            patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}),
            patch.object(
                agent,
                "_fetch_pending_items",
                new_callable=AsyncMock,
                return_value={"pending_approvals": 0, "open_tasks": 0},
            ),
        ):
            result = await agent.execute(_context())

        assert result.success is True
        # Failed sources get fallback values
        payload = result.data["payload"]
        assert payload["tasks_completed"] == []
        assert payload["emails_classified"] == {"total": 0, "by_tier": {}}
        assert payload["tomorrow_calendar"] == []
