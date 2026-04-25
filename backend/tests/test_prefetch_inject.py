"""Tests for engine pre-fetch context injection into local tier (P2.5-04)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from shared.constants import ModelName

from agents.base import AgentContext
from models.local_client import LocalResponse
from orchestrator.engine import Orchestrator
from orchestrator.model_router import ModelRoute


def _local_response(text: str = "ok") -> LocalResponse:
    return LocalResponse(
        text=text,
        reasoning=None,
        model="qwen3-8b-brain",
        tokens_input=20,
        tokens_output=10,
        duration_ms=300,
        finish_reason="stop",
    )


@pytest.fixture
def orch():
    with (
        patch("orchestrator.engine.GeminiClient"),
        patch("orchestrator.engine.ClaudeCodeSpawner"),
        patch("orchestrator.engine.LocalClient"),
        patch("orchestrator.engine.ContextBuilder"),
    ):
        o = Orchestrator()
    o.local_client = AsyncMock()
    o.gemini_client = AsyncMock()
    o.claude_spawner = AsyncMock()
    return o


# ---------------------------------------------------------------------------
# Test: _inject_system_context helper
# ---------------------------------------------------------------------------


def test_inject_returns_plain_message_when_no_context(orch: Orchestrator) -> None:
    ctx = AgentContext(user_message="What's up?", intent_type="general")
    result = orch._inject_system_context(ctx)
    assert result == "What's up?"


def test_inject_prepends_context_block_when_present(orch: Orchestrator) -> None:
    ctx = AgentContext(
        user_message="What's the weather?",
        intent_type="general",
        system_context={"weather": {"temp": 72, "description": "Sunny"}},
    )
    result = orch._inject_system_context(ctx)
    assert "[CONTEXT]" in result
    assert "[/CONTEXT]" in result
    assert "Sunny" in result
    assert result.endswith("What's the weather?")


def test_inject_context_is_valid_json(orch: Orchestrator) -> None:
    import json

    ctx = AgentContext(
        user_message="test",
        intent_type="general",
        system_context={"weather": {"temp": 72}, "schedule": []},
    )
    result = orch._inject_system_context(ctx)
    between = result.split("[CONTEXT]\n")[1].split("\n[/CONTEXT]")[0]
    parsed = json.loads(between)
    assert parsed["weather"]["temp"] == 72


# ---------------------------------------------------------------------------
# Test: _local_call passes injected prompt to generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_call_injects_context_into_prompt(orch: Orchestrator) -> None:
    route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
    ctx = AgentContext(
        user_message="What's the weather?",
        intent_type="general",
        system_context={"weather": {"temp": 65, "description": "Rainy"}},
    )
    orch.local_client.generate = AsyncMock(return_value=_local_response("It's rainy."))

    result = await orch._local_call(route, ctx)

    assert result.success
    call_kwargs = orch.local_client.generate.call_args
    prompt_sent = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt") or call_kwargs[0][1]
    assert "[CONTEXT]" in prompt_sent
    assert "Rainy" in prompt_sent


@pytest.mark.asyncio
async def test_local_call_plain_prompt_when_no_context(orch: Orchestrator) -> None:
    route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
    ctx = AgentContext(user_message="Hello?", intent_type="general")
    orch.local_client.generate = AsyncMock(return_value=_local_response("Hi!"))

    await orch._local_call(route, ctx)

    call_kwargs = orch.local_client.generate.call_args
    prompt_sent = call_kwargs.kwargs.get("prompt") or call_kwargs[1].get("prompt") or call_kwargs[0][1]
    assert prompt_sent == "Hello?"
    assert "[CONTEXT]" not in prompt_sent
