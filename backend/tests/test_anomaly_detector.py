"""Tests for AnomalyDetector — unusual transaction flagging."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.anomaly_detector import AnomalyDetector, _merchant_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txn(
    *,
    txn_id: str = "txn_001",
    merchant_name: str | None = "Starbucks",
    counterparty_name: str | None = None,
    amount: float = 25.00,
    category: str | None = "dining",
    transaction_date: date = date(2026, 3, 10),
    is_recurring: bool = False,
    pending: bool = False,
) -> MagicMock:
    """Build a mock Transaction ORM object."""
    txn = MagicMock()
    txn.id = txn_id
    txn.merchant_name = merchant_name
    txn.counterparty_name = counterparty_name
    txn.amount = amount
    txn.category = category
    txn.transaction_date = transaction_date
    txn.is_recurring = is_recurring
    txn.pending = pending
    return txn


def _make_stats(
    *,
    merchant_counts: dict[str, int] | None = None,
    category_avgs: dict[str, float] | None = None,
    category_month_totals: dict[tuple[str, int, int], float] | None = None,
    recurring_merchant_avgs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build a stats dict as returned by _fetch_aggregate_stats."""
    return {
        "merchant_counts": merchant_counts or {},
        "category_avgs": category_avgs or {},
        "category_month_totals": category_month_totals or {},
        "recurring_merchant_avgs": recurring_merchant_avgs or {},
    }


# ---------------------------------------------------------------------------
# Tests: _merchant_name helper
# ---------------------------------------------------------------------------


class TestMerchantName:
    def test_prefers_merchant_name(self) -> None:
        txn = _make_txn(merchant_name="Starbucks", counterparty_name="SBux")
        assert _merchant_name(txn) == "Starbucks"

    def test_falls_back_to_counterparty(self) -> None:
        txn = _make_txn(merchant_name=None, counterparty_name="SBux")
        assert _merchant_name(txn) == "SBux"

    def test_falls_back_to_unknown(self) -> None:
        txn = _make_txn(merchant_name=None, counterparty_name=None)
        assert _merchant_name(txn) == "Unknown"


# ---------------------------------------------------------------------------
# Tests: _check_transaction_with_stats (pure function)
# ---------------------------------------------------------------------------


class TestCheckTransactionWithStats:
    def setup_method(self) -> None:
        self.detector = AnomalyDetector()

    def test_large_transaction_flagged(self) -> None:
        """Amount > 2x category average → 'large_transaction' warning."""
        txn = _make_txn(amount=150.00, category="dining")
        stats = _make_stats(
            merchant_counts={"Starbucks": 5},
            category_avgs={"dining": 30.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        assert anomaly.reason == "large_transaction"
        assert anomaly.severity == "warning"
        assert anomaly.amount == 150.00

    def test_new_merchant_flagged(self) -> None:
        """First-time merchant (count <= 1) → 'new_merchant' info."""
        txn = _make_txn(merchant_name="NewPlace", amount=25.00)
        stats = _make_stats(
            merchant_counts={"NewPlace": 1},
            category_avgs={"dining": 30.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        assert anomaly.reason == "new_merchant"
        assert anomaly.severity == "info"

    def test_new_merchant_zero_count(self) -> None:
        """Merchant not in counts at all → 'new_merchant' info."""
        txn = _make_txn(merchant_name="BrandNew", amount=25.00)
        stats = _make_stats(
            merchant_counts={},
            category_avgs={"dining": 30.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        assert anomaly.reason == "new_merchant"

    def test_category_spike_flagged(self) -> None:
        """Single txn > 50% of month's other category spend → 'category_spike' warning."""
        txn = _make_txn(amount=200.00, category="dining", transaction_date=date(2026, 3, 10))
        stats = _make_stats(
            merchant_counts={"Starbucks": 10},
            category_avgs={"dining": 50.00},
            category_month_totals={("dining", 2026, 3): 500.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        # category_spike or large_transaction — both could match, warning wins
        assert anomaly.severity == "warning"

    def test_off_pattern_at_recurring_merchant(self) -> None:
        """Recurring merchant charge > 3x average → 'off_pattern' warning."""
        txn = _make_txn(
            merchant_name="Netflix",
            amount=50.00,
            is_recurring=True,
        )
        stats = _make_stats(
            merchant_counts={"Netflix": 12},
            category_avgs={"dining": 30.00},
            recurring_merchant_avgs={"Netflix": 15.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        assert anomaly.reason == "off_pattern"
        assert anomaly.severity == "warning"

    def test_normal_transaction_no_flag(self) -> None:
        """Transaction within expected ranges → no anomaly."""
        txn = _make_txn(amount=25.00, category="dining")
        stats = _make_stats(
            merchant_counts={"Starbucks": 10},
            category_avgs={"dining": 30.00},
            category_month_totals={("dining", 2026, 3): 500.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is None

    def test_warning_wins_over_info(self) -> None:
        """When both new_merchant (info) and large_transaction (warning) match,
        warning should be returned."""
        txn = _make_txn(merchant_name="NewExpensive", amount=200.00, category="dining")
        stats = _make_stats(
            merchant_counts={"NewExpensive": 1},  # new → info
            category_avgs={"dining": 30.00},  # 200 > 30*2 → warning
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is not None
        assert anomaly.severity == "warning"
        assert anomaly.reason == "large_transaction"

    def test_no_category_no_category_rules(self) -> None:
        """Transaction without category → skip large_transaction and category_spike rules."""
        txn = _make_txn(amount=500.00, category=None)
        stats = _make_stats(
            merchant_counts={"Starbucks": 10},
            category_avgs={"dining": 30.00},
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        assert anomaly is None

    def test_category_spike_not_triggered_when_only_txn(self) -> None:
        """When this txn IS the entire month total, month_total_excl == 0, no spike."""
        txn = _make_txn(amount=100.00, category="dining", transaction_date=date(2026, 3, 10))
        stats = _make_stats(
            merchant_counts={"Starbucks": 10},
            category_avgs={"dining": 50.00},
            category_month_totals={("dining", 2026, 3): 100.00},  # exactly this txn
        )

        anomaly = self.detector._check_transaction_with_stats(txn, stats)

        # month_total_excl = 100 - 100 = 0, so spike rule doesn't trigger
        # large_transaction: 100 > 50*2 = 100 → NOT strictly greater, so no flag
        assert anomaly is None


# ---------------------------------------------------------------------------
# Tests: scan_recent (requires mock session)
# ---------------------------------------------------------------------------


class TestScanRecent:
    @pytest.mark.asyncio
    async def test_returns_anomalies_for_flagged_transactions(self) -> None:
        """scan_recent should find anomalies in recent transactions."""
        detector = AnomalyDetector()

        txn = _make_txn(merchant_name="NewPlace", amount=500.00, category="dining")

        # Mock session
        session = AsyncMock()

        # First execute: SELECT recent transactions
        txn_result = MagicMock()
        txn_result.scalars.return_value = txn_result
        txn_result.all.return_value = [txn]

        # Remaining 4 executes: aggregate stats queries
        empty_result = MagicMock()
        empty_result.all.return_value = []

        session.execute = AsyncMock(
            side_effect=[txn_result, empty_result, empty_result, empty_result, empty_result],
        )

        anomalies = await detector.scan_recent(session, lookback_days=1)

        # NewPlace has count=0 → new_merchant
        assert len(anomalies) >= 1
        assert anomalies[0].reason == "new_merchant"

    @pytest.mark.asyncio
    async def test_empty_transactions_returns_empty(self) -> None:
        """No recent transactions → no anomalies."""
        detector = AnomalyDetector()

        session = AsyncMock()
        txn_result = MagicMock()
        txn_result.scalars.return_value = txn_result
        txn_result.all.return_value = []

        session.execute = AsyncMock(return_value=txn_result)

        anomalies = await detector.scan_recent(session, lookback_days=1)

        assert anomalies == []

    @pytest.mark.asyncio
    async def test_check_transaction_single(self) -> None:
        """check_transaction delegates to _check_transaction_with_stats."""
        detector = AnomalyDetector()

        txn = _make_txn(merchant_name="NewMerchant", amount=25.00)

        session = AsyncMock()
        # 4 aggregate stat queries all return empty
        empty_result = MagicMock()
        empty_result.all.return_value = []
        session.execute = AsyncMock(return_value=empty_result)

        anomaly = await detector.check_transaction(session, txn)

        # New merchant (count=0)
        assert anomaly is not None
        assert anomaly.reason == "new_merchant"
