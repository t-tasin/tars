"""Repository for the health_data table."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import HealthData

log = structlog.get_logger()


class HealthDataRepository:
    """CRUD operations for the ``health_data`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> HealthData:
        """Insert a new health data record."""
        record = HealthData(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_recent(self, days: int = 7, limit: int = 100) -> list[HealthData]:
        """Fetch health data from the last N days."""
        cutoff = date.today() - timedelta(days=days)
        result = await self._session.execute(
            select(HealthData)
            .where(HealthData.recorded_date >= cutoff)
            .order_by(HealthData.recorded_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_date_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[HealthData]:
        """Fetch health data within a date range (inclusive)."""
        result = await self._session.execute(
            select(HealthData)
            .where(
                HealthData.recorded_date >= start_date,
                HealthData.recorded_date <= end_date,
            )
            .order_by(HealthData.recorded_date.desc())
        )
        return list(result.scalars().all())

    async def get_by_metric(
        self,
        data_type: str,
        days: int = 30,
    ) -> list[HealthData]:
        """Fetch records for a specific health metric over the last N days."""
        cutoff = date.today() - timedelta(days=days)
        result = await self._session.execute(
            select(HealthData)
            .where(
                HealthData.data_type == data_type,
                HealthData.recorded_date >= cutoff,
            )
            .order_by(HealthData.recorded_date.desc())
        )
        return list(result.scalars().all())

    async def bulk_create(self, records: list[dict[str, Any]]) -> int:
        """Insert multiple health data records. Returns count inserted."""
        count = 0
        for data in records:
            self._session.add(HealthData(**data))
            count += 1
        await self._session.flush()
        return count
