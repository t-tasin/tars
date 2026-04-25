"""P2-12: L1 self-escalation JSON protocol — engine state machine.

L1 (LOCAL_BRAIN) is given a system prompt that asks it to emit
``{"escalate": "<tier>", "reason": "..."}`` when uncertain. The engine
parses the L1 reply and reroutes the request to the requested upstream
tier. One hop max: the upstream reply is never re-parsed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import ModelName

from agents.base import AgentContext
from models.local_client import LocalResponse
from orchestrator.engine import Orchestrator
from orchestrator.model_router import ModelRoute


def _ctx(msg: str = "what's the latest CPI print") -> AgentContext:
    return AgentContext(user_message=msg, intent_type="general")


def _local_response(text: str, model: ModelName = ModelName.LOCAL_BRAIN) -> LocalResponse:
    alias = "qwen3-1.7b-reflex" if model == ModelName.LOCAL_REFLEX else "qwen3-8b-brain"
    return LocalResponse(
        text=text,
        reasoning=None,
        model=alias,
        tokens_input=20,
        tokens_output=12,
        duration_ms=300,
        finish_reason="stop",
    )


def _gemini_ok(text: str = "gemini upstream") -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tokens_input = 10
    r.tokens_output = 5
    r.duration_ms = 200
    return r


def _claude_ok(text: str = "claude upstream") -> MagicMock:
    r = MagicMock()
    r.success = True
    r.text = text
    r.error = None
    r.cost_usd = 0.01
    r.duration_ms = 700
    r.num_turns = 1
    return r


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
# L1 plain reply → no escalation
# ---------------------------------------------------------------------------


class TestNoEscalation:
    @pytest.mark.asyncio
    async def test_plain_l1_text_returned_as_is(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response("Sure, the answer is 42."),
        )

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "Sure, the answer is 42."
        orch.gemini_client.generate.assert_not_called()
        orch.claude_spawner.execute.assert_not_called()


# ---------------------------------------------------------------------------
# L1 escalates → engine reroutes
# ---------------------------------------------------------------------------


class TestSelfEscalationReroute:
    @pytest.mark.asyncio
    async def test_escalate_claude_reroutes_to_claude(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "claude", "reason": "needs deep reasoning"}',
            ),
        )
        orch.claude_spawner.execute = AsyncMock(return_value=_claude_ok("claude answer"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "claude answer"
        orch.claude_spawner.execute.assert_called_once()
        orch.gemini_client.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalate_web_reroutes_to_gemini_flash(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "web", "reason": "needs current data"}',
            ),
        )
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("gemini flash"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "gemini flash"
        orch.gemini_client.generate.assert_called_once()
        # Gemini Flash model id passed
        call_kwargs = orch.gemini_client.generate.call_args.kwargs
        assert call_kwargs.get("model") == "gemini-2.5-flash"
        orch.claude_spawner.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalate_gemini_pro_reroutes_to_gemini_pro(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "gemini_pro", "reason": "long ctx"}',
            ),
        )
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("pro response"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "pro response"
        call_kwargs = orch.gemini_client.generate.call_args.kwargs
        assert call_kwargs.get("model") == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_escalation_marks_data_with_self_escalated_from(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "claude", "reason": "complex"}',
            ),
        )
        orch.claude_spawner.execute = AsyncMock(return_value=_claude_ok())

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.data.get("self_escalated_from") == ModelName.LOCAL_BRAIN
        assert result.data.get("escalation_reason") == "complex"


# ---------------------------------------------------------------------------
# One-hop guarantee: upstream reply is NOT re-parsed for escalation
# ---------------------------------------------------------------------------


class TestOneHopLimit:
    @pytest.mark.asyncio
    async def test_upstream_response_with_json_not_reparsed(self, orch):
        """Even if upstream literally says `{"escalate":"claude"}`, no second hop."""
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "gemini_pro", "reason": "deep"}',
            ),
        )
        # Upstream coincidentally replies with escalation-shaped JSON
        orch.gemini_client.generate = AsyncMock(
            return_value=_gemini_ok('{"escalate": "claude", "reason": "more"}'),
        )

        result = await orch._execute_with_fallback(route, _ctx())

        # Must deliver gemini's reply verbatim, NOT call claude
        assert result.text == '{"escalate": "claude", "reason": "more"}'
        orch.claude_spawner.execute.assert_not_called()


# ---------------------------------------------------------------------------
# L1-only: L0 reflex never carries the self-escalation system prompt
# ---------------------------------------------------------------------------


class TestSystemPromptScope:
    @pytest.mark.asyncio
    async def test_l1_brain_call_carries_self_escalation_system_prompt(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(return_value=_local_response("plain"))

        await orch._execute_with_fallback(route, _ctx())

        kwargs = orch.local_client.generate.call_args.kwargs
        system = kwargs.get("system") or ""
        assert "escalate" in system.lower()

    @pytest.mark.asyncio
    async def test_l0_reflex_call_has_base_persona_no_escalation(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response("hi", ModelName.LOCAL_REFLEX),
        )

        await orch._execute_with_fallback(route, _ctx())

        kwargs = orch.local_client.generate.call_args.kwargs
        system = kwargs.get("system") or ""
        # Reflex tier carries base persona (English-only) but never escalates
        assert "T.A.R.S." in system
        assert "English" in system
        assert "escalate" not in system.lower()


# ---------------------------------------------------------------------------
# Escalation target failure → graceful degradation
# ---------------------------------------------------------------------------


class TestEscalationTargetFailure:
    @pytest.mark.asyncio
    async def test_escalation_target_fails_falls_back_to_other_cloud(self, orch):
        """L1 asked for claude; claude is down → try gemini, then raw."""
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "claude", "reason": "..."}',
            ),
        )
        orch.claude_spawner.execute = AsyncMock(side_effect=Exception("rate limit"))
        orch.gemini_client.generate = AsyncMock(return_value=_gemini_ok("gemini rescued"))

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == "gemini rescued"

    @pytest.mark.asyncio
    async def test_escalation_target_and_fallback_fail_returns_raw(self, orch):
        route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "claude", "reason": "..."}',
            ),
        )
        orch.claude_spawner.execute = AsyncMock(side_effect=Exception("down"))
        orch.gemini_client.generate = AsyncMock(side_effect=Exception("down"))

        result = await orch._execute_with_fallback(route, _ctx("question"))

        assert result.error == "all_models_unavailable"
        assert result.data.get("raw_message") == "question"


# ---------------------------------------------------------------------------
# L1 escalation only — LOCAL_REFLEX (L0) never escalates
# ---------------------------------------------------------------------------


class TestReflexNeverEscalates:
    @pytest.mark.asyncio
    async def test_l0_reply_with_escalation_json_returned_as_is(self, orch):
        """L0 has no escalation system prompt — even JSON-shaped reply is passed through."""
        route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        orch.local_client.generate = AsyncMock(
            return_value=_local_response(
                '{"escalate": "claude", "reason": "..."}',
                model=ModelName.LOCAL_REFLEX,
            ),
        )

        result = await orch._execute_with_fallback(route, _ctx())

        assert result.success
        assert result.text == '{"escalate": "claude", "reason": "..."}'
        orch.claude_spawner.execute.assert_not_called()
