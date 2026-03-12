"""Tests for BriefingAgent data aggregation."""

from __future__ import annotations

import sys
import types
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext

# Pre-import db.models so SQLAlchemy registers types once. db.session is the
# problematic module (calls get_settings at import time), so conftest.py
# injects a fake into sys.modules before any production code loads.
import db.models  # noqa: F401

# Reference the fake db.session module installed by conftest.py.
_fake_db_session_module = sys.modules["db.session"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _context() -> AgentContext:
    return AgentContext(
        user_message="/briefing",
        intent_type="briefing",
        source="ios",
    )


def _make_agent(
    *,
    caldav: Any = None,
    gmail_personal: Any = None,
    gmail_professional: Any = None,
    weather: Any = None,
    notion: Any = None,
) -> Any:
    """Create a BriefingAgent with mocked integration clients.

    Uses ``object.__new__`` to skip ``__init__`` (avoids Settings / real
    client construction), then manually assigns the mock clients.
    """
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    agent._caldav = caldav or _mock_caldav()
    agent._gmail_personal = gmail_personal or _mock_gmail()
    agent._gmail_professional = gmail_professional or _mock_gmail()
    agent._weather = weather or _mock_weather()
    agent._notion = notion or AsyncMock()
    agent._notion_tasks_db = None
    return agent


def _mock_caldav(
    today_events: list[dict[str, Any]] | None = None,
    tomorrow_events: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    mock = AsyncMock()
    today = date.today()

    async def _get_events(d: date) -> list[dict[str, Any]]:
        if d == today:
            return today_events or []
        return tomorrow_events or []

    mock.get_events = AsyncMock(side_effect=_get_events)
    return mock


def _mock_gmail(emails: list[dict[str, Any]] | None = None) -> AsyncMock:
    mock = AsyncMock()
    mock.get_unread_emails = AsyncMock(return_value=emails or [])
    return mock


def _mock_weather() -> AsyncMock:
    mock = AsyncMock()
    mock.get_current = AsyncMock(return_value={"temp_f": 65, "conditions": "clear"})
    mock.get_forecast = AsyncMock(return_value=[{"time": "12:00", "temp_f": 70}])
    mock.get_daily_summary = AsyncMock(return_value={"summary": "Nice day", "needs_umbrella": False})
    return mock


class _FakeDbCtx:
    """Async context manager that yields a mock session."""

    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _install_mock_session(
    execute_returns: list[MagicMock] | MagicMock | None = None,
) -> AsyncMock:
    """Install a mock ``get_db_session`` on the fake module and return the session.

    *execute_returns*: a single MagicMock (always returned), a list of them
    (returned in order via ``side_effect``), or ``None`` (empty defaults).
    """
    mock_session = AsyncMock()
    if isinstance(execute_returns, list):
        mock_session.execute = AsyncMock(side_effect=execute_returns)
    elif execute_returns is not None:
        mock_session.execute = AsyncMock(return_value=execute_returns)
    else:
        result = MagicMock()
        result.all.return_value = []
        result.scalar_one.return_value = Decimal("0")
        mock_session.execute = AsyncMock(return_value=result)

    _fake_db_session_module.get_db_session = lambda: _FakeDbCtx(mock_session)
    return mock_session


# ---------------------------------------------------------------------------
# Tests: _fetch_calendar
# ---------------------------------------------------------------------------

class TestFetchCalendar:
    @pytest.mark.asyncio
    async def test_returns_today_and_tomorrow(self) -> None:
        today_events = [{"title": "Standup", "start": "09:00"}]
        tomorrow_events = [{"title": "Interview", "start": "14:00"}]
        agent = _make_agent(caldav=_mock_caldav(today_events, tomorrow_events))

        result = await agent._fetch_calendar()

        assert result["today"] == today_events
        assert result["tomorrow"] == tomorrow_events
        assert result["date"] == date.today().isoformat()

    @pytest.mark.asyncio
    async def test_empty_calendars(self) -> None:
        agent = _make_agent(caldav=_mock_caldav())

        result = await agent._fetch_calendar()

        assert result["today"] == []
        assert result["tomorrow"] == []


# ---------------------------------------------------------------------------
# Tests: _fetch_emails
# ---------------------------------------------------------------------------

class TestFetchEmails:
    @pytest.mark.asyncio
    async def test_returns_both_accounts(self) -> None:
        personal = _mock_gmail([{"subject": "Hi"}])
        professional = _mock_gmail([{"subject": "Meeting"}, {"subject": "PR Review"}])
        agent = _make_agent(gmail_personal=personal, gmail_professional=professional)

        result = await agent._fetch_emails()

        assert len(result["personal"]) == 1
        assert len(result["professional"]) == 2
        assert result["total_unread"] == 3

    @pytest.mark.asyncio
    async def test_no_emails(self) -> None:
        agent = _make_agent(gmail_personal=_mock_gmail(), gmail_professional=_mock_gmail())

        result = await agent._fetch_emails()

        assert result["total_unread"] == 0


# ---------------------------------------------------------------------------
# Tests: _fetch_weather
# ---------------------------------------------------------------------------

class TestFetchWeather:
    @pytest.mark.asyncio
    async def test_returns_current_forecast_summary(self) -> None:
        weather = _mock_weather()
        agent = _make_agent(weather=weather)

        result = await agent._fetch_weather()

        assert "current" in result
        assert "forecast" in result
        assert "summary" in result
        assert result["current"]["temp_f"] == 65


# ---------------------------------------------------------------------------
# Tests: _fetch_health_data
# ---------------------------------------------------------------------------

class TestFetchHealthData:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        result = await agent._fetch_health_data()

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_health_metrics(self) -> None:
        agent = _make_agent()
        yesterday = date.today() - timedelta(days=1)

        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("steps", Decimal("8500"), "count"),
            ("sleep", Decimal("7.5"), "hours"),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_health_data()

        assert result["steps"]["value"] == 8500.0
        assert result["sleep"]["value"] == 7.5
        assert result["date"] == yesterday.isoformat()


# ---------------------------------------------------------------------------
# Tests: _fetch_finance_data
# ---------------------------------------------------------------------------

class TestFetchFinanceData:
    @pytest.mark.asyncio
    async def test_returns_transactions_and_mtd(self) -> None:
        agent = _make_agent()

        mock_txn_result = MagicMock()
        mock_txn_result.all.return_value = [
            MagicMock(merchant_name="Starbucks", amount=Decimal("5.50"), category="Food"),
            MagicMock(merchant_name="Amazon", amount=Decimal("29.99"), category="Shopping"),
        ]
        mock_mtd_result = MagicMock()
        mock_mtd_result.scalar_one.return_value = Decimal("450.00")
        # Third call: budget query (returns no active budgets)
        mock_budget_result = MagicMock()
        mock_budget_scalars = MagicMock()
        mock_budget_scalars.all.return_value = []
        mock_budget_result.scalars.return_value = mock_budget_scalars
        # Fourth call: anomaly_detector.scan_recent → scalars().all() = []
        mock_anomaly_result = MagicMock()
        mock_anomaly_scalars = MagicMock()
        mock_anomaly_scalars.all.return_value = []
        mock_anomaly_result.scalars.return_value = mock_anomaly_scalars
        # Fifth call: recurring subscription query → .all() = []
        mock_recurring_result = MagicMock()
        mock_recurring_result.all.return_value = []
        _install_mock_session([
            mock_txn_result, mock_mtd_result, mock_budget_result,
            mock_anomaly_result, mock_recurring_result,
        ])

        result = await agent._fetch_finance_data()

        assert len(result["yesterday_transactions"]) == 2
        assert result["yesterday_total"] == pytest.approx(35.49)
        assert result["month_to_date"] == 450.0

    @pytest.mark.asyncio
    async def test_handles_null_merchant(self) -> None:
        agent = _make_agent()

        mock_txn_result = MagicMock()
        mock_txn_result.all.return_value = [
            MagicMock(merchant_name=None, amount=Decimal("10.00"), category="Other"),
        ]
        mock_mtd_result = MagicMock()
        mock_mtd_result.scalar_one.return_value = Decimal("10.00")
        # Third call: budget query (returns no active budgets)
        mock_budget_result = MagicMock()
        mock_budget_scalars = MagicMock()
        mock_budget_scalars.all.return_value = []
        mock_budget_result.scalars.return_value = mock_budget_scalars
        # Fourth call: anomaly_detector.scan_recent → scalars().all() = []
        mock_anomaly_result = MagicMock()
        mock_anomaly_scalars = MagicMock()
        mock_anomaly_scalars.all.return_value = []
        mock_anomaly_result.scalars.return_value = mock_anomaly_scalars
        # Fifth call: recurring subscription query → .all() = []
        mock_recurring_result = MagicMock()
        mock_recurring_result.all.return_value = []
        _install_mock_session([
            mock_txn_result, mock_mtd_result, mock_budget_result,
            mock_anomaly_result, mock_recurring_result,
        ])

        result = await agent._fetch_finance_data()

        assert result["yesterday_transactions"][0]["merchant"] == "Unknown"


# ---------------------------------------------------------------------------
# Tests: stubs
# ---------------------------------------------------------------------------

class TestStubs:
    @pytest.mark.asyncio
    async def test_tasks_returns_empty_when_no_db_configured(self) -> None:
        agent = _make_agent()
        # _notion_tasks_db is None by default, so _fetch_tasks returns []
        result = await agent._fetch_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_tasks_calls_notion_when_db_configured(self) -> None:
        mock_notion = AsyncMock()
        mock_notion.get_tasks_due = AsyncMock(return_value=[
            {"id": "page-1", "title": "Write tests", "properties": {}},
        ])
        agent = _make_agent(notion=mock_notion)
        agent._notion_tasks_db = "db-123"

        result = await agent._fetch_tasks()

        assert len(result) == 1
        assert result[0]["title"] == "Write tests"
        mock_notion.get_tasks_due.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_github_notifs_returns_empty_list(self) -> None:
        agent = _make_agent()
        result = await agent._fetch_github_notifs()
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _fetch_job_matches
# ---------------------------------------------------------------------------

class TestFetchJobMatches:
    @pytest.mark.asyncio
    async def test_returns_new_jobs(self) -> None:
        agent = _make_agent()

        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(title="SWE Intern", company="Google", location="Remote", match_score=85, source="linkedin"),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_job_matches()

        assert result["new_today"] == 1
        assert result["listings"][0]["title"] == "SWE Intern"
        assert result["listings"][0]["match_score"] == 85

    @pytest.mark.asyncio
    async def test_empty_when_no_new_jobs(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        result = await agent._fetch_job_matches()

        assert result["new_today"] == 0
        assert result["listings"] == []


# ---------------------------------------------------------------------------
# Tests: _fetch_system_health
# ---------------------------------------------------------------------------

class TestFetchSystemHealth:
    @pytest.mark.asyncio
    async def test_returns_unknown_when_no_data(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        result = await agent._fetch_system_health()

        assert result["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_all_green(self) -> None:
        agent = _make_agent()

        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(target="postgres", status="green", response_time_ms=5, checked_at=MagicMock(isoformat=lambda: "2026-03-10T08:00:00+00:00")),
            MagicMock(target="redis", status="green", response_time_ms=2, checked_at=MagicMock(isoformat=lambda: "2026-03-10T08:00:00+00:00")),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_system_health()

        assert result["status"] == "green"
        assert "postgres" in result["targets"]
        assert "redis" in result["targets"]

    @pytest.mark.asyncio
    async def test_degraded_when_not_all_green(self) -> None:
        agent = _make_agent()

        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(target="postgres", status="green", response_time_ms=5, checked_at=MagicMock(isoformat=lambda: "2026-03-10T08:00:00+00:00")),
            MagicMock(target="redis", status="red", response_time_ms=None, checked_at=MagicMock(isoformat=lambda: "2026-03-10T08:00:00+00:00")),
        ]
        _install_mock_session(mock_result)

        result = await agent._fetch_system_health()

        assert result["status"] == "degraded"


# ---------------------------------------------------------------------------
# Tests: _fetch_all_data (integration)
# ---------------------------------------------------------------------------

class TestFetchAllData:
    @pytest.mark.asyncio
    async def test_all_sources_succeed(self) -> None:
        agent = _make_agent(
            caldav=_mock_caldav([{"title": "Meeting"}]),
            gmail_personal=_mock_gmail([{"subject": "Hello"}]),
            gmail_professional=_mock_gmail(),
            weather=_mock_weather(),
        )

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={"steps": {"value": 10000, "unit": "count"}}), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={"yesterday_total": 50.0}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 2, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            data = await agent._fetch_all_data()

        expected_keys = {"calendar", "emails", "weather", "health", "finance", "tasks", "github", "jobs", "system_health", "_unavailable_sources"}
        assert set(data.keys()) == expected_keys
        assert data["_unavailable_sources"] == []

        assert data["calendar"]["today"] == [{"title": "Meeting"}]
        assert data["health"]["steps"]["value"] == 10000
        assert data["system_health"]["status"] == "green"

    @pytest.mark.asyncio
    async def test_individual_failures_dont_crash(self) -> None:
        """HC-09: Individual source failures are handled gracefully."""
        caldav = AsyncMock()
        caldav.get_events = AsyncMock(side_effect=ConnectionError("CalDAV down"))

        agent = _make_agent(
            caldav=caldav,
            gmail_personal=_mock_gmail(),
            gmail_professional=_mock_gmail(),
            weather=_mock_weather(),
        )

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, side_effect=RuntimeError("DB timeout")), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={"yesterday_total": 0}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            data = await agent._fetch_all_data()

        # Calendar failed — should have error fallback
        assert "error" in data["calendar"]
        assert data["calendar"]["today"] == []
        # Health failed — should have empty fallback
        assert data["health"] == {}
        # Weather succeeded
        assert "current" in data["weather"]
        # Other sources still present
        assert data["system_health"]["status"] == "green"

    @pytest.mark.asyncio
    async def test_all_sources_fail_still_returns_payload(self) -> None:
        """Even if every source fails, we still get a structured payload."""
        caldav = AsyncMock()
        caldav.get_events = AsyncMock(side_effect=Exception("fail"))
        personal = AsyncMock()
        personal.get_unread_emails = AsyncMock(side_effect=Exception("fail"))
        professional = AsyncMock()
        professional.get_unread_emails = AsyncMock(side_effect=Exception("fail"))
        weather = AsyncMock()
        weather.get_current = AsyncMock(side_effect=Exception("fail"))
        weather.get_forecast = AsyncMock(side_effect=Exception("fail"))
        weather.get_daily_summary = AsyncMock(side_effect=Exception("fail"))

        agent = _make_agent(
            caldav=caldav,
            gmail_personal=personal,
            gmail_professional=professional,
            weather=weather,
        )

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, side_effect=Exception("fail")), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, side_effect=Exception("fail")), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, side_effect=Exception("fail")), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, side_effect=Exception("fail")):

            data = await agent._fetch_all_data()

        # All 9 data keys + _unavailable_sources must be present with fallback values
        expected_keys = {"calendar", "emails", "weather", "health", "finance", "tasks", "github", "jobs", "system_health", "_unavailable_sources"}
        assert set(data.keys()) == expected_keys
        assert len(data["_unavailable_sources"]) > 0
        assert data["tasks"] == []
        assert data["github"] == []
        assert data["jobs"]["new_today"] == 0


# ---------------------------------------------------------------------------
# Tests: execute (BaseAgent interface)
# ---------------------------------------------------------------------------

class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_returns_agent_result(self) -> None:
        agent = _make_agent()
        _install_mock_session()  # for _store_briefing

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            result = await agent.execute(_context())

        assert result.success is True
        assert result.content_type == "briefing"
        assert result.has_side_effects is False
        assert "briefing_id" in result.data
        assert "payload" in result.data
        # Verify Section 7.2.1 payload keys
        payload = result.data["payload"]
        assert "greeting" in payload
        assert "weather" in payload
        assert "schedule" in payload
        assert "email_digest" in payload
        assert "system_health" in payload
        assert "health" in payload
        assert "finance" in payload
        assert "proactive_suggestions" in payload

    @pytest.mark.asyncio
    async def test_execute_with_gemini_composes_narrative(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Good morning, Tasin. It looks like a beautiful day ahead."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 500
        mock_response.tokens_output = 200
        mock_response.duration_ms = 1500
        mock_gemini.generate = AsyncMock(return_value=mock_response)

        ctx = AgentContext(
            user_message="/briefing",
            intent_type="briefing",
            source="ios",
            config={"gemini_client": mock_gemini},
        )

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            result = await agent.execute(ctx)

        assert result.success is True
        assert "Good morning, Tasin" in result.text
        mock_gemini.generate.assert_awaited_once()

        # Verify Gemini was called with the right model
        call_kwargs = mock_gemini.generate.call_args
        assert call_kwargs.kwargs["model"] == "gemini-2.0-pro"

    @pytest.mark.asyncio
    async def test_execute_fallback_narrative_when_gemini_unavailable(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            result = await agent.execute(_context())

        assert result.success is True
        assert "Good morning, Tasin" in result.text
        assert "trouble composing" in result.text

    @pytest.mark.asyncio
    async def test_execute_fallback_narrative_when_gemini_fails(self) -> None:
        agent = _make_agent()
        _install_mock_session()

        mock_gemini = MagicMock()
        mock_gemini.generate = AsyncMock(side_effect=RuntimeError("API down"))

        ctx = AgentContext(
            user_message="/briefing",
            intent_type="briefing",
            source="ios",
            config={"gemini_client": mock_gemini},
        )

        with patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}), \
             patch.object(agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}), \
             patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}):

            result = await agent.execute(ctx)

        assert result.success is True
        assert "trouble composing" in result.text

    @pytest.mark.asyncio
    async def test_agent_type_is_briefing(self) -> None:
        agent = _make_agent()
        assert agent.agent_type == "briefing"


# ---------------------------------------------------------------------------
# Tests: _build_payload
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def _raw_data(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "calendar": {"today": [], "tomorrow": [], "date": "2026-03-10"},
            "emails": {"personal": [], "professional": [], "total_unread": 0},
            "weather": {
                "current": {"temp_f": 65, "conditions": "clear"},
                "forecast": [],
                "summary": {"summary": "65°F, clear", "needs_umbrella": False, "suggestion": "Great day!"},
            },
            "health": {},
            "finance": {},
            "tasks": [],
            "github": [],
            "jobs": {"new_today": 0, "listings": []},
            "system_health": {"status": "green", "targets": {}},
        }
        base.update(overrides)
        return base

    def test_produces_all_required_keys(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())

        required_keys = {
            "greeting", "date", "weather", "outfit", "schedule",
            "leave_home_by", "commute_note", "email_digest",
            "system_health", "tasks_due_today", "job_matches",
            "health", "finance", "proactive_suggestions",
            "unavailability_note",
        }
        assert set(payload.keys()) == required_keys

    def test_greeting(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())
        assert payload["greeting"] == "Good morning, Tasin."

    def test_weather_section(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())
        assert payload["weather"]["summary"] == "65°F, clear"
        assert payload["weather"]["needs_umbrella"] is False

    def test_schedule_from_events(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(calendar={
            "today": [
                {"start": "09:00", "title": "Standup", "location": "Office"},
                {"start": "14:00", "title": "1:1 with Manager", "location": "Zoom"},
            ],
            "tomorrow": [],
            "date": "2026-03-10",
        })
        payload = agent._build_payload(raw)

        assert len(payload["schedule"]) == 2
        assert payload["schedule"][0]["event"] == "Standup"
        assert payload["schedule"][0]["time"] == "09:00"
        assert payload["schedule"][1]["location"] == "Zoom"

    def test_leave_home_by_computed(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(calendar={
            "today": [{"start": "09:00", "title": "Meeting", "location": "Office"}],
            "tomorrow": [],
            "date": "2026-03-10",
        })
        payload = agent._build_payload(raw)
        assert payload["leave_home_by"] is not None
        assert "8:35 AM" in payload["leave_home_by"]

    def test_leave_home_by_none_when_no_events(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())
        assert payload["leave_home_by"] is None

    def test_email_digest_with_classifications(self) -> None:
        agent = _make_agent()
        classifications = [
            {"classified_tier": "urgent", "from_name": "Prof. Sadigh", "subject": "Deadline", "snippet": "..."},
            {"classified_tier": "actionable", "from_name": "HR", "subject": "Onboarding", "snippet": "..."},
            {"classified_tier": "informational", "from_address": "news@co.com", "subject": "Update", "snippet": "..."},
            {"classified_tier": "noise", "from_address": "spam@co.com", "subject": "Sale!", "snippet": "..."},
        ]
        payload = agent._build_payload(self._raw_data(), classifications)

        digest = payload["email_digest"]
        assert len(digest["urgent"]) == 1
        assert digest["urgent"][0]["from"] == "Prof. Sadigh"
        assert len(digest["actionable"]) == 1
        assert digest["informational_count"] == 1
        assert digest["noise_filtered_count"] == 1

    def test_email_digest_without_classifications(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(emails={"personal": [], "professional": [], "total_unread": 15})
        payload = agent._build_payload(raw)

        digest = payload["email_digest"]
        assert digest["informational_count"] == 15
        assert digest["urgent"] == []

    def test_system_health_green(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(system_health={"status": "green", "targets": {"postgres": {"status": "green"}}})
        payload = agent._build_payload(raw)

        assert payload["system_health"]["status"] == "green"
        assert payload["system_health"]["details"] == "All services operational."

    def test_system_health_degraded(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(system_health={
            "status": "degraded",
            "targets": {"postgres": {"status": "green"}, "redis": {"status": "red"}},
        })
        payload = agent._build_payload(raw)

        assert payload["system_health"]["status"] == "degraded"
        assert "redis" in payload["system_health"]["details"]

    def test_job_matches_with_listings(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(jobs={
            "new_today": 3,
            "listings": [
                {"title": "ML Engineer", "company": "Scale AI", "match_score": 92},
                {"title": "SWE", "company": "Google", "match_score": 85},
            ],
        })
        payload = agent._build_payload(raw)

        assert payload["job_matches"]["new_today"] == 3
        assert "ML Engineer @ Scale AI" in payload["job_matches"]["top_match"]
        assert "92% match" in payload["job_matches"]["top_match"]

    def test_job_matches_empty(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())

        assert payload["job_matches"]["new_today"] == 0
        assert payload["job_matches"]["top_match"] is None

    def test_health_section_with_data(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(health={
            "sleep": {"value": 7.5, "unit": "hours"},
            "steps": {"value": 8500, "unit": "count"},
        })
        payload = agent._build_payload(raw)

        assert "7.5 hours" in payload["health"]["sleep"]
        assert "good" in payload["health"]["sleep"]
        assert payload["health"]["steps_yesterday"] == 8500

    def test_health_section_empty(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())

        assert "No health data" in payload["health"]["note"]

    def test_finance_section_with_data(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(finance={
            "yesterday_transactions": [
                {"merchant": "Starbucks", "amount": 5.50, "category": "Food"},
            ],
            "yesterday_total": 5.50,
            "month_to_date": 1247.00,
        })
        payload = agent._build_payload(raw)

        assert "$5.50" in payload["finance"]["yesterday_spending"]
        assert "1 transaction" in payload["finance"]["yesterday_spending"]
        assert "$1247.00" in payload["finance"]["month_to_date"]

    def test_proactive_suggestions_umbrella(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(weather={
            "current": {"temp_f": 55, "conditions": "rain"},
            "forecast": [],
            "summary": {"summary": "Rainy", "needs_umbrella": True, "suggestion": "Bring rain gear."},
        })
        payload = agent._build_payload(raw)

        suggestions = payload["proactive_suggestions"]
        assert any("umbrella" in s.lower() for s in suggestions)

    def test_outfit_is_placeholder(self) -> None:
        agent = _make_agent()
        payload = agent._build_payload(self._raw_data())

        assert payload["outfit"]["suggestion"] is None
        assert payload["outfit"]["image_refs"] == []

    def test_commute_note_for_out_of_town(self) -> None:
        agent = _make_agent()
        raw = self._raw_data(calendar={
            "today": [{"start": "11:00", "title": "Cleveland appointment", "location": "Cleveland, OH"}],
            "tomorrow": [],
            "date": "2026-03-10",
        })
        payload = agent._build_payload(raw)

        assert payload["commute_note"] is not None
        assert "Cleveland" in payload["commute_note"]


# ---------------------------------------------------------------------------
# Tests: _compose_narrative
# ---------------------------------------------------------------------------

class TestComposeNarrative:
    @pytest.mark.asyncio
    async def test_returns_gemini_text(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Good morning, Tasin. You have a packed schedule today."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 400
        mock_response.tokens_output = 150
        mock_response.duration_ms = 1200
        mock_gemini.generate = AsyncMock(return_value=mock_response)

        payload = {"greeting": "Good morning, Tasin.", "weather": {"summary": "Sunny"}}
        sections = [{"name": "weather", "priority": 1, "enabled": True}]

        result = await agent._compose_narrative(payload, sections, mock_gemini)

        assert "Good morning, Tasin" in result
        mock_gemini.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sections_ordered_by_priority(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Narrative here."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 100
        mock_response.tokens_output = 50
        mock_response.duration_ms = 500
        mock_gemini.generate = AsyncMock(return_value=mock_response)

        payload = {"greeting": "Hi", "weather": {}, "health": {}, "email_digest": {}}
        sections = [
            {"name": "health_summary", "priority": 3, "enabled": True},
            {"name": "weather", "priority": 1, "enabled": True},
            {"name": "email_digest", "priority": 2, "enabled": True},
        ]

        await agent._compose_narrative(payload, sections, mock_gemini)

        prompt = mock_gemini.generate.call_args.kwargs["prompt"]
        # Weather should come before health in the section ordering
        assert "weather" in prompt
        weather_pos = prompt.index("weather")
        health_pos = prompt.index("health_summary")
        assert weather_pos < health_pos

    @pytest.mark.asyncio
    async def test_disabled_sections_excluded(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Narrative."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 100
        mock_response.tokens_output = 50
        mock_response.duration_ms = 500
        mock_gemini.generate = AsyncMock(return_value=mock_response)

        payload = {"greeting": "Hi", "weather": {}, "finance": {"total": 100}}
        sections = [
            {"name": "weather", "priority": 1, "enabled": True},
            {"name": "finance_summary", "priority": 9, "enabled": False},
        ]

        await agent._compose_narrative(payload, sections, mock_gemini)

        prompt = mock_gemini.generate.call_args.kwargs["prompt"]
        assert "finance_summary" not in prompt

    @pytest.mark.asyncio
    async def test_returns_fallback_when_no_gemini(self) -> None:
        agent = _make_agent()
        result = await agent._compose_narrative({}, [], None)
        assert "Good morning, Tasin" in result
        assert "trouble composing" in result

    @pytest.mark.asyncio
    async def test_returns_fallback_on_gemini_error(self) -> None:
        agent = _make_agent()
        mock_gemini = MagicMock()
        mock_gemini.generate = AsyncMock(side_effect=Exception("API error"))

        result = await agent._compose_narrative({}, [], mock_gemini)
        assert "trouble composing" in result


# ---------------------------------------------------------------------------
# Tests: _store_briefing
# ---------------------------------------------------------------------------

class TestStoreBriefing:
    @pytest.mark.asyncio
    async def test_stores_and_returns_uuid(self) -> None:
        agent = _make_agent()
        session = _install_mock_session()

        payload = {"greeting": "Good morning"}
        narrative = "Good morning, Tasin."

        briefing_id = await agent._store_briefing(payload, narrative)

        assert briefing_id is not None
        # Verify session.add was called
        assert session.add.called


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_build_schedule_from_events(self) -> None:
        from agents.briefing import _build_schedule

        events = [
            {"start": "09:00", "title": "Meeting", "location": "Room A"},
            {"start": "14:00", "summary": "Review", "location": ""},
        ]
        schedule = _build_schedule(events)
        assert len(schedule) == 2
        assert schedule[0]["event"] == "Meeting"
        assert schedule[1]["event"] == "Review"

    def test_build_email_digest_with_tiers(self) -> None:
        from agents.briefing import _build_email_digest

        classifications = [
            {"classified_tier": "urgent", "from_name": "Boss", "subject": "Now!", "snippet": ""},
            {"classified_tier": "noise", "from_address": "spam", "subject": "Sale", "snippet": ""},
        ]
        digest = _build_email_digest({}, classifications)
        assert len(digest["urgent"]) == 1
        assert digest["noise_filtered_count"] == 1

    def test_summarize_system_health_green(self) -> None:
        from agents.briefing import _summarize_system_health
        assert _summarize_system_health({"status": "green"}) == "All services operational."

    def test_summarize_system_health_degraded(self) -> None:
        from agents.briefing import _summarize_system_health
        result = _summarize_system_health({
            "status": "degraded",
            "targets": {"redis": {"status": "red"}, "pg": {"status": "green"}},
        })
        assert "redis" in result

    def test_filter_payload_for_prompt(self) -> None:
        from agents.briefing import _filter_payload_for_prompt

        payload = {
            "greeting": "Hi",
            "date": "2026-03-10",
            "weather": {"temp": 65},
            "schedule": [{"event": "Meeting"}],
            "health": {"sleep": "7h"},
            "finance": {"mtd": 500},
        }
        filtered = _filter_payload_for_prompt(payload, ["weather", "health_summary"])
        assert "weather" in filtered
        assert "health" in filtered
        assert "schedule" not in filtered
        assert "finance" not in filtered

    def test_build_proactive_suggestions_workout_reminder(self) -> None:
        from agents.briefing import _build_proactive_suggestions

        weather = {"needs_umbrella": False, "suggestion": ""}
        health = {"last_workout": {"value": 3, "unit": "days"}}
        suggestions = _build_proactive_suggestions(weather, health, {}, [])
        assert any("worked out" in s.lower() or "exercise" in s.lower() for s in suggestions)
