"""Repository for the audit_log table."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog

log = structlog.get_logger()


class AuditLogRepository:
    """CRUD operations for the ``audit_log`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> AuditLog:
        """Insert a new audit log entry."""
        record = AuditLog(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_recent(self, limit: int = 50) -> list[AuditLog]:
        """Fetch the most recent audit log entries."""
        result = await self._session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def get_by_action(
        self,
        action_type: str,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Fetch audit entries filtered by action_type."""
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.action_type == action_type)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_entity(
        self,
        target: str,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Fetch audit entries for a specific target entity."""
        result = await self._session.execute(
            select(AuditLog).where(AuditLog.target == target).order_by(AuditLog.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
