"""Tests for SignalAwareRouter — local-default routing w/ cloud escalation per signal."""

from __future__ import annotations

import pytest
from shared.constants import IntentType, ModelName

from orchestrator.intent_classifier import Intent
from orchestrator.model_router import ModelRoute, SignalAwareRouter
from orchestrator.signal_detector import EscalationSignal


@pytest.fixture
def router() -> SignalAwareRouter:
    return SignalAwareRouter()


# ---------------------------------------------------------------------------
# Local defaults — no signals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent",
    [
        IntentType.GENERAL,
        IntentType.CONFIG,
        IntentType.SYSTEM,
    ],
)
def test_routes_short_intents_to_local_reflex(router, agent):
    route = router.route(Intent(agent=agent), signals=set())
    assert route.model == ModelName.LOCAL_REFLEX
    assert route.node == "node2"


@pytest.mark.parametrize(
    "agent",
    [
        IntentType.BRIEFING,
        IntentType.DAILY_LIFE,
        IntentType.COMMUNICATION,
        IntentType.JOB_SEARCH,
        IntentType.FASHION,
        IntentType.PRODUCT_RESEARCH,
        IntentType.CODING,
        IntentType.RESEARCH,
        IntentType.HEALTH_MONITOR,
        IntentType.FINANCE,
        IntentType.HEALTH_FITNESS,
        IntentType.EMAIL_CLASSIFIER,
        IntentType.EOD_SUMMARY,
        IntentType.WORKOUT_TRACKER,
    ],
)
def test_routes_substantive_intents_to_local_brain(router, agent):
    route = router.route(Intent(agent=agent), signals=set())
    assert route.model == ModelName.LOCAL_BRAIN
    assert route.node == "node2"


# ---------------------------------------------------------------------------
# Cloud escalation per signal
# ---------------------------------------------------------------------------


def test_image_generation_routes_to_gemini_vision(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.IMAGE_GENERATION},
    )
    assert route.model == ModelName.GEMINI_VISION


def test_image_understanding_routes_to_gemini_vision(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.IMAGE_UNDERSTANDING},
    )
    assert route.model == ModelName.GEMINI_VISION


def test_ocr_routes_to_gemini_vision(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.OCR_DOCUMENT},
    )
    assert route.model == ModelName.GEMINI_VISION


def test_tier3_routes_to_claude(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.TIER3_ESCALATION},
    )
    assert route.model == ModelName.CLAUDE_CODE


def test_critical_diagnostic_routes_to_claude_with_diagnostics_mcp(router):
    route = router.route(
        Intent(agent=IntentType.HEALTH_MONITOR),
        signals={EscalationSignal.CRITICAL_DIAGNOSTIC},
    )
    assert route.model == ModelName.CLAUDE_CODE
    assert route.mcp_profile == "diagnostics"


def test_serious_discussion_routes_to_claude(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.SERIOUS_DISCUSSION},
    )
    assert route.model == ModelName.CLAUDE_CODE


def test_architectural_code_routes_to_claude_on_node2_with_coding_mcp(router):
    route = router.route(
        Intent(agent=IntentType.CODING),
        signals={EscalationSignal.ARCHITECTURAL_CODE},
    )
    assert route.model == ModelName.CLAUDE_CODE
    assert route.node == "node2"
    assert route.mcp_profile == "coding"


def test_deep_research_routes_to_gemini_pro(router):
    route = router.route(
        Intent(agent=IntentType.RESEARCH, complexity="high"),
        signals={EscalationSignal.DEEP_RESEARCH},
    )
    assert route.model == ModelName.GEMINI_PRO


def test_long_context_routes_to_gemini_pro(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.LONG_CONTEXT_REQUIRED},
    )
    assert route.model == ModelName.GEMINI_PRO


def test_web_grounding_routes_to_gemini_flash(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={EscalationSignal.WEB_GROUNDING_NEEDED},
    )
    assert route.model == ModelName.GEMINI_FLASH


# ---------------------------------------------------------------------------
# Signal precedence — most-expensive/specific wins
# ---------------------------------------------------------------------------


def test_tier3_overrides_critical_diagnostic(router):
    route = router.route(
        Intent(agent=IntentType.HEALTH_MONITOR),
        signals={
            EscalationSignal.TIER3_ESCALATION,
            EscalationSignal.CRITICAL_DIAGNOSTIC,
        },
    )
    assert route.model == ModelName.CLAUDE_CODE


def test_image_generation_overrides_web_grounding(router):
    route = router.route(
        Intent(agent=IntentType.GENERAL),
        signals={
            EscalationSignal.IMAGE_GENERATION,
            EscalationSignal.WEB_GROUNDING_NEEDED,
        },
    )
    assert route.model == ModelName.GEMINI_VISION


def test_architectural_code_overrides_serious_discussion(router):
    """ARCH_CODE has node2 + coding mcp specificity, takes precedence."""
    route = router.route(
        Intent(agent=IntentType.CODING),
        signals={
            EscalationSignal.SERIOUS_DISCUSSION,
            EscalationSignal.ARCHITECTURAL_CODE,
        },
    )
    assert route.mcp_profile == "coding"
    assert route.node == "node2"


def test_deep_research_overrides_web_grounding(router):
    route = router.route(
        Intent(agent=IntentType.RESEARCH, complexity="high"),
        signals={
            EscalationSignal.DEEP_RESEARCH,
            EscalationSignal.WEB_GROUNDING_NEEDED,
        },
    )
    assert route.model == ModelName.GEMINI_PRO


# ---------------------------------------------------------------------------
# Vision intent shortcut — requires_vision attribute
# ---------------------------------------------------------------------------


def test_no_signals_with_vision_intent_still_local(router):
    """SignalAwareRouter trusts signal set; vision flag handled by detector upstream."""
    route = router.route(
        Intent(agent=IntentType.GENERAL, requires_vision=True),
        signals=set(),
    )
    # Without IMAGE_UNDERSTANDING signal, the router stays local — detector is
    # responsible for surfacing the signal when an image attachment is present.
    assert route.model in {ModelName.LOCAL_REFLEX, ModelName.LOCAL_BRAIN}


def test_route_returns_modelroute_dataclass(router):
    route = router.route(Intent(agent=IntentType.GENERAL), signals=set())
    assert isinstance(route, ModelRoute)
