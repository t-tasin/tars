"""Repository for the budgets table."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Budget

log = structlog.get_logger()


class BudgetRepository:
    """CRUD operations for the ``budgets`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, category: str, monthly_limit: Decimal) -> Budget:
        """Insert a new budget record. Category is stored lowercase."""
        budget = Budget(category=category.lower(), monthly_limit=monthly_limit)
        self._session.add(budget)
        await self._session.flush()
        log.info("budget_created", category=budget.category, monthly_limit=str(monthly_limit))
        return budget

    async def get_by_id(self, budget_id: UUID) -> Budget | None:
        """Fetch a budget by primary key."""
        return await self._session.get(Budget, budget_id)

    async def get_active(self) -> list[Budget]:
        """Fetch all active budgets."""
        result = await self._session.execute(
            select(Budget)
            .where(Budget.active.is_(True))
            .order_by(Budget.category)
        )
        return list(result.scalars().all())

    async def get_by_category(self, category: str) -> Budget | None:
        """Fetch the active budget for a given category (case-insensitive)."""
        result = await self._session.execute(
            select(Budget)
            .where(Budget.category == category.lower(), Budget.active.is_(True))
        )
        return result.scalar_one_or_none()

    async def update_limit(
        self, category: str, monthly_limit: Decimal
    ) -> Budget | None:
        """Update the monthly limit for an active budget category."""
        budget = await self.get_by_category(category)
        if budget:
            budget.monthly_limit = monthly_limit
            await self._session.flush()
            log.info(
                "budget_updated",
                category=budget.category,
                monthly_limit=str(monthly_limit),
            )
        return budget

    async def deactivate(self, budget_id: UUID) -> Budget | None:
        """Soft-delete a budget by setting active=False."""
        budget = await self.get_by_id(budget_id)
        if budget:
            budget.active = False
            await self._session.flush()
            log.info("budget_deactivated", budget_id=str(budget_id), category=budget.category)
        return budget

    async def deactivate_by_category(self, category: str) -> bool:
        """Soft-delete the active budget for a category. Returns True if found."""
        budget = await self.get_by_category(category)
        if budget:
            budget.active = False
            await self._session.flush()
            log.info("budget_deactivated", category=budget.category)
            return True
        return False
