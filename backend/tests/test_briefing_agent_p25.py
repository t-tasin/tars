"""Tests for BriefingAgent P2.5-02 rewrite: LOCAL_BRAIN composition + truncation."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import ModelName

from agents.base import AgentContext
from models.local_client import LocalResponse

# Reference the fake db.session installed by conftest.
_fake_db_session_module = sys.modules["db.session"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_response(text: str = "Good morning, Tasin! Here is your briefing.") -> LocalResponse:
    return LocalResponse(
        text=text,
        reasoning=None,
        model="qwen3-8b-brain",
        tokens_input=300,
        tokens_output=200,
        duration_ms=800,
        finish_reason="stop",
    )


def _make_agent(*, local_client: Any = None) -> Any:
    """Create BriefingAgent with mocked clients, bypassing __init__."""
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    agent._caldav = _mock_caldav()
    agent._gmail_personal = _mock_gmail(prefix="personal")
    agent._gmail_professional = _mock_gmail(prefix="professional")
    agent._weather = _mock_weather()
    agent._notion = AsyncMock()
    agent._notion_tasks_db = None
    agent._local_client = local_client or _mock_local_client()
    return agent


def _mock_local_client(text: str = "Good morning, Tasin! Here is your briefing.") -> MagicMock:
    client = MagicMock()
    client.generate = AsyncMock(return_value=_local_response(text))
    return client


def _mock_caldav(events: list[dict[str, Any]] | None = None) -> MagicMock:
    client = MagicMock()
    default = [{"title": f"Event {i}", "start": "2026-04-25T09:00:00", "calendar": "Work"} for i in range(8)]
    client.get_events = AsyncMock(return_value=events if events is not None else default[:3])
    client.get_today_events = AsyncMock(return_value=events if events is not None else default[:3])
    return client


def _mock_gmail(prefix: str = "personal", count: int = 3) -> MagicMock:
    client = MagicMock()
    emails = [{"subject": f"{prefix} email {i}", "from": f"sender{i}@example.com"} for i in range(count)]
    client.get_unread_emails = AsyncMock(return_value=emails)
    return client


def _mock_weather() -> MagicMock:
    client = MagicMock()
    client.get_current = AsyncMock(return_value={"temp_f": 72.0, "description": "Sunny"})
    client.get_forecast = AsyncMock(return_value=[])
    client.get_daily_summary = AsyncMock(return_value={"summary": "Nice day", "needs_umbrella": False})
    return client


def _install_mock_session() -> None:
    """Install a mock async session context manager into the fake db.session module."""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    _fake_db_session_module.get_db_session = MagicMock(return_value=mock_session)


# ---------------------------------------------------------------------------
# Test: _build_local_context
# ---------------------------------------------------------------------------


def test_build_local_context_includes_weather() -> None:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    raw_data = {
        "calendar": {"today": [], "tomorrow": []},
        "emails": {"personal": [], "professional": []},
        "weather": {"current": {"temp_f": 72}, "summary": {"summary": "Sunny"}},
        "tasks": [],
    }
    ctx = agent._build_local_context(raw_data)
    assert "weather" in ctx
    assert ctx["weather"]["temp_f"] == 72


def test_build_local_context_truncates_events_to_five() -> None:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    events = [{"title": f"Event {i}"} for i in range(8)]
    raw_data = {
        "calendar": {"today": events, "tomorrow": []},
        "emails": {"personal": [], "professional": []},
        "weather": {},
        "tasks": [],
    }
    ctx = agent._build_local_context(raw_data)
    assert len(ctx["schedule_today"]) == 5


def test_build_local_context_truncates_emails_to_five_per_account() -> None:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    emails = [{"subject": f"email {i}"} for i in range(12)]
    raw_data = {
        "calendar": {"today": [], "tomorrow": []},
        "emails": {"personal": emails, "professional": emails},
        "weather": {},
        "tasks": [],
    }
    ctx = agent._build_local_context(raw_data)
    assert len(ctx["emails_personal"]) == 5
    assert len(ctx["emails_professional"]) == 5


def test_build_local_context_truncates_tasks_to_five() -> None:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    tasks = [{"title": f"Task {i}"} for i in range(8)]
    raw_data = {
        "calendar": {"today": [], "tomorrow": []},
        "emails": {"personal": [], "professional": []},
        "weather": {},
        "tasks": tasks,
    }
    ctx = agent._build_local_context(raw_data)
    assert len(ctx["notion_tasks"]) == 5


# ---------------------------------------------------------------------------
# Test: _compose_with_local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_with_local_calls_local_brain() -> None:
    agent = _make_agent()
    context_dict = {"weather": {"temp_f": 72}, "schedule_today": [], "emails_personal": []}

    result = await agent._compose_with_local(context_dict)

    agent._local_client.generate.assert_awaited_once()
    call_kwargs = agent._local_client.generate.call_args
    model_used = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") or call_kwargs[0][0]
    assert model_used == ModelName.LOCAL_BRAIN
    assert result == "Good morning, Tasin! Here is your briefing."


@pytest.mark.asyncio
async def test_compose_with_local_raises_when_no_client() -> None:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    # No _local_client set

    with pytest.raises(Exception):
        await agent._compose_with_local({})


@pytest.mark.asyncio
async def test_compose_with_local_disables_thinking_and_caps_tokens() -> None:
    """Briefing must call LOCAL_BRAIN with enable_thinking=False so reasoning_content
    does not consume the max_tokens budget and leave content empty."""
    agent = _make_agent()

    await agent._compose_with_local({"weather": {"temp_f": 72}})

    kwargs = agent._local_client.generate.call_args.kwargs
    assert kwargs.get("enable_thinking") is False
    assert kwargs.get("max_tokens") == 700


@pytest.mark.asyncio
async def test_compose_with_local_raises_on_empty_content() -> None:
    """If LOCAL_BRAIN returns empty content (e.g. all output went to reasoning_content),
    raise so the Gemini fallback path is taken instead of returning an empty narrative."""
    agent = _make_agent(local_client=_mock_local_client(text=""))

    with pytest.raises(RuntimeError, match="empty content"):
        await agent._compose_with_local({"weather": {"temp_f": 72}})


# ---------------------------------------------------------------------------
# Test: execute() uses LOCAL_BRAIN for composition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_uses_local_brain_for_narrative() -> None:
    agent = _make_agent()
    _install_mock_session()

    with (
        patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}),
        patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}),
        patch.object(
            agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}
        ),
        patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}),
    ):
        ctx = AgentContext(user_message="/briefing", intent_type="briefing", source="telegram")
        result = await agent.execute(ctx)

    assert result.success is True
    assert "Good morning, Tasin" in result.text
    agent._local_client.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_falls_back_to_gemini_when_local_fails() -> None:
    agent = _make_agent()
    agent._local_client.generate = AsyncMock(side_effect=RuntimeError("local down"))
    _install_mock_session()

    mock_gemini = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Good morning, Tasin. Gemini fallback."
    mock_resp.model = "gemini-2.5-pro"
    mock_resp.tokens_input = 300
    mock_resp.tokens_output = 150
    mock_resp.duration_ms = 1000
    mock_gemini.generate = AsyncMock(return_value=mock_resp)

    ctx = AgentContext(
        user_message="/briefing",
        intent_type="briefing",
        source="telegram",
        config={"gemini_client": mock_gemini},
    )

    with (
        patch.object(agent, "_fetch_health_data", new_callable=AsyncMock, return_value={}),
        patch.object(agent, "_fetch_finance_data", new_callable=AsyncMock, return_value={}),
        patch.object(
            agent, "_fetch_job_matches", new_callable=AsyncMock, return_value={"new_today": 0, "listings": []}
        ),
        patch.object(agent, "_fetch_system_health", new_callable=AsyncMock, return_value={"status": "green"}),
    ):
        result = await agent.execute(ctx)

    assert result.success is True
    assert "Gemini fallback" in result.text
    mock_gemini.generate.assert_awaited_once()
