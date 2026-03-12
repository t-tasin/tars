"""Routes intents to the appropriate AI model and execution node."""

from __future__ import annotations

from dataclasses import dataclass, replace

import structlog

from orchestrator.intent_classifier import Intent
from shared.constants import IntentType, ModelName

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
        _AGENT_PROFILE_MAP: dict[str, str] = {
            IntentType.HEALTH_MONITOR: "diagnostics",
            IntentType.SYSTEM: "diagnostics",
        }
        return _AGENT_PROFILE_MAP.get(agent_type, "general")
