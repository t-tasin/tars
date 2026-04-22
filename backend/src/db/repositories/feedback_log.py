"""Repository for the feedback_log table."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FeedbackLog

log = structlog.get_logger()


class FeedbackLogRepository:
    """CRUD operations for the ``feedback_log`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> FeedbackLog:
        """Insert a new feedback record."""
        record = FeedbackLog(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_recent(self, limit: int = 50) -> list[FeedbackLog]:
        """Fetch the most recent feedback entries."""
        result = await self._session.execute(select(FeedbackLog).order_by(FeedbackLog.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def get_by_agent(
        self,
        feedback_type: str,
        limit: int = 50,
    ) -> list[FeedbackLog]:
        """Fetch feedback entries filtered by feedback_type."""
        result = await self._session.execute(
            select(FeedbackLog)
            .where(FeedbackLog.feedback_type == feedback_type)
            .order_by(FeedbackLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
