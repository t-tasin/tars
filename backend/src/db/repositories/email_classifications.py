"""Repository for the email_classifications table."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import EmailClassification

log = structlog.get_logger()


class EmailClassificationRepository:
    """CRUD operations for the ``email_classifications`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> EmailClassification:
        """Insert a new email classification record."""
        record = EmailClassification(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_recent_corrections(self, limit: int = 20) -> list[EmailClassification]:
        """Fetch recent user-corrected email classifications.

        Returns records where ``user_correction`` is not null, ordered by
        correction time descending.
        """
        result = await self._session.execute(
            select(EmailClassification)
            .where(EmailClassification.user_correction.isnot(None))
            .order_by(EmailClassification.correction_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_sender(
        self,
        from_address: str,
        limit: int = 50,
    ) -> list[EmailClassification]:
        """Fetch classifications for a specific sender address."""
        result = await self._session.execute(
            select(EmailClassification)
            .where(EmailClassification.from_address == from_address)
            .order_by(EmailClassification.received_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def bulk_create(self, records: list[dict[str, Any]]) -> int:
        """Insert multiple classification records. Returns count inserted."""
        count = 0
        for rec in records:
            self._session.add(EmailClassification(**rec))
            count += 1
        await self._session.flush()
        return count
