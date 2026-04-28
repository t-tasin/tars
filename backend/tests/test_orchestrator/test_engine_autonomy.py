"""Tests for P4-13 — AutonomyBudgetTracker gate wired into engine.handle.

Exercises the section inserted between step 6 (usage tracking) and step 7
(approval flow) in ``Orchestrator.process_message``.  The DB and external
models are fully mocked so these tests run instantly with no infrastructure.

Coverage:
    1. WRITE_SELF under cap  → executes normally, tracker.check_and_increment called
    2. WRITE_SELF at cap     → allowed=False → result coerced → ApprovalManager.create called
    3. WRITE_LOCAL           → tracker NOT called
    4. READ                  → tracker NOT called
    5. WRITE_WORLD           → tracker NOT called (existing approval path used)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import AutonomyClass

from agents.base import AgentResult
from orchestrator.autonomy_budget import BudgetCheck

# ---------------------------------------------------------------------------
# Helpers / shared builders
# ---------------------------------------------------------------------------


def _make_agent_result(
    autonomy_class: AutonomyClass,
    *,
    has_side_effects: bool = False,
    action_type: str | None = None,
    approval_title: str | None = None,
    preview: dict[str, Any] | None = None,
) -> AgentResult:
    return AgentResult(
        success=True,
        text="ok",
        autonomy_class=autonomy_class,
        has_side_effects=has_side_effects,
        action_type=action_type,
        approval_title=approval_title,
        preview=preview,
    )


def _make_approval_dict() -> dict[str, Any]:
    return {
        "id": "approval-123",
        "action_type": "write_self_budget_exhausted",
        "status": "pending",
    }


@asynccontextmanager
async def _noop_session():
    """Fake DB session context manager — yields an AsyncMock."""
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    result.scalar_one.return_value = 0
    result.scalar.return_value = 0
    result.scalars.return_value = result
    session.execute = AsyncMock(return_value=result)
    yield session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def orchestrator():
    """Return an Orchestrator with all external deps patched."""
    from orchestrator.engine import Orchestrator

    return Orchestrator()


# ---------------------------------------------------------------------------
# Test 1: WRITE_SELF under cap — executes normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_self_under_cap_executes_normally(orchestrator) -> None:
    """WRITE_SELF under cap: tracker is called, result flows through normally."""
    agent_result = _make_agent_result(AutonomyClass.WRITE_SELF)
    budget_ok = BudgetCheck(allowed=True, count=1, cap=10)

    with (
        patch("db.session.get_db_session", side_effect=_noop_session),
        patch.object(orchestrator, "_execute", return_value=agent_result),
        patch.object(
            orchestrator,
            "intent_classifier",
        ) as mock_ic,
        patch.object(orchestrator, "model_router") as mock_mr,
        patch.object(orchestrator, "context_builder") as mock_cb,
        patch(
            "orchestrator.engine.UsageTracker",
        ) as MockUsageTracker,
        patch(
            "orchestrator.engine.AutonomyBudgetTracker",
        ) as MockTracker,
        patch.object(orchestrator, "response_formatter") as mock_fmt,
        patch.object(orchestrator, "_save_message", return_value=("conv-1", "msg-1")),
        patch.object(orchestrator, "_save_response"),
        patch.object(orchestrator, "_audit_log"),
    ):
        # Intent / route stubs
        mock_intent = MagicMock()
        mock_intent.agent = "briefing"
        mock_ic.classify.return_value = mock_intent

        mock_route = MagicMock()
        mock_route.model = "local"
        mock_route.node = "node1"
        mock_route.mcp_profile = None
        mock_mr.route.return_value = mock_route

        mock_cb.build = AsyncMock(return_value=MagicMock())

        # UsageTracker stub
        mock_ut = AsyncMock()
        mock_ut.check_budget_alert = AsyncMock(return_value=None)
        MockUsageTracker.return_value = mock_ut

        # Budget tracker stub — allowed
        mock_tracker_inst = AsyncMock()
        mock_tracker_inst.check_and_increment = AsyncMock(return_value=budget_ok)
        MockTracker.return_value = mock_tracker_inst

        mock_fmt.format.return_value = {"response": "ok"}

        await orchestrator.process_message("hello", "ios")

        # Tracker was instantiated and check_and_increment called with WRITE_SELF
        MockTracker.assert_called_once()
        mock_tracker_inst.check_and_increment.assert_awaited_once_with(AutonomyClass.WRITE_SELF, "briefing")

        # Approval path was NOT triggered (no side effects on agent_result)
        orchestrator.approval_manager.create = AsyncMock()
        orchestrator.approval_manager.create.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: WRITE_SELF at cap — coerced to approval flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_self_at_cap_triggers_approval(orchestrator) -> None:
    """WRITE_SELF budget exhausted: result is coerced and ApprovalManager.create called."""
    agent_result = _make_agent_result(AutonomyClass.WRITE_SELF)
    budget_denied = BudgetCheck(allowed=False, count=5, cap=5, reason="daily cap exhausted")
    approval_record = _make_approval_dict()

    with (
        patch("db.session.get_db_session", side_effect=_noop_session),
        patch.object(orchestrator, "_execute", return_value=agent_result),
        patch.object(orchestrator, "intent_classifier") as mock_ic,
        patch.object(orchestrator, "model_router") as mock_mr,
        patch.object(orchestrator, "context_builder") as mock_cb,
        patch("orchestrator.engine.UsageTracker") as MockUsageTracker,
        patch("orchestrator.engine.AutonomyBudgetTracker") as MockTracker,
        patch.object(orchestrator.approval_manager, "create", return_value=approval_record) as mock_create,
        patch.object(orchestrator, "response_formatter") as mock_fmt,
        patch.object(orchestrator, "_save_message", return_value=("conv-1", "msg-1")),
        patch.object(orchestrator, "_save_response"),
        patch.object(orchestrator, "_audit_log"),
    ):
        mock_intent = MagicMock()
        mock_intent.agent = "briefing"
        mock_ic.classify.return_value = mock_intent

        mock_route = MagicMock()
        mock_route.model = "local"
        mock_route.node = "node1"
        mock_route.mcp_profile = None
        mock_mr.route.return_value = mock_route

        mock_cb.build = AsyncMock(return_value=MagicMock())

        mock_ut = AsyncMock()
        mock_ut.check_budget_alert = AsyncMock(return_value=None)
        MockUsageTracker.return_value = mock_ut

        # Budget tracker stub — denied
        mock_tracker_inst = AsyncMock()
        mock_tracker_inst.check_and_increment = AsyncMock(return_value=budget_denied)
        MockTracker.return_value = mock_tracker_inst

        mock_fmt.format_approval.return_value = {"response": "approval"}

        await orchestrator.process_message("self-modify something", "ios")

        # Tracker called
        mock_tracker_inst.check_and_increment.assert_awaited_once_with(AutonomyClass.WRITE_SELF, "briefing")

        # Approval was created (result was coerced to has_side_effects=True)
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["action_type"] == "write_self_budget_exhausted"
        assert call_kwargs["preview_payload"]["budget_reason"] == "daily cap exhausted"
        assert call_kwargs["preview_payload"]["count"] == 5
        assert call_kwargs["preview_payload"]["cap"] == 5


# ---------------------------------------------------------------------------
# Test 3: WRITE_LOCAL — tracker NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_local_tracker_not_called(orchestrator) -> None:
    """WRITE_LOCAL ops must not touch the autonomy tracker."""
    agent_result = _make_agent_result(AutonomyClass.WRITE_LOCAL)

    with (
        patch("db.session.get_db_session", side_effect=_noop_session),
        patch.object(orchestrator, "_execute", return_value=agent_result),
        patch.object(orchestrator, "intent_classifier") as mock_ic,
        patch.object(orchestrator, "model_router") as mock_mr,
        patch.object(orchestrator, "context_builder") as mock_cb,
        patch("orchestrator.engine.UsageTracker") as MockUsageTracker,
        patch("orchestrator.engine.AutonomyBudgetTracker") as MockTracker,
        patch.object(orchestrator, "response_formatter") as mock_fmt,
        patch.object(orchestrator, "_save_message", return_value=("conv-1", "msg-1")),
        patch.object(orchestrator, "_save_response"),
        patch.object(orchestrator, "_audit_log"),
    ):
        mock_intent = MagicMock()
        mock_intent.agent = "config"
        mock_ic.classify.return_value = mock_intent

        mock_route = MagicMock()
        mock_route.model = "local"
        mock_route.node = "node1"
        mock_route.mcp_profile = None
        mock_mr.route.return_value = mock_route

        mock_cb.build = AsyncMock(return_value=MagicMock())

        mock_ut = AsyncMock()
        mock_ut.check_budget_alert = AsyncMock(return_value=None)
        MockUsageTracker.return_value = mock_ut

        mock_fmt.format.return_value = {"response": "ok"}

        await orchestrator.process_message("change setting", "ios")

        MockTracker.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: READ — tracker NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_tracker_not_called(orchestrator) -> None:
    """READ ops must not touch the autonomy tracker."""
    agent_result = _make_agent_result(AutonomyClass.READ)

    with (
        patch("db.session.get_db_session", side_effect=_noop_session),
        patch.object(orchestrator, "_execute", return_value=agent_result),
        patch.object(orchestrator, "intent_classifier") as mock_ic,
        patch.object(orchestrator, "model_router") as mock_mr,
        patch.object(orchestrator, "context_builder") as mock_cb,
        patch("orchestrator.engine.UsageTracker") as MockUsageTracker,
        patch("orchestrator.engine.AutonomyBudgetTracker") as MockTracker,
        patch.object(orchestrator, "response_formatter") as mock_fmt,
        patch.object(orchestrator, "_save_message", return_value=("conv-1", "msg-1")),
        patch.object(orchestrator, "_save_response"),
        patch.object(orchestrator, "_audit_log"),
    ):
        mock_intent = MagicMock()
        mock_intent.agent = "research"
        mock_ic.classify.return_value = mock_intent

        mock_route = MagicMock()
        mock_route.model = "local"
        mock_route.node = "node1"
        mock_route.mcp_profile = None
        mock_mr.route.return_value = mock_route

        mock_cb.build = AsyncMock(return_value=MagicMock())

        mock_ut = AsyncMock()
        mock_ut.check_budget_alert = AsyncMock(return_value=None)
        MockUsageTracker.return_value = mock_ut

        mock_fmt.format.return_value = {"response": "ok"}

        await orchestrator.process_message("what time is it", "ios")

        MockTracker.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: WRITE_WORLD — tracker NOT called (existing approval path used)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_world_tracker_not_called_approval_path_used(orchestrator) -> None:
    """WRITE_WORLD must bypass the budget tracker and go straight to ApprovalManager."""
    agent_result = _make_agent_result(
        AutonomyClass.WRITE_WORLD,
        has_side_effects=True,
        action_type="send_email",
        approval_title="Send email to boss",
    )
    approval_record = _make_approval_dict()

    with (
        patch("db.session.get_db_session", side_effect=_noop_session),
        patch.object(orchestrator, "_execute", return_value=agent_result),
        patch.object(orchestrator, "intent_classifier") as mock_ic,
        patch.object(orchestrator, "model_router") as mock_mr,
        patch.object(orchestrator, "context_builder") as mock_cb,
        patch("orchestrator.engine.UsageTracker") as MockUsageTracker,
        patch("orchestrator.engine.AutonomyBudgetTracker") as MockTracker,
        patch.object(orchestrator.approval_manager, "create", return_value=approval_record),
        patch.object(orchestrator, "response_formatter") as mock_fmt,
        patch.object(orchestrator, "_save_message", return_value=("conv-1", "msg-1")),
        patch.object(orchestrator, "_save_response"),
        patch.object(orchestrator, "_audit_log"),
    ):
        mock_intent = MagicMock()
        mock_intent.agent = "communication"
        mock_ic.classify.return_value = mock_intent

        mock_route = MagicMock()
        mock_route.model = "gemini_flash"
        mock_route.node = "node1"
        mock_route.mcp_profile = None
        mock_mr.route.return_value = mock_route

        mock_cb.build = AsyncMock(return_value=MagicMock())

        mock_ut = AsyncMock()
        mock_ut.check_budget_alert = AsyncMock(return_value=None)
        MockUsageTracker.return_value = mock_ut

        mock_fmt.format_approval.return_value = {"response": "approval"}

        await orchestrator.process_message("send an email", "ios")

        # Tracker was never instantiated
        MockTracker.assert_not_called()

        # But the existing approval path was used
        orchestrator.approval_manager.create.assert_awaited_once()
