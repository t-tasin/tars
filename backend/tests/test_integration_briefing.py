"""Integration tests for the complete morning briefing pipeline.

Tests the full flow: /briefing command → intent classification → model routing →
context building → BriefingAgent execution (data fetch → payload build →
narrative composition → DB storage) → usage tracking → response formatting.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import db.session as _db_session_mod  # already replaced by conftest
from agents.base import AgentContext
from agents.briefing import BriefingAgent
from db.models import Briefing, ModelUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_session_factory(session: Any):
    """Return an async context manager that yields the given test session.

    Mirrors the real get_db_session behavior: commit on clean exit.
    """

    @asynccontextmanager
    async def _get_db_session():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return _get_db_session


def _mock_settings() -> MagicMock:
    """Build a Settings mock with all fields needed by BriefingAgent + Orchestrator."""
    s = MagicMock()
    s.icloud_caldav_user = "test@icloud.com"
    s.icloud_caldav_password = "test-password"
    s.gmail_personal_credentials = "dGVzdA=="  # base64("test")
    s.gmail_professional_credentials = "dGVzdA=="
    s.openweathermap_api_key = "test-key"
    s.default_location = "Wooster,OH,US"
    s.gemini_api_key = "test-gemini-key"
    s.database_url = "sqlite+aiosqlite://"
    s.debug = False
    # Test the legacy intent-based router (briefing → gemini_pro). The signal-aware
    # router would default briefing → local_brain, which is covered elsewhere.
    s.feature_new_router = False
    return s


def _build_briefing_agent(
    mock_caldav: AsyncMock,
    mock_gmail: AsyncMock,
    mock_weather: AsyncMock,
) -> BriefingAgent:
    """Construct a BriefingAgent with all integration clients mocked."""
    settings = _mock_settings()
    with (
        patch("config.get_settings", return_value=settings),
        patch("integrations.caldav_client.CalDAVClient", return_value=mock_caldav),
        patch("integrations.gmail_client.GmailClient", return_value=mock_gmail),
        patch("integrations.weather_client.WeatherClient", return_value=mock_weather),
    ):
        agent = BriefingAgent()

    agent._caldav = mock_caldav
    agent._gmail_personal = mock_gmail
    agent._gmail_professional = mock_gmail
    agent._weather = mock_weather
    # Force the Gemini fallback path: tests verify the cloud-fallback contract,
    # not the live LOCAL_BRAIN call. _compose_with_local raises when None.
    agent._local_client = None
    return agent


# ---------------------------------------------------------------------------
# 1. BriefingAgent integration: full execute() flow
# ---------------------------------------------------------------------------


class TestBriefingAgentPipeline:
    """Test the BriefingAgent.execute() pipeline with mocked externals."""

    async def test_full_briefing_pipeline(
        self,
        test_db: Any,
        mock_gemini: AsyncMock,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """Seed config, mock externals, run execute(), verify stored briefing."""
        agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)

        with patch.object(_db_session_mod, "get_db_session", _db_session_factory(test_db)):
            context = AgentContext(
                user_message="/briefing",
                intent_type="briefing",
                source="test",
                config={"gemini_client": mock_gemini},
            )
            result = await agent.execute(context)

        # 1. Result is successful
        assert result.success is True
        assert result.content_type == "briefing"
        assert result.error is None

        # 2. Narrative is a non-empty string
        assert isinstance(result.text, str)
        assert len(result.text) > 0

        # 3. Data contains briefing_id and payload
        assert "briefing_id" in result.data
        assert "payload" in result.data
        briefing_id = result.data["briefing_id"]
        assert briefing_id  # non-empty UUID string

        # 4. Payload contains all expected sections
        payload = result.data["payload"]
        expected_sections = [
            "greeting",
            "date",
            "weather",
            "outfit",
            "schedule",
            "email_digest",
            "system_health",
            "tasks_due_today",
            "job_matches",
            "health",
            "finance",
            "proactive_suggestions",
        ]
        for section in expected_sections:
            assert section in payload, f"Missing section: {section}"

        # 5. Weather section has correct data from mock
        assert payload["weather"]["needs_umbrella"] is False

        # 6. Schedule has 3 events from mock CalDAV
        assert len(payload["schedule"]) == 3

        # 7. Email digest reflects the mocked emails
        assert isinstance(payload["email_digest"], dict)
        assert "informational_count" in payload["email_digest"]

        # 8. Briefing was stored (in-memory or real DB)
        stored = await test_db.execute(_select_all(Briefing))
        briefing_rows = stored.scalars().all()
        assert len(briefing_rows) >= 1
        briefing_row = briefing_rows[0]
        assert briefing_row.briefing_type == "morning"
        assert briefing_row.briefing_date == date.today()
        assert briefing_row.narrative == result.text

        # 9. Gemini was called for narrative composition
        mock_gemini.generate.assert_called_once()

    async def test_briefing_graceful_degradation_no_gemini(
        self,
        test_db: Any,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """Briefing uses fallback narrative when no Gemini client provided (HC-09)."""
        agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)

        with patch.object(_db_session_mod, "get_db_session", _db_session_factory(test_db)):
            context = AgentContext(
                user_message="/briefing",
                intent_type="briefing",
                source="test",
                config={},  # No gemini_client → fallback
            )
            result = await agent.execute(context)

        assert result.success is True
        assert "trouble" in result.text.lower() or "good morning" in result.text.lower()

    async def test_briefing_handles_fetch_failure_gracefully(
        self,
        test_db: Any,
        mock_gemini: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """If CalDAV fails, the briefing still succeeds with empty calendar."""
        failing_caldav = AsyncMock()
        failing_caldav.get_events = AsyncMock(side_effect=ConnectionError("CalDAV down"))

        agent = _build_briefing_agent(failing_caldav, mock_gmail, mock_weather)
        agent._caldav = failing_caldav

        with patch.object(_db_session_mod, "get_db_session", _db_session_factory(test_db)):
            context = AgentContext(
                user_message="/briefing",
                intent_type="briefing",
                source="test",
                config={"gemini_client": mock_gemini},
            )
            result = await agent.execute(context)

        assert result.success is True
        payload = result.data["payload"]
        assert payload["schedule"] == []


# ---------------------------------------------------------------------------
# 2. Orchestrator-level integration: /briefing → full pipeline
# ---------------------------------------------------------------------------


class TestOrchestratorBriefingPipeline:
    """Test the orchestrator routing /briefing through the full pipeline."""

    async def test_orchestrator_routes_briefing_command(
        self,
        test_db: Any,
        mock_gemini: AsyncMock,
        mock_claude: AsyncMock,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """The orchestrator routes /briefing to the briefing agent,
        tracks usage, and returns a formatted response."""
        settings = _mock_settings()

        # Clear lru_cache so our mock takes effect
        import config as config_mod

        config_mod.get_settings.cache_clear()

        with patch("config.get_settings", return_value=settings):
            from orchestrator.engine import Orchestrator

            orch = Orchestrator()

        orch.gemini_client = mock_gemini
        orch.claude_spawner = mock_claude

        briefing_agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)
        orch.register_agent(briefing_agent)

        db_factory = _db_session_factory(test_db)
        with (
            patch.object(_db_session_mod, "get_db_session", db_factory),
            patch("orchestrator.engine.get_db_session", db_factory),
        ):
            response = await orch.process_message(
                text="/briefing",
                source="ios",
            )

        # Restore cache
        config_mod.get_settings.cache_clear()

        # 1. Response structure
        assert "conversation_id" in response
        assert "message_id" in response
        assert response["agent_used"] == "briefing"
        assert response["model_used"] == "gemini_pro"

        # 2. Response contains briefing text
        resp_payload = response.get("response", {})
        assert resp_payload.get("content_type") == "briefing"
        assert len(resp_payload.get("text", "")) > 0

        # 3. No approval needed (briefing is read-only)
        assert resp_payload.get("approval") is None

        # 4. Usage was tracked
        usage_result = await test_db.execute(_select_all(ModelUsage))
        usage_rows = usage_result.scalars().all()
        assert len(usage_rows) >= 1
        usage = usage_rows[0]
        assert usage.agent_type == "briefing"
        assert usage.model == "gemini_pro"
        assert usage.success is True

        # 5. Briefing was stored
        briefing_result = await test_db.execute(_select_all(Briefing))
        briefings = briefing_result.scalars().all()
        assert len(briefings) == 1
        assert briefings[0].briefing_type == "morning"
        assert briefings[0].narrative  # non-empty

    async def test_intent_classifier_routes_briefing(self) -> None:
        """Verify /briefing is classified as the briefing intent."""
        from shared.constants import IntentType

        from orchestrator.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        intent = classifier.classify("/briefing", source="ios")
        assert intent.agent == IntentType.BRIEFING

    async def test_model_router_routes_briefing_to_gemini_pro(self) -> None:
        """Verify briefing intent routes to Gemini Pro on node1."""
        from shared.constants import IntentType, ModelName

        from orchestrator.intent_classifier import Intent
        from orchestrator.model_router import ModelRouter

        router = ModelRouter()
        intent = Intent(agent=IntentType.BRIEFING)
        route = router.route(intent)

        assert route.model == ModelName.GEMINI_PRO
        assert route.node == "node1"
        assert route.mcp_profile is None


# ---------------------------------------------------------------------------
# 3. Payload structure validation
# ---------------------------------------------------------------------------


class TestBriefingPayloadStructure:
    """Validate the payload built by _build_payload."""

    async def test_payload_sections_complete(
        self,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """The payload should contain all 7.2.1 sections."""
        agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)

        raw_data = await agent._fetch_all_data()
        payload = agent._build_payload(raw_data)

        assert payload["greeting"].startswith("Good morning")
        assert payload["date"] == date.today().isoformat()

        required_keys = [
            "weather",
            "outfit",
            "schedule",
            "leave_home_by",
            "email_digest",
            "system_health",
            "tasks_due_today",
            "job_matches",
            "health",
            "finance",
            "proactive_suggestions",
        ]
        for key in required_keys:
            assert key in payload, f"Missing key: {key}"

    async def test_payload_weather_from_mock(
        self,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """Weather section should reflect mock data."""
        agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)

        raw_data = await agent._fetch_all_data()
        payload = agent._build_payload(raw_data)

        weather = payload["weather"]
        assert weather["needs_umbrella"] is False
        summary_lower = weather["summary"].lower()
        assert "no rain" in summary_lower or "clear" in summary_lower

    async def test_payload_schedule_from_mock(
        self,
        mock_caldav: AsyncMock,
        mock_gmail: AsyncMock,
        mock_weather: AsyncMock,
    ) -> None:
        """Schedule section should contain the 3 mocked events."""
        agent = _build_briefing_agent(mock_caldav, mock_gmail, mock_weather)

        raw_data = await agent._fetch_all_data()
        payload = agent._build_payload(raw_data)

        schedule = payload["schedule"]
        assert len(schedule) == 3
        assert schedule[0]["event"] == "Team Standup"
        assert schedule[1]["event"] == "Lunch with Advisor"
        assert schedule[2]["event"] == "Office Hours"


# ---------------------------------------------------------------------------
# Query helpers (work with both real DB and in-memory session)
# ---------------------------------------------------------------------------


def _select_briefing_by_id(briefing_id: str) -> Any:
    from sqlalchemy import select

    return select(Briefing).where(Briefing.id == uuid.UUID(briefing_id))


def _select_all(model_class: type) -> Any:
    from sqlalchemy import select

    return select(model_class)
