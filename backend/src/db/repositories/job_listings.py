"""Repository for the job_listings table."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import JobListing
from shared.constants import JobStatus

log = structlog.get_logger()


class JobListingRepository:
    """CRUD operations for the ``job_listings`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> JobListing:
        """Insert a new job listing."""
        listing = JobListing(**kwargs)
        self._session.add(listing)
        await self._session.flush()
        return listing

    async def get_by_id(self, job_id: uuid.UUID) -> JobListing | None:
        """Fetch a job listing by primary key."""
        result = await self._session.execute(
            select(JobListing).where(JobListing.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_recent(self, limit: int = 25) -> list[JobListing]:
        """Fetch most recently scraped job listings."""
        result = await self._session.execute(
            select(JobListing)
            .order_by(JobListing.scraped_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(
        self,
        status: JobStatus,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobListing]:
        """Fetch job listings filtered by status."""
        result = await self._session.execute(
            select(JobListing)
            .where(JobListing.status == status)
            .order_by(JobListing.scraped_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        **kwargs: Any,
    ) -> JobListing | None:
        """Update a job listing's status and optional fields."""
        listing = await self.get_by_id(job_id)
        if listing is None:
            return None
        listing.status = status
        for key, value in kwargs.items():
            if hasattr(listing, key):
                setattr(listing, key, value)
        await self._session.flush()
        return listing

    async def bulk_create(self, listings: list[dict[str, Any]]) -> int:
        """Insert multiple job listings. Returns count inserted."""
        count = 0
        for data in listings:
            self._session.add(JobListing(**data))
            count += 1
        await self._session.flush()
        return count

    async def get_saved(self, limit: int = 50) -> list[JobListing]:
        """Fetch job listings with status 'saved'."""
        return await self.get_by_status(JobStatus.SAVED, limit=limit)

    async def get_saved_count(self) -> int:
        """Return count of saved job listings."""
        result = await self._session.execute(
            select(func.count())
            .select_from(JobListing)
            .where(JobListing.status == JobStatus.SAVED)
        )
        return result.scalar_one()
