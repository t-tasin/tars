"""Routes intents to the appropriate AI model and execution node.

Two routers live here:

- ``ModelRouter`` (legacy) — pre-Phase-2 routing keyed off ``intent.agent`` w/
  Claude escalation on complexity. Used while ``FEATURE_NEW_ROUTER=false``.
- ``SignalAwareRouter`` (P2-09) — local-default routing. Each request runs on
  Qwen3 (L0 reflex or L1 brain) unless one of ``EscalationSignal`` fires.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from shared.constants import IntentType, ModelName

from orchestrator.intent_classifier import Intent
from orchestrator.signal_detector import EscalationSignal

log = structlog.get_logger()


# MCP server profiles — scopes which MCP servers each Claude agent can access.
MCP_PROFILES: dict[str, list[str]] = {
    "coding": ["github", "filesystem", "postgres"],
    "research": ["brave-search", "postgres"],
    "diagnostics": ["postgres", "brave-search"],
    "communication": ["postgres"],
    "general": ["brave-search"],
}


@dataclass(frozen=True)
class ModelRoute:
    """Describes where and how to execute an agent."""

    model: str  # ModelName value
    node: str = "node1"  # "node1" | "node2"
    mcp_profile: str | None = None  # Key into MCP_PROFILES


class ModelRouter:
    """Routes intents to the appropriate AI model and execution node."""

    AGENT_MODEL_MAP: dict[str, ModelRoute] = {
        # Always Claude
        IntentType.COMMUNICATION: ModelRoute(model=ModelName.CLAUDE_CODE, node="node1"),
        IntentType.CODING: ModelRoute(model=ModelName.CLAUDE_CODE, node="node2"),
        IntentType.RESEARCH: ModelRoute(model=ModelName.CLAUDE_CODE, node="node1"),
        # Always Gemini
        IntentType.EMAIL_CLASSIFIER: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        IntentType.FASHION: ModelRoute(model=ModelName.GEMINI_VISION, node="node1"),
        IntentType.HEALTH_FITNESS: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        IntentType.WORKOUT_TRACKER: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        IntentType.FINANCE: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        # Gemini default, Claude escalation on high complexity
        IntentType.BRIEFING: ModelRoute(model=ModelName.GEMINI_PRO, node="node1"),
        IntentType.JOB_SEARCH: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        IntentType.PRODUCT_RESEARCH: ModelRoute(model=ModelName.GEMINI_PRO, node="node1"),
        IntentType.DAILY_LIFE: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        IntentType.EOD_SUMMARY: ModelRoute(model=ModelName.GEMINI_PRO, node="node1"),
        # Local only
        IntentType.HEALTH_MONITOR: ModelRoute(model=ModelName.LOCAL, node="node1"),
        IntentType.CONFIG: ModelRoute(model=ModelName.LOCAL, node="node1"),
        IntentType.SYSTEM: ModelRoute(model=ModelName.LOCAL, node="node1"),
        # General fallback
        IntentType.GENERAL: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
    }

    def route(self, intent: Intent) -> ModelRoute:
        """Determine the model, node, and MCP profile for an intent.

        Applies override rules in order:
        1. Vision override — force gemini_vision if intent requires vision.
        2. Complexity escalation — promote to claude_code if high complexity
           and not already using Claude.
        3. Docker sandbox — move to node2 if intent needs sandboxed execution.
        4. MCP profile — assign the appropriate MCP profile for Claude agents.
        """
        base = self.AGENT_MODEL_MAP.get(
            intent.agent,
            ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
        )

        model = base.model
        node = base.node
        mcp_profile: str | None = None

        # 1. Vision override
        if intent.requires_vision:
            model = ModelName.GEMINI_VISION

        # 2. Complexity escalation (only if not already Claude)
        if intent.complexity == "high" and model != ModelName.CLAUDE_CODE:
            model = ModelName.CLAUDE_CODE

        # 3. Docker sandbox override
        if intent.needs_docker_sandbox:
            node = "node2"

        # 4. Assign MCP profile for Claude agents
        if model == ModelName.CLAUDE_CODE:
            mcp_profile = self._resolve_mcp_profile(intent.agent)

        route = ModelRoute(model=model, node=node, mcp_profile=mcp_profile)

        log.debug(
            "model_routed",
            agent=intent.agent,
            model=route.model,
            node=route.node,
            mcp_profile=route.mcp_profile,
            overrides={
                "vision": intent.requires_vision,
                "escalated": model != base.model,
                "sandbox": intent.needs_docker_sandbox,
            },
        )
        return route

    @staticmethod
    def _resolve_mcp_profile(agent_type: str) -> str | None:
        """Map agent type to its MCP profile name."""
        # Direct match
        if agent_type in MCP_PROFILES:
            return agent_type

        # Agent-type to profile mapping for agents without a direct match
        agent_profile_map: dict[str, str] = {
            IntentType.HEALTH_MONITOR: "diagnostics",
            IntentType.SYSTEM: "diagnostics",
        }
        return agent_profile_map.get(agent_type, "general")


# ---------------------------------------------------------------------------
# SignalAwareRouter (P2-09)
# ---------------------------------------------------------------------------

# Intents whose default workload fits the L0 reflex tier (short, casual, latency-sensitive).
_REFLEX_INTENTS: set[str] = {
    IntentType.GENERAL,
    IntentType.CONFIG,
    IntentType.SYSTEM,
}


class SignalAwareRouter:
    """Local-default router. Escalates to cloud only on EscalationSignal.

    Routing precedence (most specific / most expensive first):
        TIER3_ESCALATION       → Claude (general mcp)
        IMAGE_GENERATION       → Gemini Vision
        IMAGE_UNDERSTANDING    → Gemini Vision
        OCR_DOCUMENT           → Gemini Vision
        CRITICAL_DIAGNOSTIC    → Claude (diagnostics mcp)
        ARCHITECTURAL_CODE     → Claude (coding mcp, node2)
        SERIOUS_DISCUSSION     → Claude (general mcp)
        DEEP_RESEARCH          → Gemini Pro
        LONG_CONTEXT_REQUIRED  → Gemini Pro
        WEB_GROUNDING_NEEDED   → Gemini Flash
        (none)                 → Local Reflex / Brain per intent
    """

    def route(self, intent: Intent, signals: set[EscalationSignal]) -> ModelRoute:
        # Cloud escalation — first matching signal wins.
        if EscalationSignal.TIER3_ESCALATION in signals:
            route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node1", mcp_profile="general")
        elif EscalationSignal.IMAGE_GENERATION in signals:
            route = ModelRoute(model=ModelName.GEMINI_VISION, node="node1")
        elif EscalationSignal.IMAGE_UNDERSTANDING in signals:
            route = ModelRoute(model=ModelName.GEMINI_VISION, node="node1")
        elif EscalationSignal.OCR_DOCUMENT in signals:
            route = ModelRoute(model=ModelName.GEMINI_VISION, node="node1")
        elif EscalationSignal.ARCHITECTURAL_CODE in signals:
            route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node2", mcp_profile="coding")
        elif EscalationSignal.CRITICAL_DIAGNOSTIC in signals:
            route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node1", mcp_profile="diagnostics")
        elif EscalationSignal.SERIOUS_DISCUSSION in signals:
            route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node1", mcp_profile="general")
        elif EscalationSignal.DEEP_RESEARCH in signals:
            route = ModelRoute(model=ModelName.GEMINI_PRO, node="node1")
        elif EscalationSignal.LONG_CONTEXT_REQUIRED in signals:
            route = ModelRoute(model=ModelName.GEMINI_PRO, node="node1")
        elif EscalationSignal.WEB_GROUNDING_NEEDED in signals:
            route = ModelRoute(model=ModelName.GEMINI_FLASH, node="node1")
        elif intent.agent in _REFLEX_INTENTS:
            route = ModelRoute(model=ModelName.LOCAL_REFLEX, node="node2")
        else:
            route = ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")

        log.debug(
            "signal_aware_routed",
            agent=intent.agent,
            model=route.model,
            node=route.node,
            mcp_profile=route.mcp_profile,
            signals=sorted(s.value for s in signals),
        )
        return route
