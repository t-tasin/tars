"""P2-11: local → gemini → claude fallback chain in the engine.

Tests _execute_with_fallback directly for LOCAL_REFLEX / LOCAL_BRAIN routes.
Existing cloud fallback chain (claude→gemini, gemini→claude) is also regression-tested.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import ModelName

from agents.base import AgentContext
from models.local_client import LocalResponse
from orchestrator.engine import Orchestrator
from orchestrator.model_router import ModelRoute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(msg: str = "hello") -> AgentContext:
    return AgentContext(user_message=msg, intent_type="general")


def _local_ok(text: str = "local ok", model: ModelName = ModelName.LOCAL_REFLEX) -> LocalResponse:
    alias = "qwen3-1.7b-reflex" if model == ModelName.LOCAL_REFLEX else "qwen3-8b-brain"
    return LocalResponse(
        text=text,
        reasoning=None,
        model=alias,
        tokens_input=5,
        tokens_output=3,
        duration_ms=100,
        finish_reason="stop",
    )


def _gemini_ok(text: str = "gemini ok") -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tokens_input = 10
    r.tokens_output = 5
    r.duration_ms = 200
    return r


def _claude_ok(text: str = "claude ok") -> MagicMock:
    r = MagicMock()
    r.success = True
    r.text = text
    r.error = None
    r.cost_usd = 0.001
    r.duration_ms = 500
    r.num_turns = 1
    return r


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def orch():
    """Orchestrator with all external clients replaced by AsyncMocks."""
    with (
        patch("orchestrator.engine.GeminiClient"),
        patch("orchestrator.engine.ClaudeCodeSpawner"),
        patch("orchestrator.engine.LocalClient"),
        patch("orchestrator.engine.ContextBuilder"),
    ):
        o = Orchestrator()
    # Replace with controllable AsyncMocks
    o.local_client = AsyncMock()
    o.gemini_client = AsyncMock()
    o.claude_spawner = AsyncMock()
    return o


# ---------------------------------------------------------------------------
# LOCAL_REFLEX happy path
# ---------------------------------------------------------------------------


class TestLocalTierPrimarySuccess:
    @pytest.mark.asyncio
    async def test_local_reflex_success_returns_local_text(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(return_value=_local_ok("fast answer"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "fast answer"
        orch.gemini_client.generate.assert_not_called()
        orch.claude_spawner.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_brain_success_returns_local_text(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(return_value=_local_ok("deep answer", ModelName.LOCAL_BRAIN))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "deep answer"
        orch.gemini_client.generate.assert_not_called()
        orch.claude_spawner.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_reflex_calls_generate_with_reflex_model(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(return_value=_local_ok())

        await orch._execute_with_fallback(route, _ctx("ping"))

        call_args = orch.local_client.generate.call_args
        model_arg = call_args.kwargs.get("model") or call_args.args[0]
        assert model_arg == ModelName.LOCAL_REFLEX

    @pytest.mark.asyncio
    async def test_local_brain_calls_generate_with_brain_model(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(return_value=_local_ok(model=ModelName.LOCAL_BRAIN))

        await orch._execute_with_fallback(route, _ctx("complex"))

        call_args = orch.local_client.generate.call_args
        model_arg = call_args.kwargs.get("model") or call_args.args[0]
        assert model_arg == ModelName.LOCAL_BRAIN


# ---------------------------------------------------------------------------
# LOCAL tier fallback: local fails → gemini
# ---------------------------------------------------------------------------


class TestLocalToGeminiFallback:
    @pytest.mark.asyncio
    async def test_local_reflex_fails_falls_back_to_gemini(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("connection refused"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("gemini saved it"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "gemini saved it"

    @pytest.mark.asyncio
    async def test_local_brain_fails_falls_back_to_gemini(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("timeout"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("gemini ok"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "gemini ok"

    @pytest.mark.asyncio
    async def test_gemini_fallback_records_fallback_from_model(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok())

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.data.get("fallback_from") == ModelName.LOCAL_REFLEX

    @pytest.mark.asyncio
    async def test_claude_not_called_when_gemini_fallback_succeeds(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok())

        await orch._execute_with_fallback(route, _ctx())

        orch.claude_spawner.execute.assert_not_called()


# ---------------------------------------------------------------------------
# LOCAL tier fallback: local + gemini fail → claude
# ---------------------------------------------------------------------------


class TestLocalToGeminiToClaudeFallback:
    @pytest.mark.asyncio
    async def test_local_and_gemini_fail_falls_back_to_claude(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("timeout"))
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("quota"))
        orch.claude_spawner.execute = AsyncMock(return_value=_claude_ok("claude saved it"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "claude saved it"

    @pytest.mark.asyncio
    async def test_claude_fallback_records_original_local_model(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.claude_spawner.execute = AsyncMock(return_value=_claude_ok())

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.data.get("fallback_from") == ModelName.LOCAL_BRAIN


# ---------------------------------------------------------------------------
# All three fail → raw data
# ---------------------------------------------------------------------------


class TestAllLocalChainFails:
    @pytest.mark.asyncio
    async def test_all_fail_returns_raw_data_result(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.claude_spawner.execute = AsyncMock(side_effect=Exception("down"))

        result = await orch._execute_with_fallback(route, _ctx("my message"))

        assert result.success  # raw delivery always marked success
        assert result.error == "all_models_unavailable"
        assert result.data.get("fallback") is True
        assert result.data.get("raw_message") == "my message"

    @pytest.mark.asyncio
    async def test_all_fail_brain_route_also_returns_raw_data(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("down"))
        orch.claude_spawner.execute = AsyncMock(side_effect=Exception("down"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.error == "all_models_unavailable"


# ---------------------------------------------------------------------------
# Regression: existing cloud fallback chain unchanged
# ---------------------------------------------------------------------------


class TestExistingCloudFallbackUnchanged:
    @pytest.mark.asyncio
    async def test_claude_primary_fails_falls_back_to_gemini(self, orch):
        route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node1", mcp_profile="general")
        orch.claude_spawner.execute = AsyncMock(side_effect=Exception("rate limit"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("gemini saved claude"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "gemini saved claude"
        orch.local_client.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_gemini_primary_fails_falls_back_to_claude(self, orch):
        route = ModelRoute(model=ModelName.GEMINI_FLASH, node="node1")
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("overloaded"))
        orch.claude_spawner.execute = AsyncMock(return_value=_claude_ok("claude rescued"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "claude rescued"
        orch.local_client.generate.assert_not_called()
