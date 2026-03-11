"""Repository for the finance_summaries table."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FinanceSummary

log = structlog.get_logger()


class FinanceSummaryRepository:
    """CRUD operations for the ``finance_summaries`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> FinanceSummary:
        """Insert a new finance summary."""
        summary = FinanceSummary(**kwargs)
        self._session.add(summary)
        await self._session.flush()
        return summary

    async def get_latest(self, period_type: str = "week") -> FinanceSummary | None:
        """Fetch the most recent finance summary of a given period type."""
        result = await self._session.execute(
            select(FinanceSummary)
            .where(FinanceSummary.period_type == period_type)
            .order_by(FinanceSummary.period_start.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_period(
        self,
        period_type: str,
        period_start: date,
    ) -> FinanceSummary | None:
        """Fetch a specific finance summary by period type and start date."""
        result = await self._session.execute(
            select(FinanceSummary).where(
                FinanceSummary.period_type == period_type,
                FinanceSummary.period_start == period_start,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, period_type: str, period_start: date, **kwargs: Any) -> FinanceSummary:
        """Insert or update a finance summary keyed on (period_type, period_start).

        Matches the select-then-branch upsert pattern used by
        ``ContactRepository.upsert()`` and ``ConfigRepository.set()``.
        """
        existing = await self.get_by_period(period_type, period_start)
        if existing is not None:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            await self._session.flush()
            log.info("finance_summary_updated", period_type=period_type, period_start=period_start.isoformat())
            return existing

        summary = FinanceSummary(period_type=period_type, period_start=period_start, **kwargs)
        self._session.add(summary)
        await self._session.flush()
        log.info("finance_summary_created", period_type=period_type, period_start=period_start.isoformat())
        return summary
