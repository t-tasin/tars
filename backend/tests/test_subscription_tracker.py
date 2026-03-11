"""Tests for SubscriptionTracker — recurring charge detection and price changes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.subscription_tracker import (
    PriceChange,
    RecurringCharge,
    SubscriptionTracker,
    _detect_cycle,
    _next_expected,
)


# ---------------------------------------------------------------------------
# Tests: _detect_cycle helper
# ---------------------------------------------------------------------------


class TestDetectCycle:
    def test_weekly_cycle(self) -> None:
        base = date(2026, 1, 5)
        dates = [base + timedelta(days=7 * i) for i in range(4)]
        assert _detect_cycle(dates) == "weekly"

    def test_biweekly_cycle(self) -> None:
        base = date(2026, 1, 5)
        dates = [base + timedelta(days=14 * i) for i in range(4)]
        assert _detect_cycle(dates) == "biweekly"

    def test_monthly_cycle(self) -> None:
        dates = [date(2026, 1, 15), date(2026, 2, 14), date(2026, 3, 15)]
        assert _detect_cycle(dates) == "monthly"

    def test_annual_cycle(self) -> None:
        dates = [date(2024, 3, 10), date(2025, 3, 12), date(2026, 3, 10)]
        assert _detect_cycle(dates) == "annual"

    def test_no_cycle_single_date(self) -> None:
        assert _detect_cycle([date(2026, 3, 10)]) is None

    def test_no_cycle_irregular(self) -> None:
        dates = [date(2026, 1, 1), date(2026, 1, 3), date(2026, 3, 20)]
        assert _detect_cycle(dates) is None


class TestNextExpected:
    def test_weekly(self) -> None:
        assert _next_expected(date(2026, 3, 10), "weekly") == date(2026, 3, 17)

    def test_monthly(self) -> None:
        assert _next_expected(date(2026, 3, 10), "monthly") == date(2026, 4, 9)

    def test_annual(self) -> None:
        assert _next_expected(date(2026, 3, 10), "annual") == date(2027, 3, 10)

    def test_unknown_defaults_to_30(self) -> None:
        assert _next_expected(date(2026, 3, 10), "unknown") == date(2026, 4, 9)


# ---------------------------------------------------------------------------
# Tests: check_price_changes (pure function — no DB)
# ---------------------------------------------------------------------------


class TestCheckPriceChanges:
    def setup_method(self) -> None:
        self.tracker = SubscriptionTracker()

    def test_price_increase_detected(self) -> None:
        """A >10% price increase should be flagged."""
        txns = {
            "Netflix": [
                (15.49, date(2026, 1, 15)),
                (15.49, date(2026, 2, 15)),
                (17.99, date(2026, 3, 15)),  # ~16% increase
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 1
        assert changes[0].merchant == "Netflix"
        assert changes[0].current_amount == 17.99
        assert changes[0].change_pct > 10.0

    def test_no_price_change(self) -> None:
        """Consistent amounts should not be flagged."""
        txns = {
            "Spotify": [
                (9.99, date(2026, 1, 15)),
                (9.99, date(2026, 2, 15)),
                (9.99, date(2026, 3, 15)),
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 0

    def test_price_decrease_not_flagged(self) -> None:
        """A decrease should not be flagged (only increases)."""
        txns = {
            "Service": [
                (20.00, date(2026, 1, 15)),
                (20.00, date(2026, 2, 15)),
                (15.00, date(2026, 3, 15)),
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 0

    def test_single_transaction_skipped(self) -> None:
        """A merchant with only 1 transaction can't have a price change."""
        txns = {
            "OneOff": [(10.00, date(2026, 3, 10))],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 0

    def test_zero_previous_avg_skipped(self) -> None:
        """If previous avg is zero, skip to avoid division by zero."""
        txns = {
            "Free": [
                (0.00, date(2026, 1, 15)),
                (0.00, date(2026, 2, 15)),
                (5.00, date(2026, 3, 15)),
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 0

    def test_multiple_merchants(self) -> None:
        """Multiple merchants — only those with >10% increase flagged."""
        txns = {
            "Rising": [
                (10.00, date(2026, 1, 15)),
                (10.00, date(2026, 2, 15)),
                (12.50, date(2026, 3, 15)),  # 25% increase
            ],
            "Stable": [
                (20.00, date(2026, 1, 15)),
                (20.00, date(2026, 2, 15)),
                (20.50, date(2026, 3, 15)),  # 2.5% — under threshold
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 1
        assert changes[0].merchant == "Rising"

    def test_two_months_only(self) -> None:
        """Edge case: only 2 transactions — still detectable."""
        txns = {
            "New": [
                (10.00, date(2026, 2, 15)),
                (12.00, date(2026, 3, 15)),  # 20% increase
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 1

    def test_variable_amounts(self) -> None:
        """Slightly variable amounts — avg compared to latest."""
        txns = {
            "Variable": [
                (9.50, date(2026, 1, 15)),
                (10.50, date(2026, 2, 15)),
                (10.00, date(2026, 3, 1)),
                (15.00, date(2026, 3, 15)),  # avg of prev 3 = 10.0, latest=15 → 50%
            ],
        }
        changes = self.tracker.check_price_changes(txns)
        assert len(changes) == 1
        assert changes[0].change_pct > 40.0


# ---------------------------------------------------------------------------
# Tests: detect_recurring (requires mock DB session)
# ---------------------------------------------------------------------------


def _make_mock_session_for_recurring(
    txn_rows: list[tuple[str, Decimal, date]],
) -> AsyncMock:
    """Build a mock session that returns transaction rows for detect_recurring."""
    session = AsyncMock()

    # First execute: the SELECT query
    select_result = MagicMock()
    select_result.all.return_value = txn_rows
    # Second execute: the UPDATE query (if recurring found)
    update_result = MagicMock()

    session.execute = AsyncMock(side_effect=[select_result, update_result])
    session.flush = AsyncMock()
    return session


class TestDetectRecurring:
    @pytest.mark.asyncio
    async def test_monthly_recurring_detected(self) -> None:
        """A merchant appearing in 3+ distinct months is flagged as recurring."""
        rows = [
            ("Netflix", Decimal("15.49"), date(2026, 1, 15)),
            ("Netflix", Decimal("15.49"), date(2026, 2, 15)),
            ("Netflix", Decimal("15.49"), date(2026, 3, 15)),
        ]
        session = _make_mock_session_for_recurring(rows)
        tracker = SubscriptionTracker()

        recurring, txn_map = await tracker.detect_recurring(session)

        assert len(recurring) == 1
        assert recurring[0].merchant == "Netflix"
        assert recurring[0].cycle == "monthly"
        assert "Netflix" in txn_map

    @pytest.mark.asyncio
    async def test_non_recurring_single_transaction(self) -> None:
        """A merchant appearing only once should not be flagged."""
        rows = [
            ("RandomStore", Decimal("25.00"), date(2026, 3, 5)),
        ]
        session = _make_mock_session_for_recurring(rows)
        # Only one execute call (SELECT) since no recurring found
        session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=rows))
        )

        tracker = SubscriptionTracker()
        recurring, txn_map = await tracker.detect_recurring(session)

        assert len(recurring) == 0
        assert len(txn_map) == 0

    @pytest.mark.asyncio
    async def test_weekly_cycle_detected(self) -> None:
        """Weekly transactions should be detected as weekly cycle."""
        base = date(2026, 1, 5)
        rows = [
            ("GymPass", Decimal("5.00"), base + timedelta(days=7 * i))
            for i in range(6)
        ]
        session = _make_mock_session_for_recurring(rows)
        tracker = SubscriptionTracker()

        recurring, _ = await tracker.detect_recurring(session)

        assert len(recurring) == 1
        assert recurring[0].cycle == "weekly"

    @pytest.mark.asyncio
    async def test_batch_update_is_recurring(self) -> None:
        """Recurring detection should batch-update is_recurring=True."""
        rows = [
            ("Netflix", Decimal("15.49"), date(2026, 1, 15)),
            ("Netflix", Decimal("15.49"), date(2026, 2, 15)),
            ("Netflix", Decimal("15.49"), date(2026, 3, 15)),
        ]
        session = _make_mock_session_for_recurring(rows)
        tracker = SubscriptionTracker()

        await tracker.detect_recurring(session)

        # Should have 2 execute calls: SELECT + UPDATE
        assert session.execute.await_count == 2
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_two_months_with_cycle_detected(self) -> None:
        """Merchant with 2 transactions but consistent interval → detected via cycle."""
        rows = [
            ("NewSub", Decimal("10.00"), date(2026, 2, 1)),
            ("NewSub", Decimal("10.00"), date(2026, 3, 1)),
        ]
        session = _make_mock_session_for_recurring(rows)
        tracker = SubscriptionTracker()

        recurring, _ = await tracker.detect_recurring(session)

        # 2 distinct months < 3, but cycle detection should match "monthly"
        assert len(recurring) == 1
        assert recurring[0].cycle == "monthly"


# ---------------------------------------------------------------------------
# Tests: get_subscription_summary
# ---------------------------------------------------------------------------


class TestGetSubscriptionSummary:
    @pytest.mark.asyncio
    async def test_monthly_total_calculation(self) -> None:
        """Verify estimated_monthly_total calculation per cycle type."""
        tracker = SubscriptionTracker()

        # Mock detect_recurring to return known charges
        mock_recurring = [
            RecurringCharge("Weekly", 10.00, "weekly", date(2026, 3, 10), date(2026, 3, 17)),
            RecurringCharge("Monthly", 15.00, "monthly", date(2026, 3, 1), date(2026, 4, 1)),
            RecurringCharge("Annual", 120.00, "annual", date(2026, 1, 1), date(2027, 1, 1)),
        ]
        mock_txn_map: dict[str, list[tuple[float, date]]] = {
            "Weekly": [(10.00, date(2026, 3, 10))],
            "Monthly": [(15.00, date(2026, 3, 1))],
            "Annual": [(120.00, date(2026, 1, 1))],
        }

        tracker.detect_recurring = AsyncMock(  # type: ignore[method-assign]
            return_value=(mock_recurring, mock_txn_map),
        )
        tracker.check_price_changes = MagicMock(return_value=[])  # type: ignore[method-assign]

        session = AsyncMock()
        result = await tracker.get_subscription_summary(session)

        # weekly: 10*4.33=43.3, monthly: 15, annual: 120/12=10
        expected_total = round(10.00 * 4.33 + 15.00 + 120.00 / 12, 2)
        assert result["estimated_monthly_total"] == expected_total
        assert result["subscription_count"] == 3
        assert len(result["recurring_charges"]) == 3
        assert len(result["price_changes"]) == 0

    @pytest.mark.asyncio
    async def test_empty_transactions(self) -> None:
        """No recurring transactions → empty summary."""
        tracker = SubscriptionTracker()
        tracker.detect_recurring = AsyncMock(  # type: ignore[method-assign]
            return_value=([], {}),
        )
        tracker.check_price_changes = MagicMock(return_value=[])  # type: ignore[method-assign]

        session = AsyncMock()
        result = await tracker.get_subscription_summary(session)

        assert result["subscription_count"] == 0
        assert result["estimated_monthly_total"] == 0.0
