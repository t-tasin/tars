"""Repository for the model_usage table."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ModelUsage

log = structlog.get_logger()


class ModelUsageRepository:
    """CRUD operations for the ``model_usage`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> ModelUsage:
        """Insert a new usage record."""
        record = ModelUsage(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_daily_totals(
        self,
        date: dt.date | None = None,
    ) -> dict[str, dict[str, int | str]]:
        """Get usage totals grouped by model for a given day."""
        target = date or dt.date.today()
        start = dt.datetime.combine(target, dt.time.min, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=1)

        stmt = (
            select(
                ModelUsage.model,
                func.count().label("calls"),
                func.coalesce(func.sum(ModelUsage.tokens_input), 0).label("tokens_input"),
                func.coalesce(func.sum(ModelUsage.tokens_output), 0).label("tokens_output"),
                func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("estimated_cost"),
            )
            .where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
            .group_by(ModelUsage.model)
        )
        result = await self._session.execute(stmt)
        return {
            row.model: {
                "calls": row.calls,
                "tokens_input": row.tokens_input,
                "tokens_output": row.tokens_output,
                "estimated_cost": str(row.estimated_cost),
            }
            for row in result.all()
        }

    async def get_weekly_totals(self) -> dict[str, int | float | str]:
        """Get usage totals for the current week (Monday-Sunday)."""
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        week_start = dt.datetime.combine(monday, dt.time.min, tzinfo=dt.timezone.utc)
        week_end = week_start + dt.timedelta(days=7)

        stmt = (
            select(
                ModelUsage.model,
                func.count().label("calls"),
                func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("estimated_cost"),
            )
            .where(ModelUsage.created_at >= week_start, ModelUsage.created_at < week_end)
            .group_by(ModelUsage.model)
        )
        result = await self._session.execute(stmt)

        claude_calls = 0
        gemini_calls = 0
        total_cost = Decimal("0")

        for row in result.all():
            total_cost += Decimal(str(row.estimated_cost))
            if row.model == "claude_code":
                claude_calls = row.calls
            else:
                gemini_calls += row.calls

        return {
            "week_start": monday.isoformat(),
            "claude_calls": claude_calls,
            "gemini_calls": gemini_calls,
            "total_calls": claude_calls + gemini_calls,
            "total_estimated_cost": str(total_cost),
        }

    async def get_by_model(
        self,
        model: str,
        limit: int = 50,
    ) -> list[ModelUsage]:
        """Fetch recent usage records for a specific model."""
        result = await self._session.execute(
            select(ModelUsage)
            .where(ModelUsage.model == model)
            .order_by(ModelUsage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_agent(
        self,
        agent_type: str,
        limit: int = 50,
    ) -> list[ModelUsage]:
        """Fetch recent usage records for a specific agent type."""
        result = await self._session.execute(
            select(ModelUsage)
            .where(ModelUsage.agent_type == agent_type)
            .order_by(ModelUsage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
