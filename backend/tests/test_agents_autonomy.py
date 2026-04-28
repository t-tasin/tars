"""Per-agent autonomy class declaration tests (P4-12).

Asserts every concrete ``BaseAgent`` subclass declares ``autonomy_class``
matching the registry in ``docs/FEATURES.md``. This is the gate that
prevents a new agent from shipping without an autonomy decision.
"""

from __future__ import annotations

import pytest
from shared.constants import AutonomyClass

from agents.base import AgentResult, BaseAgent
from agents.briefing import BriefingAgent
from agents.coding import CodingAgent
from agents.communication import CommunicationAgent
from agents.daily_life import DailyLifeAgent
from agents.email_classifier import EmailClassifierAgent
from agents.eod_summary import EODSummaryAgent
from agents.fashion import FashionAgent
from agents.finance import FinanceAgent
from agents.health_fitness import HealthFitnessAgent
from agents.health_monitor import HealthMonitorAgent
from agents.job_application import JobApplicationAgent
from agents.job_search import JobSearchAgent
from agents.product_research import ProductResearchAgent
from agents.research import ResearchAgent
from agents.workout_tracker import WorkoutTrackerAgent

# Registry: agent class → expected autonomy class.
# Keep this list in lockstep with docs/FEATURES.md "Agents Registry".
AGENT_REGISTRY: list[tuple[type[BaseAgent], AutonomyClass]] = [
    (BriefingAgent, AutonomyClass.READ),
    (JobSearchAgent, AutonomyClass.READ),
    (ProductResearchAgent, AutonomyClass.READ),
    (ResearchAgent, AutonomyClass.READ),
    (HealthMonitorAgent, AutonomyClass.READ),
    (FinanceAgent, AutonomyClass.READ),
    (EmailClassifierAgent, AutonomyClass.WRITE_LOCAL),
    (DailyLifeAgent, AutonomyClass.WRITE_LOCAL),
    (FashionAgent, AutonomyClass.WRITE_LOCAL),
    (EODSummaryAgent, AutonomyClass.WRITE_LOCAL),
    (WorkoutTrackerAgent, AutonomyClass.WRITE_LOCAL),
    (HealthFitnessAgent, AutonomyClass.WRITE_SELF),
    (CommunicationAgent, AutonomyClass.WRITE_WORLD),
    (CodingAgent, AutonomyClass.WRITE_WORLD),
    (JobApplicationAgent, AutonomyClass.WRITE_WORLD),
]


# ── Class-attribute declarations ───────────────────────────────────────


@pytest.mark.parametrize(("agent_cls", "expected"), AGENT_REGISTRY, ids=lambda v: getattr(v, "__name__", str(v)))
def test_agent_declares_autonomy_class(agent_cls: type[BaseAgent], expected: AutonomyClass) -> None:
    """Each agent class must set ``autonomy_class`` matching the registry."""
    assert hasattr(agent_cls, "autonomy_class"), f"{agent_cls.__name__} missing autonomy_class"
    assert isinstance(agent_cls.autonomy_class, AutonomyClass), (
        f"{agent_cls.__name__}.autonomy_class is not an AutonomyClass instance"
    )
    assert agent_cls.autonomy_class == expected, (
        f"{agent_cls.__name__}.autonomy_class={agent_cls.autonomy_class} (expected {expected})"
    )


def test_every_agent_has_explicit_class_attribute() -> None:
    """No agent in the registry may inherit the BaseAgent default silently.

    BaseAgent ships with a conservative READ default; concrete agents must
    *explicitly* declare so future Tasin reviewers can audit by grep.
    """
    for agent_cls, _expected in AGENT_REGISTRY:
        assert "autonomy_class" in agent_cls.__dict__, (
            f"{agent_cls.__name__} inherits autonomy_class — must override explicitly"
        )


# ── Field is required on AgentResult ───────────────────────────────────


class TestAgentResultRequiresAutonomyClass:
    def test_construction_without_autonomy_class_raises(self) -> None:
        with pytest.raises(TypeError):
            AgentResult(success=True, text="hello")  # type: ignore[call-arg]

    def test_construction_with_autonomy_class_succeeds(self) -> None:
        result = AgentResult(
            success=True,
            text="hello",
            autonomy_class=AutonomyClass.READ,
        )
        assert result.autonomy_class is AutonomyClass.READ
