"""Repository for the briefings table."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Briefing

log = structlog.get_logger()


class BriefingRepository:
    """CRUD operations for the ``briefings`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> Briefing:
        """Insert a new briefing record."""
        briefing = Briefing(**kwargs)
        self._session.add(briefing)
        await self._session.flush()
        return briefing

    async def get_latest(self, briefing_type: str = "morning") -> Briefing | None:
        """Fetch the most recent briefing of a given type."""
        result = await self._session.execute(
            select(Briefing)
            .where(Briefing.briefing_type == briefing_type)
            .order_by(Briefing.briefing_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_date(
        self,
        target_date: date,
        briefing_type: str = "morning",
    ) -> Briefing | None:
        """Fetch the briefing for a specific date and type."""
        result = await self._session.execute(
            select(Briefing).where(
                Briefing.briefing_date == target_date,
                Briefing.briefing_type == briefing_type,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, briefing_id: uuid.UUID) -> Briefing | None:
        """Fetch a briefing by primary key."""
        result = await self._session.execute(select(Briefing).where(Briefing.id == briefing_id))
        return result.scalar_one_or_none()
