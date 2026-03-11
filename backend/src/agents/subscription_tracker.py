"""Subscription tracking — recurring charge detection and price change alerts.

Utility class used by FinanceAgent and the scheduler. Not a standalone agent.
HC-04: Updates only the internal ``is_recurring`` flag on transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()

# Cycle definitions: (name, target_days, tolerance_days)
_CYCLES = [
    ("weekly", 7, 3),
    ("biweekly", 14, 3),
    ("monthly", 30, 3),
    ("annual", 365, 15),
]

_PRICE_CHANGE_THRESHOLD = 0.10  # 10%


@dataclass
class RecurringCharge:
    merchant: str
    amount: float
    cycle: str  # "weekly", "biweekly", "monthly", "annual"
    last_charged: date
    next_expected: date


@dataclass
class PriceChange:
    merchant: str
    previous_avg: float
    current_amount: float
    change_pct: float


def _merchant_coalesce(Transaction: type) -> Any:
    """COALESCE(merchant_name, counterparty_name, 'Unknown') expression."""
    return func.coalesce(
        Transaction.merchant_name, Transaction.counterparty_name, "Unknown"
    )


def _detect_cycle(dates: list[date]) -> str | None:
    """Detect the most likely billing cycle from a sorted list of transaction dates.

    Returns the cycle name or ``None`` if no consistent pattern is found.
    """
    if len(dates) < 2:
        return None

    intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    med_interval = median(intervals)

    for cycle_name, target_days, tolerance in _CYCLES:
        if abs(med_interval - target_days) <= tolerance:
            return cycle_name

    return None


def _next_expected(last: date, cycle: str) -> date:
    """Estimate the next expected charge date from the cycle."""
    offsets = {"weekly": 7, "biweekly": 14, "monthly": 30, "annual": 365}
    return last + timedelta(days=offsets.get(cycle, 30))


class SubscriptionTracker:
    """Detects recurring charges and price changes in transaction history."""

    async def detect_recurring(
        self, session: AsyncSession,
    ) -> tuple[list[RecurringCharge], dict[str, list[tuple[float, date]]]]:
        """Identify recurring merchants from the last 90 days of transactions.

        A merchant is recurring if it appears in 3+ distinct months OR has
        charges with consistent intervals (within tolerance of a known cycle).

        Side effect: sets ``is_recurring = True`` on matching transactions
        within the 90-day window.

        Returns:
            A tuple of (recurring_charges, recurring_merchant_txns) where
            recurring_merchant_txns maps merchant name to sorted (amount, date)
            pairs — used by ``check_price_changes`` to avoid a redundant query.
        """
        from db.models import Transaction

        cutoff = date.today() - timedelta(days=90)
        merchant_col = _merchant_coalesce(Transaction)

        # Fetch all non-pending spending transactions grouped by merchant
        result = await session.execute(
            select(
                merchant_col.label("merchant"),
                Transaction.amount,
                Transaction.transaction_date,
            )
            .where(
                Transaction.transaction_date >= cutoff,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount > 0,
            )
            .order_by(merchant_col, Transaction.transaction_date)
        )
        rows = result.all()

        # Group by merchant
        merchant_txns: dict[str, list[tuple[Decimal, date]]] = {}
        for merchant, amount, txn_date in rows:
            merchant_txns.setdefault(merchant, []).append((amount, txn_date))

        recurring: list[RecurringCharge] = []
        recurring_merchants: list[str] = []
        recurring_merchant_txns: dict[str, list[tuple[float, date]]] = {}

        for merchant, txns in merchant_txns.items():
            # Sort by date for correct ordering
            sorted_txns = sorted(txns, key=lambda t: t[1])
            dates = [t[1] for t in sorted_txns]

            # Check: 3+ distinct months
            distinct_months = len({(d.year, d.month) for d in dates})
            cycle = _detect_cycle(dates)

            if distinct_months >= 3 or cycle is not None:
                effective_cycle = cycle or "monthly"
                last_date = dates[-1]
                latest_amount = float(sorted_txns[-1][0])

                recurring.append(
                    RecurringCharge(
                        merchant=merchant,
                        amount=latest_amount,
                        cycle=effective_cycle,
                        last_charged=last_date,
                        next_expected=_next_expected(last_date, effective_cycle),
                    )
                )
                recurring_merchants.append(merchant)
                recurring_merchant_txns[merchant] = [
                    (float(t[0]), t[1]) for t in sorted_txns
                ]

        # Batch-update is_recurring on matching transactions (within the 90-day window only)
        if recurring_merchants:
            await session.execute(
                update(Transaction)
                .where(
                    _merchant_coalesce(Transaction).in_(recurring_merchants),
                    Transaction.transaction_date >= cutoff,
                    Transaction.is_recurring == False,  # noqa: E712
                )
                .values(is_recurring=True)
            )
            await session.flush()

        log.info(
            "subscription_detection_complete",
            recurring_count=len(recurring),
            merchants_updated=len(recurring_merchants),
        )
        return recurring, recurring_merchant_txns

    def check_price_changes(
        self,
        recurring_merchant_txns: dict[str, list[tuple[float, date]]],
    ) -> list[PriceChange]:
        """Compare latest charge to rolling average for recurring merchants.

        Flags merchants where the latest charge is >10% above the average
        of all previous charges.

        Uses pre-fetched data from ``detect_recurring`` to avoid a redundant
        DB query.
        """
        changes: list[PriceChange] = []
        for merchant, txns in recurring_merchant_txns.items():
            if len(txns) < 2:
                continue

            previous_amounts = [t[0] for t in txns[:-1]]
            current_amount = txns[-1][0]
            prev_avg = sum(previous_amounts) / len(previous_amounts)

            if prev_avg == 0:
                continue

            change_pct = (current_amount - prev_avg) / prev_avg
            if change_pct > _PRICE_CHANGE_THRESHOLD:
                changes.append(
                    PriceChange(
                        merchant=merchant,
                        previous_avg=round(prev_avg, 2),
                        current_amount=round(current_amount, 2),
                        change_pct=round(change_pct * 100, 1),
                    )
                )

        if changes:
            log.info(
                "price_changes_detected",
                count=len(changes),
                merchants=[c.merchant for c in changes],
            )
        return changes

    async def get_subscription_summary(
        self, session: AsyncSession,
    ) -> dict[str, Any]:
        """Return a combined subscription summary with recurring charges and price changes."""
        recurring, recurring_merchant_txns = await self.detect_recurring(session)
        price_changes = self.check_price_changes(recurring_merchant_txns)

        total_monthly = 0.0
        for charge in recurring:
            if charge.cycle == "weekly":
                total_monthly += charge.amount * 4.33
            elif charge.cycle == "biweekly":
                total_monthly += charge.amount * 2.17
            elif charge.cycle == "monthly":
                total_monthly += charge.amount
            elif charge.cycle == "annual":
                total_monthly += charge.amount / 12

        return {
            "recurring_charges": [
                {
                    "merchant": r.merchant,
                    "amount": r.amount,
                    "cycle": r.cycle,
                    "last_charged": r.last_charged.isoformat(),
                    "next_expected": r.next_expected.isoformat(),
                }
                for r in recurring
            ],
            "estimated_monthly_total": round(total_monthly, 2),
            "price_changes": [
                {
                    "merchant": p.merchant,
                    "previous_avg": p.previous_avg,
                    "current": p.current_amount,
                    "increase_pct": p.change_pct,
                }
                for p in price_changes
            ],
            "subscription_count": len(recurring),
        }
