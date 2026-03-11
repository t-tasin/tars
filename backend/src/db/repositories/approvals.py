"""Repository for the approvals table."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Approval
from shared.constants import ApprovalStatus

log = structlog.get_logger()


class ApprovalRepository:
    """CRUD operations for the ``approvals`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> Approval:
        """Insert a new approval record."""
        approval = Approval(**kwargs)
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get_by_id(self, approval_id: uuid.UUID) -> Approval | None:
        """Fetch an approval by primary key."""
        result = await self._session.execute(
            select(Approval).where(Approval.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def get_pending(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Approval], int]:
        """Return pending approvals and total pending count."""
        base = select(Approval).where(Approval.status == ApprovalStatus.PENDING)

        count_result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar_one()

        rows_result = await self._session.execute(
            base.order_by(Approval.created_at.asc()).limit(limit).offset(offset)
        )
        return list(rows_result.scalars().all()), total

    async def update_status(
        self,
        approval_id: uuid.UUID,
        status: ApprovalStatus,
        **kwargs: Any,
    ) -> Approval | None:
        """Update an approval's status and optional fields."""
        approval = await self.get_by_id(approval_id)
        if approval is None:
            return None
        approval.status = status
        for key, value in kwargs.items():
            if hasattr(approval, key):
                setattr(approval, key, value)
        await self._session.flush()
        return approval

    async def get_expired(self) -> list[Approval]:
        """Return pending approvals that have passed their expiry time."""
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(Approval).where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at <= now,
            )
        )
        return list(result.scalars().all())

    async def get_by_filters(
        self,
        status: ApprovalStatus | None = None,
        action_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Approval]:
        """Fetch approvals with optional status/action_type filters."""
        stmt = select(Approval)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        if action_type is not None:
            stmt = stmt.where(Approval.action_type == action_type)
        stmt = stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
