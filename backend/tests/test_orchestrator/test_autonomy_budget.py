"""Tests for ``orchestrator.autonomy_budget.AutonomyBudgetTracker`` (P4-13).

Pure-Python — no DB. The repository is patched at the call site so the
tracker behaviour can be exercised under controlled conditions
(under-cap, at-cap, weekly-cap, no-limit-row, frozen clock, defensive
raise for WRITE_WORLD/INFRA, ...).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import AutonomyClass

from orchestrator.autonomy_budget import AutonomyBudgetTracker, BudgetCheck


def _session_factory() -> object:
    """Return a session-factory callable that yields a MagicMock session."""

    @asynccontextmanager
    async def _factory():
        yield MagicMock()

    return _factory


def _frozen_clock(year: int, month: int, day: int):
    """Return a clock callable pinned to the supplied UTC date."""
    fixed = datetime(year, month, day, 9, 0, tzinfo=UTC)
    return lambda: fixed


# ---------------------------------------------------------------------------
# Pass-through classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_is_passthrough_no_db_hit() -> None:
    factory_called = False

    @asynccontextmanager
    async def factory():
        nonlocal factory_called
        factory_called = True
        yield MagicMock()

    tracker = AutonomyBudgetTracker(session_factory=factory)
    result = await tracker.check_and_increment(AutonomyClass.READ, "briefing")
    assert result == BudgetCheck(allowed=True, count=0, cap=None)
    assert factory_called is False


@pytest.mark.asyncio
async def test_write_local_is_passthrough_no_db_hit() -> None:
    factory_called = False

    @asynccontextmanager
    async def factory():
        nonlocal factory_called
        factory_called = True
        yield MagicMock()

    tracker = AutonomyBudgetTracker(session_factory=factory)
    result = await tracker.check_and_increment(AutonomyClass.WRITE_LOCAL, "email_classifier")
    assert result.allowed is True
    assert result.cap is None
    assert factory_called is False


# ---------------------------------------------------------------------------
# Defensive raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_world_raises_runtime_error() -> None:
    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with pytest.raises(RuntimeError, match="ApprovalManager"):
        await tracker.check_and_increment(AutonomyClass.WRITE_WORLD, "communication")


@pytest.mark.asyncio
async def test_write_infra_raises_runtime_error() -> None:
    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with pytest.raises(RuntimeError, match="ApprovalManager"):
        await tracker.check_and_increment(AutonomyClass.WRITE_INFRA, "deployer")


# ---------------------------------------------------------------------------
# WRITE_SELF — under cap, at cap, no limit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_self_under_cap_increments_and_allows() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, 180))
    repo.get_count = AsyncMock(return_value=5)
    repo.weekly_count = AsyncMock(return_value=20)
    repo.increment = AsyncMock(return_value=6)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    assert result.allowed is True
    assert result.count == 6
    assert result.cap == 30
    repo.increment.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_self_at_daily_cap_denies() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, 180))
    repo.get_count = AsyncMock(return_value=30)  # already at cap
    repo.weekly_count = AsyncMock(return_value=120)
    repo.increment = AsyncMock(return_value=99)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    assert result.allowed is False
    assert result.count == 30
    assert result.cap == 30
    assert result.reason == "daily cap exhausted"
    repo.increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_self_above_daily_cap_denies() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, None))
    repo.get_count = AsyncMock(return_value=42)  # somehow above cap

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    assert result.allowed is False
    assert result.reason == "daily cap exhausted"


@pytest.mark.asyncio
async def test_write_self_no_limit_row_passes_through_uncapped() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=None)  # no row → uncapped
    repo.increment = AsyncMock(return_value=7)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "research")

    assert result.allowed is True
    assert result.count == 7
    assert result.cap is None
    repo.increment.assert_awaited_once()


# ---------------------------------------------------------------------------
# Weekly cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_self_weekly_cap_denies_when_daily_under() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, 180))
    repo.get_count = AsyncMock(return_value=10)  # daily fine
    repo.weekly_count = AsyncMock(return_value=180)  # weekly exhausted
    repo.increment = AsyncMock(return_value=11)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    assert result.allowed is False
    assert result.cap == 180
    assert result.reason == "weekly cap exhausted"
    repo.increment.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_self_weekly_skipped_when_unset() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, None))  # weekly_cap None
    repo.get_count = AsyncMock(return_value=5)
    repo.weekly_count = AsyncMock(return_value=999_999)  # would deny if checked
    repo.increment = AsyncMock(return_value=6)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    assert result.allowed is True
    repo.weekly_count.assert_not_awaited()
    repo.increment.assert_awaited_once()


# ---------------------------------------------------------------------------
# Clock injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frozen_clock_is_used_for_today() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(30, 180))
    repo.get_count = AsyncMock(return_value=0)
    repo.weekly_count = AsyncMock(return_value=0)
    repo.increment = AsyncMock(return_value=1)

    clock = _frozen_clock(2026, 4, 28)
    tracker = AutonomyBudgetTracker(session_factory=_session_factory(), clock=clock)

    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")

    # Both the count lookup and the increment must use the frozen date.
    args = repo.get_count.await_args
    assert args.args[0].isoformat() == "2026-04-28"
    inc_args = repo.increment.await_args
    assert inc_args.args[0].isoformat() == "2026-04-28"


@pytest.mark.asyncio
async def test_default_clock_falls_back_to_utc_now() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=None)
    repo.increment = AsyncMock(return_value=1)

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "briefing")
    assert result.allowed is True


# ---------------------------------------------------------------------------
# Concurrency hygiene — two consecutive checks reflect independent counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_calls_use_fresh_counts() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(2, None))
    counts = iter([0, 1])
    repo.get_count = AsyncMock(side_effect=lambda *a, **kw: next(counts))
    increments = iter([1, 2])
    repo.increment = AsyncMock(side_effect=lambda *a, **kw: next(increments))

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        first = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "agent1")
        second = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "agent1")

    assert first.allowed is True and first.count == 1
    assert second.allowed is True and second.count == 2


@pytest.mark.asyncio
async def test_third_call_at_cap_denied() -> None:
    repo = AsyncMock()
    repo.get_limit = AsyncMock(return_value=(2, None))
    repo.get_count = AsyncMock(return_value=2)  # already at cap
    repo.increment = AsyncMock()

    tracker = AutonomyBudgetTracker(session_factory=_session_factory())
    with patch(
        "orchestrator.autonomy_budget.AutonomyBudgetRepository",
        return_value=repo,
    ):
        result = await tracker.check_and_increment(AutonomyClass.WRITE_SELF, "agent1")

    assert result.allowed is False
    assert result.reason == "daily cap exhausted"
    repo.increment.assert_not_awaited()


# ---------------------------------------------------------------------------
# BudgetCheck dataclass
# ---------------------------------------------------------------------------


def test_budget_check_is_frozen_dataclass() -> None:
    bc = BudgetCheck(allowed=True, count=1, cap=30)
    with pytest.raises((AttributeError, TypeError)):
        bc.allowed = False  # type: ignore[misc]
    assert bc.reason is None
