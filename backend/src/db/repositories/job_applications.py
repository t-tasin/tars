"""Repository for the job_applications table."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import JobApplication

log = structlog.get_logger()


class JobApplicationRepository:
    """CRUD operations for the ``job_applications`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> JobApplication:
        """Insert a new job application."""
        app = JobApplication(**kwargs)
        self._session.add(app)
        await self._session.flush()
        return app

    async def get_by_job(self, job_listing_id: uuid.UUID) -> list[JobApplication]:
        """Fetch all applications for a specific job listing."""
        result = await self._session.execute(
            select(JobApplication)
            .where(JobApplication.job_listing_id == job_listing_id)
            .order_by(JobApplication.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_status(
        self,
        status: str,
        limit: int = 50,
    ) -> list[JobApplication]:
        """Fetch applications filtered by status."""
        result = await self._session.execute(
            select(JobApplication)
            .where(JobApplication.status == status)
            .order_by(JobApplication.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        application_id: uuid.UUID,
        status: str,
    ) -> JobApplication | None:
        """Update an application's status."""
        result = await self._session.execute(
            select(JobApplication).where(JobApplication.id == application_id)
        )
        app = result.scalar_one_or_none()
        if app is None:
            return None
        app.status = status
        await self._session.flush()
        return app
