"""Tests for UsageTracker with mocked async DB session."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from shared.constants import ModelName

from models.usage_tracker import (
    _COST_PER_1M,
    BUDGET_ALERT_PCT,
    CLAUDE_DAILY_LIMIT,
    CLAUDE_WEEKLY_LIMIT,
    UsageTracker,
    _estimate_cost,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def tracker(mock_session: AsyncMock) -> UsageTracker:
    return UsageTracker(session=mock_session)


# ---------------------------------------------------------------------------
# _estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_cost_gemini_flash():
    # 1M input tokens at $0.10 + 1M output tokens at $0.40 = $0.50
    cost = _estimate_cost(ModelName.GEMINI_FLASH, 1_000_000, 1_000_000)
    assert cost == Decimal("0.500000")


def test_estimate_cost_gemini_pro():
    # 1000 input at $1.25/1M + 500 output at $5.00/1M
    cost = _estimate_cost(ModelName.GEMINI_PRO, 1000, 500)
    expected = Decimal("1.25") * 1000 / Decimal("1000000") + Decimal("5.00") * 500 / Decimal("1000000")
    assert cost == expected.quantize(Decimal("0.000001"))


def test_estimate_cost_claude_is_zero():
    cost = _estimate_cost(ModelName.CLAUDE_CODE, 50000, 10000)
    assert cost == Decimal("0")


def test_estimate_cost_local_is_zero():
    cost = _estimate_cost(ModelName.LOCAL, 100, 100)
    assert cost == Decimal("0")


def test_local_reflex_has_explicit_zero_cost_entry():
    assert ModelName.LOCAL_REFLEX in _COST_PER_1M
    assert _COST_PER_1M[ModelName.LOCAL_REFLEX] == (Decimal("0"), Decimal("0"))


def test_local_brain_has_explicit_zero_cost_entry():
    assert ModelName.LOCAL_BRAIN in _COST_PER_1M
    assert _COST_PER_1M[ModelName.LOCAL_BRAIN] == (Decimal("0"), Decimal("0"))


def test_local_embed_has_explicit_zero_cost_entry():
    assert ModelName.LOCAL_EMBED in _COST_PER_1M
    assert _COST_PER_1M[ModelName.LOCAL_EMBED] == (Decimal("0"), Decimal("0"))


def test_estimate_cost_unknown_model():
    cost = _estimate_cost("unknown_model", 1000, 1000)
    assert cost == Decimal("0")


# ---------------------------------------------------------------------------
# track()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_adds_record(tracker: UsageTracker, mock_session: AsyncMock):
    task_id = uuid4()

    await tracker.track(
        model=ModelName.GEMINI_FLASH,
        agent_type="email_classifier",
        task_id=task_id,
        tokens_input=500,
        tokens_output=100,
        duration_ms=200,
        success=True,
    )

    mock_session.add.assert_called_once()
    record = mock_session.add.call_args[0][0]

    assert record.model == ModelName.GEMINI_FLASH
    assert record.agent_type == "email_classifier"
    assert record.task_id == task_id
    assert record.tokens_input == 500
    assert record.tokens_output == 100
    assert record.duration_ms == 200
    assert record.success is True
    assert record.error_type is None
    assert record.estimated_cost == _estimate_cost(ModelName.GEMINI_FLASH, 500, 100)

    mock_session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_track_with_error(tracker: UsageTracker, mock_session: AsyncMock):
    await tracker.track(
        model=ModelName.CLAUDE_CODE,
        agent_type="coding",
        task_id=None,
        tokens_input=0,
        tokens_output=0,
        duration_ms=5000,
        success=False,
        error_type="timeout",
    )

    record = mock_session.add.call_args[0][0]
    assert record.success is False
    assert record.error_type == "timeout"
    assert record.task_id is None


# ---------------------------------------------------------------------------
# get_daily_summary()
# ---------------------------------------------------------------------------


def _make_daily_row(model: str, calls: int, tokens_in: int, tokens_out: int, cost: str):
    row = MagicMock()
    row.model = model
    row.calls = calls
    row.tokens_input = tokens_in
    row.tokens_output = tokens_out
    row.estimated_cost = Decimal(cost)
    return row


@pytest.mark.asyncio
async def test_get_daily_summary(tracker: UsageTracker, mock_session: AsyncMock):
    rows = [
        _make_daily_row(ModelName.GEMINI_FLASH, 10, 5000, 2000, "0.001300"),
        _make_daily_row(ModelName.CLAUDE_CODE, 3, 8000, 4000, "0.000000"),
    ]
    result_mock = MagicMock()
    result_mock.all.return_value = rows
    mock_session.execute.return_value = result_mock

    summary = await tracker.get_daily_summary()

    assert ModelName.GEMINI_FLASH in summary
    assert summary[ModelName.GEMINI_FLASH]["calls"] == 10
    assert summary[ModelName.GEMINI_FLASH]["tokens_input"] == 5000
    assert summary[ModelName.GEMINI_FLASH]["tokens_output"] == 2000

    assert ModelName.CLAUDE_CODE in summary
    assert summary[ModelName.CLAUDE_CODE]["calls"] == 3


@pytest.mark.asyncio
async def test_get_daily_summary_specific_date(tracker: UsageTracker, mock_session: AsyncMock):
    result_mock = MagicMock()
    result_mock.all.return_value = []
    mock_session.execute.return_value = result_mock

    summary = await tracker.get_daily_summary(date=dt.date(2026, 1, 15))
    assert summary == {}


# ---------------------------------------------------------------------------
# get_weekly_summary()
# ---------------------------------------------------------------------------


def _make_weekly_row(model: str, calls: int, cost: str):
    row = MagicMock()
    row.model = model
    row.calls = calls
    row.estimated_cost = Decimal(cost)
    return row


@pytest.mark.asyncio
async def test_get_weekly_summary(tracker: UsageTracker, mock_session: AsyncMock):
    rows = [
        _make_weekly_row(ModelName.CLAUDE_CODE, 12, "0.000000"),
        _make_weekly_row(ModelName.GEMINI_FLASH, 50, "0.030000"),
        _make_weekly_row(ModelName.GEMINI_PRO, 15, "0.015000"),
    ]
    result_mock = MagicMock()
    result_mock.all.return_value = rows
    mock_session.execute.return_value = result_mock

    summary = await tracker.get_weekly_summary()

    assert summary["claude_calls"] == 12
    assert summary["claude_weekly_limit"] == CLAUDE_WEEKLY_LIMIT
    assert summary["claude_limit_pct"] == round((12 / CLAUDE_WEEKLY_LIMIT) * 100, 1)
    assert summary["gemini_calls"] == 65  # 50 + 15
    assert summary["total_calls"] == 77
    assert "week_start" in summary


@pytest.mark.asyncio
async def test_get_weekly_summary_empty(tracker: UsageTracker, mock_session: AsyncMock):
    result_mock = MagicMock()
    result_mock.all.return_value = []
    mock_session.execute.return_value = result_mock

    summary = await tracker.get_weekly_summary()

    assert summary["claude_calls"] == 0
    assert summary["gemini_calls"] == 0
    assert summary["total_calls"] == 0
    assert summary["claude_limit_pct"] == 0


# ---------------------------------------------------------------------------
# check_budget_alert()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_budget_alert_no_alert(tracker: UsageTracker, mock_session: AsyncMock):
    """Under threshold — no alert."""
    daily_mock = MagicMock()
    daily_mock.scalar.return_value = 5
    weekly_mock = MagicMock()
    weekly_mock.scalar.return_value = 20

    mock_session.execute.side_effect = [daily_mock, weekly_mock]

    alert = await tracker.check_budget_alert()
    assert alert is None


@pytest.mark.asyncio
async def test_check_budget_alert_weekly_warning(tracker: UsageTracker, mock_session: AsyncMock):
    """Weekly calls at 70%+ of limit should trigger warning."""
    weekly_threshold = int(CLAUDE_WEEKLY_LIMIT * BUDGET_ALERT_PCT)

    daily_mock = MagicMock()
    daily_mock.scalar.return_value = 5
    weekly_mock = MagicMock()
    weekly_mock.scalar.return_value = weekly_threshold

    mock_session.execute.side_effect = [daily_mock, weekly_mock]

    alert = await tracker.check_budget_alert()
    assert alert is not None
    assert "weekly" in alert.lower()
    assert str(weekly_threshold) in alert


@pytest.mark.asyncio
async def test_check_budget_alert_weekly_limit_reached(tracker: UsageTracker, mock_session: AsyncMock):
    """At or over weekly limit."""
    daily_mock = MagicMock()
    daily_mock.scalar.return_value = 5
    weekly_mock = MagicMock()
    weekly_mock.scalar.return_value = CLAUDE_WEEKLY_LIMIT

    mock_session.execute.side_effect = [daily_mock, weekly_mock]

    alert = await tracker.check_budget_alert()
    assert alert is not None
    assert "REACHED" in alert


@pytest.mark.asyncio
async def test_check_budget_alert_daily_warning(tracker: UsageTracker, mock_session: AsyncMock):
    """Daily calls at 70%+ should trigger daily warning."""
    daily_threshold = int(CLAUDE_DAILY_LIMIT * BUDGET_ALERT_PCT)

    daily_mock = MagicMock()
    daily_mock.scalar.return_value = daily_threshold
    weekly_mock = MagicMock()
    weekly_mock.scalar.return_value = daily_threshold  # under weekly threshold

    mock_session.execute.side_effect = [daily_mock, weekly_mock]

    alert = await tracker.check_budget_alert()
    assert alert is not None
    assert "daily" in alert.lower()


@pytest.mark.asyncio
async def test_check_budget_alert_both_warnings(tracker: UsageTracker, mock_session: AsyncMock):
    """Both daily and weekly limits can trigger simultaneously."""
    daily_mock = MagicMock()
    daily_mock.scalar.return_value = CLAUDE_DAILY_LIMIT
    weekly_mock = MagicMock()
    weekly_mock.scalar.return_value = CLAUDE_WEEKLY_LIMIT

    mock_session.execute.side_effect = [daily_mock, weekly_mock]

    alert = await tracker.check_budget_alert()
    assert alert is not None
    assert "weekly" in alert.lower()
    assert "daily" in alert.lower()
