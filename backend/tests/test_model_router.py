"""Tests for the ModelRouter."""

from __future__ import annotations

import pytest
from shared.constants import IntentType, ModelName

from orchestrator.intent_classifier import Intent
from orchestrator.model_router import MCP_PROFILES, ModelRoute, ModelRouter


@pytest.fixture
def router() -> ModelRouter:
    return ModelRouter()


# ── Default routing for all 16 agent types ──────────────────────────────


_EXPECTED_DEFAULTS: list[tuple[str, str, str]] = [
    # (agent_type, expected_model, expected_node)
    (IntentType.COMMUNICATION, ModelName.CLAUDE_CODE, "node1"),
    (IntentType.CODING, ModelName.CLAUDE_CODE, "node2"),
    (IntentType.RESEARCH, ModelName.CLAUDE_CODE, "node1"),
    (IntentType.EMAIL_CLASSIFIER, ModelName.GEMINI_FLASH, "node1"),
    (IntentType.FASHION, ModelName.GEMINI_VISION, "node1"),
    (IntentType.HEALTH_FITNESS, ModelName.GEMINI_FLASH, "node1"),
    (IntentType.FINANCE, ModelName.GEMINI_FLASH, "node1"),
    (IntentType.BRIEFING, ModelName.GEMINI_PRO, "node1"),
    (IntentType.JOB_SEARCH, ModelName.GEMINI_FLASH, "node1"),
    (IntentType.PRODUCT_RESEARCH, ModelName.GEMINI_PRO, "node1"),
    (IntentType.DAILY_LIFE, ModelName.GEMINI_FLASH, "node1"),
    (IntentType.EOD_SUMMARY, ModelName.GEMINI_PRO, "node1"),
    (IntentType.HEALTH_MONITOR, ModelName.LOCAL, "node1"),
    (IntentType.CONFIG, ModelName.LOCAL, "node1"),
    (IntentType.SYSTEM, ModelName.LOCAL, "node1"),
    (IntentType.GENERAL, ModelName.GEMINI_FLASH, "node1"),
]


@pytest.mark.parametrize(
    ("agent_type", "expected_model", "expected_node"),
    _EXPECTED_DEFAULTS,
    ids=[t[0] for t in _EXPECTED_DEFAULTS],
)
def test_default_routing(
    router: ModelRouter,
    agent_type: str,
    expected_model: str,
    expected_node: str,
) -> None:
    intent = Intent(agent=agent_type)
    route = router.route(intent)
    assert route.model == expected_model
    assert route.node == expected_node


# ── Vision override ─────────────────────────────────────────────────────


def test_vision_override_forces_gemini_vision(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.DAILY_LIFE, requires_vision=True)
    route = router.route(intent)
    assert route.model == ModelName.GEMINI_VISION


def test_vision_override_on_claude_agent(router: ModelRouter) -> None:
    """Vision should override even Claude-default agents."""
    intent = Intent(agent=IntentType.COMMUNICATION, requires_vision=True)
    route = router.route(intent)
    assert route.model == ModelName.GEMINI_VISION


# ── Complexity escalation ───────────────────────────────────────────────


def test_complexity_escalation_to_claude(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.BRIEFING, complexity="high")
    route = router.route(intent)
    assert route.model == ModelName.CLAUDE_CODE


def test_complexity_no_escalation_when_already_claude(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.CODING, complexity="high")
    route = router.route(intent)
    assert route.model == ModelName.CLAUDE_CODE


def test_normal_complexity_no_escalation(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.BRIEFING, complexity="normal")
    route = router.route(intent)
    assert route.model == ModelName.GEMINI_PRO


# ── Docker sandbox routing ──────────────────────────────────────────────


def test_docker_sandbox_moves_to_node2(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.RESEARCH, needs_docker_sandbox=True)
    route = router.route(intent)
    assert route.node == "node2"


def test_docker_sandbox_on_node1_agent(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.BRIEFING, needs_docker_sandbox=True)
    route = router.route(intent)
    assert route.node == "node2"


# ── MCP profile assignment ──────────────────────────────────────────────


def test_claude_agents_get_mcp_profile(router: ModelRouter) -> None:
    for agent in (IntentType.COMMUNICATION, IntentType.CODING, IntentType.RESEARCH):
        intent = Intent(agent=agent)
        route = router.route(intent)
        assert route.mcp_profile is not None


def test_non_claude_agents_no_mcp_profile(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.BRIEFING)
    route = router.route(intent)
    assert route.mcp_profile is None


def test_escalated_agent_gets_mcp_profile(router: ModelRouter) -> None:
    """When Gemini escalates to Claude, it should get an MCP profile."""
    intent = Intent(agent=IntentType.BRIEFING, complexity="high")
    route = router.route(intent)
    assert route.model == ModelName.CLAUDE_CODE
    assert route.mcp_profile is not None


def test_coding_agent_mcp_profile(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.CODING)
    route = router.route(intent)
    assert route.mcp_profile == "coding"
    assert "github" in MCP_PROFILES["coding"]
    assert "filesystem" in MCP_PROFILES["coding"]


def test_communication_agent_mcp_profile(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.COMMUNICATION)
    route = router.route(intent)
    assert route.mcp_profile == "communication"


def test_research_agent_mcp_profile(router: ModelRouter) -> None:
    intent = Intent(agent=IntentType.RESEARCH)
    route = router.route(intent)
    assert route.mcp_profile == "research"


# ── Combined overrides ──────────────────────────────────────────────────


def test_vision_takes_priority_over_complexity(router: ModelRouter) -> None:
    """Vision override happens first, then complexity checks against the
    vision-overridden model. Since gemini_vision != claude_code, complexity
    escalation should promote to claude_code."""
    intent = Intent(
        agent=IntentType.DAILY_LIFE,
        requires_vision=True,
        complexity="high",
    )
    route = router.route(intent)
    # Vision sets gemini_vision, then complexity escalates to claude_code
    assert route.model == ModelName.CLAUDE_CODE


def test_sandbox_with_escalation(router: ModelRouter) -> None:
    intent = Intent(
        agent=IntentType.BRIEFING,
        complexity="high",
        needs_docker_sandbox=True,
    )
    route = router.route(intent)
    assert route.model == ModelName.CLAUDE_CODE
    assert route.node == "node2"


# ── Unknown agent fallback ──────────────────────────────────────────────


def test_unknown_agent_gets_default(router: ModelRouter) -> None:
    intent = Intent(agent="nonexistent_agent")
    route = router.route(intent)
    assert route.model == ModelName.GEMINI_FLASH
    assert route.node == "node1"


# ── ModelRoute is frozen ────────────────────────────────────────────────


def test_model_route_is_immutable() -> None:
    route = ModelRoute(model=ModelName.CLAUDE_CODE)
    with pytest.raises(AttributeError):
        route.model = ModelName.GEMINI_FLASH  # type: ignore[misc]
