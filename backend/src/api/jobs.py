"""Job search API endpoints for T.A.R.S."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.constants import JobStatus
from src.api.schemas import (
    ErrorDetail,
    ErrorResponse,
    JobActionRequest,
    JobDetail,
    JobsListResponse,
)
from src.db.models import JobListing
from src.db.repositories.job_listings import JobListingRepository
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["jobs"])

# Allowed status transitions per action
_ACTION_STATUS_MAP: dict[str, JobStatus] = {
    "save": JobStatus.SAVED,
    "skip": JobStatus.SKIPPED,
    "apply": JobStatus.APPLYING,
    "archive": JobStatus.EXPIRED,
}


@router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: JobStatus | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobsListResponse:
    """List job listings with optional filtering by status and match score."""
    query = select(JobListing)

    if status is not None:
        query = query.where(JobListing.status == status)
    if min_score is not None:
        query = query.where(JobListing.match_score >= min_score)

    # Total count for the filtered query
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # New today count
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    new_today_result = await db.execute(
        select(func.count()).where(
            JobListing.scraped_at >= today_start,
        )
    )
    new_today = new_today_result.scalar_one()

    # Fetch paginated results, newest first with highest score priority
    query = query.order_by(
        JobListing.scraped_at.desc(),
        JobListing.match_score.desc().nulls_last(),
    )
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all()

    jobs = [
        JobDetail(
            id=row.id,
            title=row.title,
            company=row.company,
            location=row.location,
            salary_range=row.salary_range,
            match_score=row.match_score,
            match_reasons=row.match_reasons if isinstance(row.match_reasons, list) else [],
            concerns=row.concerns if isinstance(row.concerns, list) else [],
            url=row.url,
            status=row.status,
            source=row.source,
            scraped_at=row.scraped_at,
        )
        for row in rows
    ]

    log.info(
        "jobs_listed",
        total=total,
        returned=len(jobs),
        filters={"status": status, "min_score": min_score},
    )

    return JobsListResponse(jobs=jobs, total=total, new_today=new_today)


@router.post("/jobs/{job_id}/action")
async def job_action(
    job_id: UUID,
    body: JobActionRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Perform an action on a job listing (save, skip, apply, archive)."""
    repo = JobListingRepository(db)
    job = await repo.get_by_id(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=ErrorDetail(code="not_found", message="Job listing not found"),
            ).model_dump(),
        )

    new_status = _ACTION_STATUS_MAP[body.action]

    # For "apply" action, route through the approval system (HC-01)
    if body.action == "apply":
        from src.orchestrator.approval_manager import ApprovalManager

        approval_manager = ApprovalManager()
        approval = await approval_manager.create(
            db,
            action_type="apply_job",
            title=f"Apply to {job.title} at {job.company}",
            preview_payload={
                "job_id": str(job.id),
                "title": job.title,
                "company": job.company,
                "url": job.url,
                "match_score": job.match_score,
            },
        )

        log.info(
            "job_apply_pending_approval",
            job_id=str(job_id),
            approval_id=str(approval["id"]),
        )

        return {
            "job_id": str(job_id),
            "action": body.action,
            "status": "pending_approval",
            "approval_id": str(approval["id"]),
        }

    # Direct status update for non-apply actions
    kwargs: dict[str, Any] = {}
    if body.action == "save":
        kwargs["follow_up_at"] = datetime.now(timezone.utc) + timedelta(days=7)

    await repo.update_status(job_id, new_status, **kwargs)

    log.info(
        "job_action_performed",
        job_id=str(job_id),
        action=body.action,
        new_status=new_status,
    )

    return {
        "job_id": str(job_id),
        "action": body.action,
        "status": str(new_status),
    }
