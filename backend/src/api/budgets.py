"""Budget management API endpoints for T.A.R.S. Read-only analysis (HC-04)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import BudgetCreateRequest, BudgetListResponse, BudgetStatus
from db.models import Transaction
from db.repositories.budgets import BudgetRepository
from dependencies import get_db, verify_auth
from utils.finance import compute_budget_statuses

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["finance"])


@router.get("/finance/budgets", response_model=BudgetListResponse)
async def list_budgets(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BudgetListResponse:
    """List all active budgets with current month spending vs limit."""
    repo = BudgetRepository(db)
    budgets = await repo.get_active()
    spending = await _mtd_spending_by_category(db, budgets)
    statuses = compute_budget_statuses(budgets, spending)
    log.info("budgets_listed", count=len(statuses))
    return BudgetListResponse(budgets=[BudgetStatus(**s) for s in statuses])


@router.get("/finance/budgets/status", response_model=BudgetListResponse)
async def budget_status_summary(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BudgetListResponse:
    """Budget status summary — all categories with % used and days remaining."""
    repo = BudgetRepository(db)
    budgets = await repo.get_active()
    spending = await _mtd_spending_by_category(db, budgets)
    statuses = compute_budget_statuses(budgets, spending)
    log.info("budget_status_fetched", count=len(statuses))
    return BudgetListResponse(budgets=[BudgetStatus(**s) for s in statuses])


@router.post("/finance/budgets", response_model=BudgetStatus)
async def create_or_update_budget(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: BudgetCreateRequest,
) -> BudgetStatus:
    """Create or update a budget for a category."""
    repo = BudgetRepository(db)
    limit = Decimal(str(body.monthly_limit))
    existing = await repo.get_by_category(body.category)

    if existing:
        await repo.update_limit(body.category, limit)
    else:
        await repo.create(body.category, limit)

    # Return the current status for this budget
    budget = await repo.get_by_category(body.category)
    spending = await _mtd_spending_by_category(db, [budget])  # type: ignore[list-item]
    statuses = compute_budget_statuses([budget], spending)  # type: ignore[list-item]
    return BudgetStatus(**statuses[0])


@router.delete("/finance/budgets/{category}")
async def deactivate_budget(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str,
) -> dict[str, str]:
    """Deactivate a budget by category."""
    repo = BudgetRepository(db)
    found = await repo.deactivate_by_category(category)
    if not found:
        raise HTTPException(status_code=404, detail=f"No active budget for '{category}'")
    return {"status": "deactivated", "category": category.lower()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mtd_spending_by_category(
    db: AsyncSession,
    budgets: list[Any],
) -> dict[str, float]:
    """Query month-to-date spending grouped by category for the given budgets."""
    if not budgets:
        return {}

    today = date.today()
    month_start = today.replace(day=1)
    categories = [b.category.lower() for b in budgets]

    result = await db.execute(
        select(
            func.lower(Transaction.category),
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .where(
            func.lower(Transaction.category).in_(categories),
            Transaction.transaction_date >= month_start,
            Transaction.transaction_date <= today,
            Transaction.pending == False,  # noqa: E712
            Transaction.amount > 0,
        )
        .group_by(func.lower(Transaction.category))
    )
    return {row[0]: float(row[1]) for row in result.all()}
