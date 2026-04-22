"""Anomaly detection — flags unusual transactions for user review.

Utility class used by FinanceAgent and the scheduler. Not a standalone agent.
HC-04: Strictly read-only — no modifications to any financial data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()

_LARGE_TXN_MULTIPLIER = 2.0  # amount > 2x category avg
_CATEGORY_SPIKE_THRESHOLD = 0.50  # single txn > 50% of monthly category spend
_OFF_PATTERN_MULTIPLIER = 3.0  # amount > 3x usual at a recurring merchant


@dataclass
class Anomaly:
    transaction_id: str
    merchant: str
    amount: float
    date: date
    reason: str  # "large_transaction", "new_merchant", "category_spike", "off_pattern"
    details: str
    severity: str  # "info", "warning"


def _merchant_name(txn: Any) -> str:
    """Extract effective merchant name from a Transaction ORM object."""
    return txn.merchant_name or txn.counterparty_name or "Unknown"


class AnomalyDetector:
    """Detects unusual individual transactions."""

    async def scan_recent(
        self,
        session: AsyncSession,
        lookback_days: int = 1,
    ) -> list[Anomaly]:
        """Scan recent transactions for anomalies.

        Pre-fetches all needed aggregates in bulk, then checks each transaction
        in memory. Includes pending transactions for early warning.
        ``lookback_days=1`` checks today and yesterday.
        """
        from db.models import Transaction

        cutoff = date.today() - timedelta(days=lookback_days)

        result = await session.execute(
            select(Transaction)
            .where(
                Transaction.transaction_date >= cutoff,
                Transaction.amount > 0,
            )
            .order_by(Transaction.transaction_date.desc())
        )
        recent_txns = list(result.scalars().all())

        if not recent_txns:
            return []

        # Pre-fetch aggregates in bulk to avoid N+1 queries
        stats = await self._fetch_aggregate_stats(session)

        anomalies: list[Anomaly] = []
        for txn in recent_txns:
            anomaly = self._check_transaction_with_stats(txn, stats)
            if anomaly is not None:
                anomalies.append(anomaly)

        if anomalies:
            log.info(
                "anomalies_detected",
                count=len(anomalies),
                warnings=sum(1 for a in anomalies if a.severity == "warning"),
            )
        return anomalies

    async def check_transaction(
        self,
        session: AsyncSession,
        txn: Any,
    ) -> Anomaly | None:
        """Run all anomaly rules against a single transaction.

        Returns the highest-severity anomaly found, or ``None``.
        Fetches its own stats — use ``scan_recent`` for batch processing.
        """
        stats = await self._fetch_aggregate_stats(session)
        return self._check_transaction_with_stats(txn, stats)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_aggregate_stats(
        self,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Pre-fetch all aggregates needed for anomaly rules.

        Returns a dict with:
        - merchant_counts: {merchant_name: total_transaction_count}
        - category_avgs: {category: avg_amount} (90 day, non-pending, spending)
        - category_month_totals: {(category, year, month): total_amount}
        - recurring_merchant_avgs: {merchant_name: avg_amount}
        """
        from db.models import Transaction

        lookback_90 = date.today() - timedelta(days=90)
        merchant_col = func.coalesce(Transaction.merchant_name, Transaction.counterparty_name, "Unknown")

        # 1. Merchant transaction counts (all time, for new-merchant detection)
        merchant_count_result = await session.execute(
            select(
                merchant_col.label("merchant"),
                func.count().label("cnt"),
            ).group_by(merchant_col)
        )
        merchant_counts = {row.merchant: row.cnt for row in merchant_count_result.all()}

        # 2. Category averages (90 days, non-pending, spending)
        cat_avg_result = await session.execute(
            select(
                Transaction.category,
                func.avg(Transaction.amount).label("avg_amt"),
            )
            .where(
                Transaction.transaction_date >= lookback_90,
                Transaction.amount > 0,
                Transaction.pending == False,  # noqa: E712
                Transaction.category.is_not(None),
            )
            .group_by(Transaction.category)
        )
        category_avgs = {row.category: float(row.avg_amt) for row in cat_avg_result.all()}

        # 3. Monthly category totals (for category spike detection)
        cat_month_result = await session.execute(
            select(
                Transaction.category,
                func.extract("year", Transaction.transaction_date).label("yr"),
                func.extract("month", Transaction.transaction_date).label("mo"),
                func.sum(Transaction.amount).label("total"),
            )
            .where(
                Transaction.transaction_date >= lookback_90,
                Transaction.amount > 0,
                Transaction.pending == False,  # noqa: E712
                Transaction.category.is_not(None),
            )
            .group_by(
                Transaction.category,
                func.extract("year", Transaction.transaction_date),
                func.extract("month", Transaction.transaction_date),
            )
        )
        category_month_totals = {
            (row.category, int(row.yr), int(row.mo)): float(row.total) for row in cat_month_result.all()
        }

        # 4. Recurring merchant averages (for off-pattern detection)
        recurring_avg_result = await session.execute(
            select(
                merchant_col.label("merchant"),
                func.avg(Transaction.amount).label("avg_amt"),
            )
            .where(
                Transaction.is_recurring == True,  # noqa: E712
                Transaction.amount > 0,
                Transaction.pending == False,  # noqa: E712
            )
            .group_by(merchant_col)
        )
        recurring_merchant_avgs = {row.merchant: float(row.avg_amt) for row in recurring_avg_result.all()}

        return {
            "merchant_counts": merchant_counts,
            "category_avgs": category_avgs,
            "category_month_totals": category_month_totals,
            "recurring_merchant_avgs": recurring_merchant_avgs,
        }

    def _check_transaction_with_stats(
        self,
        txn: Any,
        stats: dict[str, Any],
    ) -> Anomaly | None:
        """Run all anomaly rules using pre-fetched stats.

        Collects all matching anomalies and returns the highest severity.
        """
        merchant = _merchant_name(txn)
        amount = float(txn.amount)
        txn_id = str(txn.id)
        txn_date = txn.transaction_date

        candidates: list[Anomaly] = []

        # --- Rule 1: New merchant ---
        # A merchant with count==1 means this is the only transaction we've ever
        # seen for it (the current one), so it's "new".
        merchant_count = stats["merchant_counts"].get(merchant, 0)
        if merchant_count <= 1:
            candidates.append(
                Anomaly(
                    transaction_id=txn_id,
                    merchant=merchant,
                    amount=amount,
                    date=txn_date,
                    reason="new_merchant",
                    details=(f"New charge of ${amount:.2f} at {merchant} — first time seeing this merchant"),
                    severity="info",
                )
            )

        # --- Rule 2: Off-pattern at recurring merchant ---
        if txn.is_recurring:
            usual_avg = stats["recurring_merchant_avgs"].get(merchant)
            if usual_avg and amount > usual_avg * _OFF_PATTERN_MULTIPLIER:
                candidates.append(
                    Anomaly(
                        transaction_id=txn_id,
                        merchant=merchant,
                        amount=amount,
                        date=txn_date,
                        reason="off_pattern",
                        details=(
                            f"${amount:.2f} at {merchant} is {amount / usual_avg:.1f}x the usual ${usual_avg:.2f}"
                        ),
                        severity="warning",
                    )
                )

        # --- Rule 3: Large transaction (>2x category average) ---
        if txn.category:
            cat_avg = stats["category_avgs"].get(txn.category)
            if cat_avg and amount > cat_avg * _LARGE_TXN_MULTIPLIER:
                candidates.append(
                    Anomaly(
                        transaction_id=txn_id,
                        merchant=merchant,
                        amount=amount,
                        date=txn_date,
                        reason="large_transaction",
                        details=(
                            f"${amount:.2f} at {merchant} is {amount / cat_avg:.1f}x "
                            f"the average for {txn.category} (${cat_avg:.2f})"
                        ),
                        severity="warning",
                    )
                )

        # --- Rule 4: Category spike (>50% of month's category spend) ---
        if txn.category:
            month_key = (txn.category, txn_date.year, txn_date.month)
            month_total = stats["category_month_totals"].get(month_key, 0)
            # Subtract this transaction's own amount from the month total
            month_total_excl = month_total - amount
            # Spike = this txn alone exceeds 50% of *other* spending in the category
            if month_total_excl > 0 and amount > month_total_excl * _CATEGORY_SPIKE_THRESHOLD:
                candidates.append(
                    Anomaly(
                        transaction_id=txn_id,
                        merchant=merchant,
                        amount=amount,
                        date=txn_date,
                        reason="category_spike",
                        details=(
                            f"${amount:.2f} at {merchant} is "
                            f"{amount / month_total * 100:.0f}% of this month's "
                            f"{txn.category} spending (${month_total:.2f})"
                        ),
                        severity="warning",
                    )
                )

        if not candidates:
            return None

        # Return highest-severity anomaly (warning > info)
        warnings = [a for a in candidates if a.severity == "warning"]
        return warnings[0] if warnings else candidates[0]
