"""Repository for the system_health_log table."""

from __future__ import annotations

from typing import Any

import structlog
from shared.constants import HealthStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SystemHealthLog

log = structlog.get_logger()


class SystemHealthLogRepository:
    """CRUD operations for the ``system_health_log`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> SystemHealthLog:
        """Insert a new health check record."""
        record = SystemHealthLog(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_latest(self) -> dict[str, Any]:
        """Get the most recent health check per target.

        Returns a dict keyed by target name with latest status info::

            {"postgres": {"status": "healthy", "response_time_ms": 12, ...}, ...}
        """
        # Use a subquery to find the latest checked_at per target
        from sqlalchemy import func

        subq = (
            select(
                SystemHealthLog.target,
                func.max(SystemHealthLog.checked_at).label("max_checked"),
            )
            .group_by(SystemHealthLog.target)
            .subquery()
        )

        stmt = select(SystemHealthLog).join(
            subq,
            (SystemHealthLog.target == subq.c.target) & (SystemHealthLog.checked_at == subq.c.max_checked),
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return {
            row.target: {
                "status": str(row.status),
                "response_time_ms": row.response_time_ms,
                "details": row.details,
                "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            }
            for row in rows
        }

    async def get_by_status(
        self,
        status: HealthStatus,
        limit: int = 50,
    ) -> list[SystemHealthLog]:
        """Fetch health logs filtered by status."""
        result = await self._session.execute(
            select(SystemHealthLog)
            .where(SystemHealthLog.status == status)
            .order_by(SystemHealthLog.checked_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50) -> list[SystemHealthLog]:
        """Fetch the most recent health check records."""
        result = await self._session.execute(
            select(SystemHealthLog).order_by(SystemHealthLog.checked_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
