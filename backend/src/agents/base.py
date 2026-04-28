"""Base agent class and data structures for all T.A.R.S. agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from shared.constants import TIER_MAP, AutonomyClass, RiskTier


@dataclass
class AgentResult:
    """Result returned by any agent execution.

    ``autonomy_class`` is **required** (P4-12). Each agent must declare the
    autonomy class for the action it performed; the orchestrator gates
    execution + approval based on this value (see ``docs/autonomy_budget.md``).
    """

    success: bool
    text: str
    autonomy_class: AutonomyClass
    content_type: str = "text"
    cards: list[dict[str, Any]] = field(default_factory=list)
    has_side_effects: bool = False
    action_type: str | None = None
    approval_title: str | None = None
    preview: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AgentContext:
    """Context provided to an agent for execution."""

    user_message: str
    intent_type: str
    conversation_id: UUID | None = None
    source: str = "system"
    attachments: list[dict[str, Any]] = field(default_factory=list)
    db_context: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    system_context: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base class for all T.A.R.S. agents."""

    agent_type: str = "base"
    #: Default autonomy class for this agent — used as the conservative
    #: fallback when constructing :class:`AgentResult`. Subclasses must
    #: declare their own class per ``docs/FEATURES.md`` Agents Registry.
    autonomy_class: AutonomyClass = AutonomyClass.READ

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult:
        """Execute the agent's primary task. Must be implemented by all agents."""
        ...

    async def validate_context(self, context: AgentContext) -> bool:
        """Optional validation before execution. Override if needed."""
        return True

    def get_risk_tier(self, action_type: str) -> RiskTier:
        """Look up the risk tier for a given action type."""
        return TIER_MAP.get(action_type, RiskTier.TIER2_APPROVAL)
