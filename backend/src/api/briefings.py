"""Briefing and schedule API endpoints for T.A.R.S."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import BriefingResponse, ErrorDetail, ErrorResponse, ScheduleResponse
from src.config import get_settings
from src.db.repositories.briefings import BriefingRepository
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["briefings"])


@router.get("/briefing", response_model=BriefingResponse)
async def get_briefing(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Literal["morning", "end_of_day"] = "morning",
    date: date | None = None,
) -> BriefingResponse:
    """Fetch the briefing for a given date and type.

    Returns 404 if no briefing exists for the requested date/type.
    """
    target_date = date or _today()

    repo = BriefingRepository(db)
    briefing = await repo.get_by_date(target_date, briefing_type=type)

    if briefing is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="not_found",
                    message=f"No {type} briefing found for {target_date.isoformat()}",
                ),
            ).model_dump(),
        )

    log.info("briefing_fetched", briefing_id=str(briefing.id), type=type, date=target_date.isoformat())

    return BriefingResponse(
        briefing_id=briefing.id,
        type=briefing.briefing_type,
        date=briefing.briefing_date.isoformat(),
        payload=briefing.payload,
        narrative=briefing.narrative,
        created_at=briefing.created_at,
        delivered=briefing.delivered,
    )


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    date: date | None = None,
    days: Annotated[int, Query(ge=1, le=7)] = 1,
) -> ScheduleResponse:
    """Fetch calendar events for a date range via CalDAV."""
    from src.integrations.caldav_client import CalDAVClient

    target_date = date or _today()
    end_date = target_date + timedelta(days=days)

    settings = get_settings()
    caldav = CalDAVClient(
        username=settings.icloud_caldav_user,
        password=settings.icloud_caldav_password,
    )

    try:
        events = await caldav.get_events(target_date, end_date)
    except Exception:
        log.error("schedule_fetch_failed", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="integration_error",
                    message="Failed to fetch calendar events",
                ),
            ).model_dump(),
        )

    log.info("schedule_fetched", date=target_date.isoformat(), days=days, event_count=len(events))

    # Compute leave-home-by from the briefing agent helpers
    from src.agents.briefing import _build_commute_note, _compute_leave_home_by

    return ScheduleResponse(
        date=target_date.isoformat(),
        events=events,
        leave_home_by=_compute_leave_home_by(events),
        commute_note=_build_commute_note(events),
    )


def _today() -> date:
    """Return today's date. Extracted for testability."""
    return date.today()
