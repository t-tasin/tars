"""Finance API endpoints for T.A.R.S. Read-only spending data (HC-04)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import FinanceSummaryResponse
from src.db.models import FinanceSummary, Transaction
from src.dependencies import get_db, verify_auth
from src.utils.finance import pct_change, previous_period_start, resolve_period

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["finance"])


@router.get("/finance/summary", response_model=FinanceSummaryResponse)
async def get_finance_summary(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: Literal["day", "week", "month"] = "week",
    target_date: date | None = None,
) -> FinanceSummaryResponse:
    """Get spending summary for a given period. Read-only (HC-04)."""
    anchor = target_date or date.today()
    start_date, end_date = resolve_period(period, anchor)

    # Total spent
    total_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
            Transaction.pending == False,  # noqa: E712
            Transaction.amount > 0,
        )
    )
    total_spent = float(total_result.scalar_one())

    # Transactions list (most recent first, capped at 50)
    txn_result = await db.execute(
        select(
            Transaction.merchant_name,
            Transaction.amount,
            Transaction.category,
            Transaction.transaction_date,
        )
        .where(
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
            Transaction.pending == False,  # noqa: E712
        )
        .order_by(Transaction.transaction_date.desc(), Transaction.amount.desc())
        .limit(50)
    )
    transactions = [
        {
            "merchant": row.merchant_name or "Unknown",
            "amount": float(row.amount),
            "category": row.category,
            "date": row.transaction_date.isoformat(),
        }
        for row in txn_result.all()
    ]

    # Month-to-date total
    month_start = anchor.replace(day=1)
    mtd_result = await db.execute(
        select(
            func.coalesce(func.sum(Transaction.amount), 0),
            func.count(),
        ).where(
            Transaction.transaction_date >= month_start,
            Transaction.transaction_date <= anchor,
            Transaction.pending == False,  # noqa: E712
            Transaction.amount > 0,
        )
    )
    mtd_row = mtd_result.one()
    mtd_total = float(mtd_row[0])
    mtd_count = mtd_row[1]

    # Category breakdown for month-to-date
    cat_result = await db.execute(
        select(
            func.coalesce(Transaction.category, "Uncategorized"),
            func.sum(Transaction.amount),
        )
        .where(
            Transaction.transaction_date >= month_start,
            Transaction.transaction_date <= anchor,
            Transaction.pending == False,  # noqa: E712
            Transaction.amount > 0,
        )
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
    )
    by_category = {row[0]: float(row[1]) for row in cat_result.all()}

    # Trends — query finance_summaries for previous period comparison
    trends = await _fetch_trends(db, period, start_date, total_spent)

    # Alerts
    alerts = _build_alerts(total_spent, period, mtd_total)

    log.info(
        "finance_summary_fetched",
        period=period,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        total_spent=total_spent,
        transaction_count=len(transactions),
        has_trends=trends is not None,
    )

    return FinanceSummaryResponse(
        period=period,
        date=anchor.isoformat(),
        total_spent=total_spent,
        transactions=transactions,
        month_to_date={
            "total": mtd_total,
            "transaction_count": mtd_count,
            "by_category": by_category,
        },
        alerts=alerts,
        trends=trends,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_trends(
    db: AsyncSession,
    period: str,
    current_start: date,
    current_total: float,
) -> dict[str, Any] | None:
    """Query finance_summaries for the previous period and compute deltas."""
    prev_start = previous_period_start(period, current_start)
    result = await db.execute(
        select(FinanceSummary).where(
            FinanceSummary.period_type == period,
            FinanceSummary.period_start == prev_start,
        )
    )
    previous = result.scalar_one_or_none()
    if previous is None:
        return None

    prev_total = float(previous.total_spent)
    total_change_pct = pct_change(current_total, prev_total)

    return {
        "total_change_pct": total_change_pct,
        "previous_total": prev_total,
    }


def _build_alerts(total_spent: float, period: str, mtd_total: float) -> list[str]:
    """Generate spending alerts based on thresholds."""
    alerts: list[str] = []

    if period == "day" and total_spent > 200:
        alerts.append(f"High daily spending: ${total_spent:.2f}")
    elif period == "week" and total_spent > 1000:
        alerts.append(f"High weekly spending: ${total_spent:.2f}")

    if mtd_total > 3000:
        alerts.append(f"Month-to-date spending above $3,000: ${mtd_total:.2f}")

    return alerts
