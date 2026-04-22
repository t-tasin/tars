"""Workout tracking API endpoints."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    CreateSplitRequest,
    LogDetail,
    LogSetRequest,
    SessionDetail,
    SkipSessionRequest,
    SplitDetail,
    StreakResponse,
    UpdateSplitRequest,
    WorkoutHistoryResponse,
)
from src.db.models import AuditLog, WorkoutLog
from src.db.repositories.workout import WorkoutRepository
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/workout", tags=["workout"])


def _get_repo(db: AsyncSession) -> WorkoutRepository:
    return WorkoutRepository(db)


@router.post("/splits", status_code=201)
async def create_split(
    request: CreateSplitRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Create a new workout split with exercises. Deactivates any existing active split."""
    repo = _get_repo(db)
    split = await repo.create_split(
        name=request.name,
        rotation_days=request.rotation_days,
        exercises=[ex.model_dump() for ex in request.exercises],
    )
    # HC-08: audit log
    db.add(
        AuditLog(
            action_type="workout_split_created",
            actor="api",
            target=str(split.id),
            details={"name": split.name, "rotation_days": request.rotation_days},
        )
    )
    log.info("workout_split_created", split_id=str(split.id), name=split.name)
    return {"split_id": str(split.id), "name": split.name, "active": True}


@router.get("/splits/active")
async def get_active_split(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SplitDetail:
    """Get the currently active split with all exercises."""
    repo = _get_repo(db)
    split = await repo.get_active_split()
    if split is None:
        raise HTTPException(status_code=404, detail="No active workout split")
    return SplitDetail.model_validate(split)


@router.put("/splits/{split_id}")
async def update_split(
    split_id: UUID,
    request: UpdateSplitRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update an existing split's name or rotation."""
    repo = _get_repo(db)
    split = await repo.update_split(split_id, name=request.name, rotation_days=request.rotation_days)
    if split is None:
        raise HTTPException(status_code=404, detail="Split not found")
    # HC-08: audit log
    db.add(
        AuditLog(
            action_type="workout_split_updated",
            actor="api",
            target=str(split.id),
            details={"name": request.name, "rotation_days": request.rotation_days},
        )
    )
    return {"split_id": str(split.id), "updated": True}


@router.get("/sessions/today")
async def get_today_session(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionDetail:
    """Get today's workout session with exercises and set targets."""
    repo = _get_repo(db)
    session = await repo.get_today_session()
    if session is None:
        raise HTTPException(status_code=404, detail="No session scheduled for today")
    return SessionDetail.model_validate(session)


@router.post("/sessions/{session_id}/start")
async def start_session(
    session_id: UUID,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Mark a pending session as active."""
    repo = _get_repo(db)
    session = await repo.start_session(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="Session not found or not in pending state")
    db.add(AuditLog(action_type="workout_session_started", actor="api", target=str(session_id), details={}))
    log.info("workout_session_started", session_id=str(session_id))
    return {"session_id": str(session_id), "status": "active"}


@router.post("/sessions/{session_id}/skip")
async def skip_session(
    session_id: UUID,
    request: SkipSessionRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Skip a session with a mandatory reason."""
    repo = _get_repo(db)
    session = await repo.skip_session(session_id, reason=request.reason)
    if session is None:
        raise HTTPException(status_code=400, detail="Session not found or already completed")
    db.add(
        AuditLog(
            action_type="workout_session_skipped",
            actor="api",
            target=str(session_id),
            details={"reason": request.reason},
        )
    )
    log.info("workout_session_skipped", session_id=str(session_id), reason=request.reason)
    return {"session_id": str(session_id), "status": "skipped"}


@router.post("/sessions/{session_id}/complete")
async def complete_session(
    session_id: UUID,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Complete a session and run progressive overload engine."""
    repo = _get_repo(db)
    session = await repo.complete_session(session_id)
    if session is None:
        raise HTTPException(status_code=400, detail="Session not found or not active")

    # Run progressive overload
    progression = await repo.apply_progressive_overload(session_id)
    progressed = [str(eid) for eid, advanced in progression.items() if advanced]

    db.add(
        AuditLog(
            action_type="workout_session_completed",
            actor="api",
            target=str(session_id),
            details={"exercises_progressed": progressed},
        )
    )
    log.info(
        "workout_session_completed",
        session_id=str(session_id),
        exercises_progressed=len(progressed),
    )
    return {
        "session_id": str(session_id),
        "status": "completed",
        "progression": {str(k): v for k, v in progression.items()},
        "exercises_progressed": progressed,
    }


@router.post("/logs")
async def log_set(
    request: LogSetRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Log actual reps and weight for a single set."""
    repo = _get_repo(db)
    entry = await repo.log_set(
        session_id=request.session_id,
        exercise_id=request.exercise_id,
        set_number=request.set_number,
        actual_reps=request.actual_reps,
        actual_weight=request.actual_weight,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found for this set")
    return {"logged": True, "set_number": request.set_number}


@router.get("/history")
async def get_history(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    exercise_id: UUID | None = Query(None),
    limit: int = Query(50, le=200),
) -> WorkoutHistoryResponse:
    """Get historical workout logs, optionally filtered by exercise."""
    repo = _get_repo(db)
    if exercise_id:
        logs = await repo.get_exercise_history(exercise_id, limit=limit)
    else:
        # Return most recent logs across all exercises
        result = await db.execute(
            select(WorkoutLog)
            .where(WorkoutLog.actual_reps.isnot(None))
            .order_by(WorkoutLog.created_at.desc())
            .limit(limit)
        )
        logs = list(result.scalars().all())

    return WorkoutHistoryResponse(
        logs=[LogDetail.model_validate(log) for log in logs],
        total=len(logs),
    )


@router.get("/streak")
async def get_streak(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreakResponse:
    """Get current workout streak and recent skip history."""
    repo = _get_repo(db)
    split = await repo.get_active_split()
    if split is None:
        return StreakResponse(streak=0, recent_skips=[])

    streak = await repo.calculate_streak(split.id)
    skips = await repo.get_recent_skips(split.id)

    return StreakResponse(
        streak=streak,
        recent_skips=[
            {
                "date": s.created_at.isoformat(),
                "day_name": s.day_name,
                "reason": s.skip_reason or "",
            }
            for s in skips
        ],
    )
