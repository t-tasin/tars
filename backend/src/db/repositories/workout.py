"""Repository for workout tracking tables."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from shared.constants import WorkoutSessionStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import WorkoutExercise, WorkoutLog, WorkoutSession, WorkoutSplit

log = structlog.get_logger()


class WorkoutRepository:
    """CRUD operations for workout tracking tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Splits
    # ------------------------------------------------------------------

    async def create_split(
        self,
        name: str,
        rotation_days: list[str],
        exercises: list[dict[str, Any]],
    ) -> WorkoutSplit:
        """Create a new split, deactivating any currently active one first."""
        # Deactivate current active split
        await self._session.execute(
            update(WorkoutSplit)
            .where(WorkoutSplit.active == True)  # noqa: E712
            .values(active=False)
        )

        split = WorkoutSplit(
            name=name,
            rotation_days=rotation_days,
            active=True,
        )
        self._session.add(split)
        await self._session.flush()

        # Create exercises
        for idx, ex in enumerate(exercises):
            self._session.add(
                WorkoutExercise(
                    split_id=split.id,
                    day_name=ex["day_name"],
                    exercise_name=ex["exercise_name"],
                    target_sets=ex["target_sets"],
                    target_reps=ex["target_reps"],
                    current_weight=Decimal(str(ex["current_weight"])),
                    weight_unit=ex.get("weight_unit", "lbs"),
                    weight_increment=Decimal(str(ex.get("weight_increment", 2.5))),
                    order_index=idx,
                )
            )

        await self._session.flush()
        return split

    async def get_active_split(self) -> WorkoutSplit | None:
        """Get the currently active split with eager-loaded exercises."""
        result = await self._session.execute(
            select(WorkoutSplit)
            .where(WorkoutSplit.active == True)  # noqa: E712
            .options(selectinload(WorkoutSplit.exercises))
        )
        return result.scalar_one_or_none()

    async def update_split(
        self,
        split_id: uuid.UUID,
        name: str | None = None,
        rotation_days: list[str] | None = None,
    ) -> WorkoutSplit | None:
        """Update a split's name and/or rotation."""
        result = await self._session.execute(select(WorkoutSplit).where(WorkoutSplit.id == split_id))
        split = result.scalar_one_or_none()
        if split is None:
            return None

        if name is not None:
            split.name = name
        if rotation_days is not None:
            split.rotation_days = rotation_days

        await self._session.flush()
        return split

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(
        self,
        split_id: uuid.UUID,
        day_name: str,
        rotation_index: int,
        scheduled_at: datetime | None = None,
    ) -> WorkoutSession:
        """Create a workout session and pre-populate workout_logs."""
        session = WorkoutSession(
            split_id=split_id,
            day_name=day_name,
            rotation_index=rotation_index,
            scheduled_at=scheduled_at,
        )
        self._session.add(session)
        await self._session.flush()

        # Get exercises for this day and create log entries
        result = await self._session.execute(
            select(WorkoutExercise)
            .where(
                WorkoutExercise.split_id == split_id,
                WorkoutExercise.day_name == day_name,
            )
            .order_by(WorkoutExercise.order_index)
        )
        exercises = result.scalars().all()

        for exercise in exercises:
            for set_num in range(1, exercise.target_sets + 1):
                self._session.add(
                    WorkoutLog(
                        session_id=session.id,
                        exercise_id=exercise.id,
                        set_number=set_num,
                        target_reps=exercise.target_reps,
                        target_weight=exercise.current_weight,
                    )
                )

        await self._session.flush()
        return session

    async def get_today_session(self) -> WorkoutSession | None:
        """Get today's session with eager-loaded logs."""
        today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC)
        today_end = datetime.combine(date.today(), datetime.max.time(), tzinfo=UTC)

        result = await self._session.execute(
            select(WorkoutSession)
            .where(
                WorkoutSession.created_at >= today_start,
                WorkoutSession.created_at <= today_end,
            )
            .options(
                selectinload(WorkoutSession.logs),
                selectinload(WorkoutSession.split).selectinload(WorkoutSplit.exercises),
            )
            .order_by(WorkoutSession.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def start_session(self, session_id: uuid.UUID) -> WorkoutSession | None:
        """Mark a session as active."""
        result = await self._session.execute(select(WorkoutSession).where(WorkoutSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None or session.status != WorkoutSessionStatus.PENDING:
            return None

        session.status = WorkoutSessionStatus.ACTIVE
        session.started_at = datetime.now(UTC)
        await self._session.flush()
        return session

    async def skip_session(self, session_id: uuid.UUID, reason: str) -> WorkoutSession | None:
        """Mark a session as skipped with a mandatory reason."""
        result = await self._session.execute(select(WorkoutSession).where(WorkoutSession.id == session_id))
        session = result.scalar_one_or_none()
        if session is None or session.status not in (
            WorkoutSessionStatus.PENDING,
            WorkoutSessionStatus.ACTIVE,
        ):
            return None

        session.status = WorkoutSessionStatus.SKIPPED
        session.skip_reason = reason
        session.completed_at = datetime.now(UTC)
        await self._session.flush()
        return session

    async def complete_session(self, session_id: uuid.UUID) -> WorkoutSession | None:
        """Mark a session as completed."""
        result = await self._session.execute(
            select(WorkoutSession).where(WorkoutSession.id == session_id).options(selectinload(WorkoutSession.logs))
        )
        session = result.scalar_one_or_none()
        if session is None or session.status != WorkoutSessionStatus.ACTIVE:
            return None

        session.status = WorkoutSessionStatus.COMPLETED
        session.completed_at = datetime.now(UTC)
        await self._session.flush()
        return session

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    async def log_set(
        self,
        session_id: uuid.UUID,
        exercise_id: uuid.UUID,
        set_number: int,
        actual_reps: int,
        actual_weight: float,
    ) -> WorkoutLog | None:
        """Log actual performance for a set."""
        result = await self._session.execute(
            select(WorkoutLog).where(
                WorkoutLog.session_id == session_id,
                WorkoutLog.exercise_id == exercise_id,
                WorkoutLog.set_number == set_number,
            )
        )
        log_entry = result.scalar_one_or_none()
        if log_entry is None:
            return None

        log_entry.actual_reps = actual_reps
        log_entry.actual_weight = Decimal(str(actual_weight))
        log_entry.logged_at = datetime.now(UTC)
        await self._session.flush()
        return log_entry

    async def get_session_logs(self, session_id: uuid.UUID) -> list[WorkoutLog]:
        """Get all logs for a session."""
        result = await self._session.execute(
            select(WorkoutLog)
            .where(WorkoutLog.session_id == session_id)
            .order_by(WorkoutLog.exercise_id, WorkoutLog.set_number)
        )
        return list(result.scalars().all())

    async def get_exercise_history(
        self,
        exercise_id: uuid.UUID,
        limit: int = 50,
    ) -> list[WorkoutLog]:
        """Get historical logs for an exercise (for progressive overload charts)."""
        result = await self._session.execute(
            select(WorkoutLog)
            .where(
                WorkoutLog.exercise_id == exercise_id,
                WorkoutLog.actual_reps.isnot(None),
            )
            .order_by(WorkoutLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Progressive overload
    # ------------------------------------------------------------------

    async def apply_progressive_overload(self, session_id: uuid.UUID) -> dict[uuid.UUID, bool]:
        """Check each exercise in a session and advance weight if all sets hit target.

        Returns a dict of {exercise_id: progressed (True/False)}.
        """
        logs = await self.get_session_logs(session_id)

        # Group logs by exercise
        by_exercise: dict[uuid.UUID, list[WorkoutLog]] = {}
        for log_entry in logs:
            by_exercise.setdefault(log_entry.exercise_id, []).append(log_entry)

        results: dict[uuid.UUID, bool] = {}
        for exercise_id, exercise_logs in by_exercise.items():
            all_hit = all(
                entry.actual_reps is not None
                and entry.actual_weight is not None
                and entry.actual_reps >= entry.target_reps
                and entry.actual_weight >= entry.target_weight
                for entry in exercise_logs
            )

            if all_hit:
                # Progress: increase current_weight by weight_increment
                ex_result = await self._session.execute(
                    select(WorkoutExercise).where(WorkoutExercise.id == exercise_id)
                )
                exercise = ex_result.scalar_one_or_none()
                if exercise:
                    exercise.current_weight += exercise.weight_increment
                    await self._session.flush()
                    results[exercise_id] = True
            else:
                results[exercise_id] = False

        return results

    # ------------------------------------------------------------------
    # Streak
    # ------------------------------------------------------------------

    async def calculate_streak(self, split_id: uuid.UUID) -> int:
        """Calculate consecutive workout adherence streak.

        Rest days in the rotation don't break the streak.
        Only 'skipped' workout sessions break it.
        """
        result = await self._session.execute(
            select(WorkoutSession).where(WorkoutSession.split_id == split_id).order_by(WorkoutSession.created_at.desc())
        )
        sessions = result.scalars().all()

        streak = 0
        for session in sessions:
            if session.status == WorkoutSessionStatus.COMPLETED:
                streak += 1
            elif session.status == WorkoutSessionStatus.SKIPPED:
                break
            elif session.status in (WorkoutSessionStatus.PENDING, WorkoutSessionStatus.ACTIVE):
                # Current/in-progress session — don't count but don't break
                continue
            else:
                break

        return streak

    async def get_recent_skips(self, split_id: uuid.UUID, days: int = 30) -> list[WorkoutSession]:
        """Get recently skipped sessions for accountability messaging."""
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)

        result = await self._session.execute(
            select(WorkoutSession)
            .where(
                WorkoutSession.split_id == split_id,
                WorkoutSession.status == WorkoutSessionStatus.SKIPPED,
                WorkoutSession.created_at >= cutoff,
            )
            .order_by(WorkoutSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending_sessions_past_schedule(self) -> list[WorkoutSession]:
        """Get pending sessions whose scheduled_at has passed (for reminder polling)."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(WorkoutSession).where(
                WorkoutSession.status == WorkoutSessionStatus.PENDING,
                WorkoutSession.scheduled_at.isnot(None),
                WorkoutSession.scheduled_at <= now,
            )
        )
        return list(result.scalars().all())
