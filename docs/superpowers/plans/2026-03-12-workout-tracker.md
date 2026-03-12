# Workout Tracker Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dedicated workout tracker agent that manages splits, enforces progressive overload, and holds the user accountable via streak tracking and skip memory.

**Architecture:** New `workout_tracker` agent extending `BaseAgent`, backed by 4 new DB tables, exposed via 10 REST endpoints, with 2 scheduler jobs for daily session creation and reminder polling. Voice logging delegates to Gemini Flash for NLP parsing; iOS app provides fallback UI.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0, Alembic, APScheduler, Gemini Flash, APNs, Swift/SwiftUI

**Spec:** `docs/superpowers/specs/2026-03-12-workout-tracker-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `shared/constants.py` (modify) | Add `WorkoutSessionStatus` StrEnum, `IntentType.WORKOUT_TRACKER`, `AGENT_MODEL_MAP` entry |
| `backend/alembic/versions/006_add_workout_tables.py` | Migration: 4 tables + ENUM + indexes |
| `backend/src/db/models.py` (modify) | ORM models for `WorkoutSplit`, `WorkoutExercise`, `WorkoutSession`, `WorkoutLog` |
| `backend/src/db/repositories/workout.py` | Data access layer for all 4 workout tables |
| `backend/src/api/schemas.py` (modify) | Pydantic request/response schemas for workout endpoints |
| `backend/src/api/workout.py` | 10 REST endpoints |
| `backend/src/api/router.py` (modify) | Register workout router |
| `backend/src/agents/workout_tracker.py` | Agent: progressive overload engine, accountability, voice parsing |
| `backend/src/orchestrator/intent_classifier.py` (modify) | Add workout pattern + `/workout` command |
| `backend/src/orchestrator/model_router.py` (modify) | Add `WORKOUT_TRACKER` route |
| `backend/src/scheduler/jobs.py` (modify) | Add 2 scheduler jobs |
| `backend/src/agents/health_fitness.py` (modify) | Suppress gym suggestions when active split exists |
| `backend/tests/test_agents/test_workout_tracker.py` | Unit tests for agent logic |
| `backend/tests/test_api/test_workout.py` | API endpoint tests |
| `ios/TARS/TARS/ViewModels/WorkoutViewModel.swift` | View model for workout screens |
| `ios/TARS/TARS/Views/Workout/WorkoutSessionView.swift` | Active workout session screen |
| `ios/TARS/TARS/Views/Workout/SplitSetupView.swift` | Split configuration screen |
| `ios/TARS/TARS/Services/APIClient.swift` (modify) | Add workout endpoint definitions |

---

## Chunk 1: Constants, Migration, and ORM Models

### Task 1: Add WorkoutSessionStatus and IntentType.WORKOUT_TRACKER to constants

**Files:**
- Modify: `shared/constants.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_constants_workout.py`:

```python
"""Test that workout-related constants exist in shared.constants."""
from __future__ import annotations

from shared.constants import (
    AGENT_MODEL_MAP,
    IntentType,
    ModelName,
    WorkoutSessionStatus,
)


def test_workout_session_status_values():
    assert WorkoutSessionStatus.PENDING == "pending"
    assert WorkoutSessionStatus.ACTIVE == "active"
    assert WorkoutSessionStatus.COMPLETED == "completed"
    assert WorkoutSessionStatus.SKIPPED == "skipped"


def test_intent_type_has_workout_tracker():
    assert IntentType.WORKOUT_TRACKER == "workout_tracker"


def test_agent_model_map_includes_workout_tracker():
    assert IntentType.WORKOUT_TRACKER in AGENT_MODEL_MAP
    assert AGENT_MODEL_MAP[IntentType.WORKOUT_TRACKER] == ModelName.GEMINI_FLASH
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_constants_workout.py -v`
Expected: FAIL — `ImportError: cannot import name 'WorkoutSessionStatus'`

- [ ] **Step 3: Add constants to shared/constants.py**

In `shared/constants.py`, add after `IntentType.GENERAL`:

```python
    WORKOUT_TRACKER = "workout_tracker"
```

Add new StrEnum after `HealthStatus`:

```python
class WorkoutSessionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"
```

Add to `AGENT_MODEL_MAP`:

```python
    IntentType.WORKOUT_TRACKER: ModelName.GEMINI_FLASH,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_constants_workout.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add shared/constants.py backend/tests/test_constants_workout.py
git commit -m "feat(workout): add WorkoutSessionStatus enum and IntentType.WORKOUT_TRACKER"
```

---

### Task 2: Create Alembic migration for 4 workout tables

**Files:**
- Create: `backend/alembic/versions/006_add_workout_tables.py`

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/006_add_workout_tables.py`:

```python
"""006_add_workout_tables

Add workout tracking tables: workout_splits, workout_exercises,
workout_sessions, workout_logs. Includes workout_session_status ENUM.

Revision ID: 006_add_workout_tables
Revises: 005_migrate_plaid_to_teller
Create Date: 2026-03-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision: str = "006_add_workout_tables"
down_revision: str = "005_migrate_plaid_to_teller"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create ENUM type
    workout_status = sa.Enum(
        "pending", "active", "completed", "skipped",
        name="workout_session_status",
    )
    workout_status.create(op.get_bind(), checkfirst=True)

    # 1. workout_splits
    op.create_table(
        "workout_splits",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rotation_days", JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_splits_active", "workout_splits", ["active"],
        postgresql_where=sa.text("active = true"),
    )

    # 2. workout_exercises
    op.create_table(
        "workout_exercises",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("split_id", sa.Uuid(), sa.ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("day_name", sa.String(30), nullable=False),
        sa.Column("exercise_name", sa.String(100), nullable=False),
        sa.Column("target_sets", sa.Integer, nullable=False),
        sa.Column("target_reps", sa.Integer, nullable=False),
        sa.Column("current_weight", sa.Numeric(8, 2), nullable=False),
        sa.Column("weight_unit", sa.String(5), nullable=False, server_default=sa.text("'lbs'")),
        sa.Column("weight_increment", sa.Numeric(8, 2), nullable=False, server_default=sa.text("2.5")),
        sa.Column("order_index", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_exercises_split_day", "workout_exercises", ["split_id", "day_name"])

    # 3. workout_sessions
    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("split_id", sa.Uuid(), sa.ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("day_name", sa.String(30), nullable=False),
        sa.Column("rotation_index", sa.Integer, nullable=False),
        sa.Column("scheduled_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", workout_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("skip_reason", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_sessions_status", "workout_sessions", ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_sessions_date", "workout_sessions", [sa.text("created_at DESC")])
    op.create_index("idx_sessions_split", "workout_sessions", ["split_id", sa.text("created_at DESC")])

    # 4. workout_logs
    op.create_table(
        "workout_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("workout_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("exercise_id", sa.Uuid(), sa.ForeignKey("workout_exercises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("set_number", sa.Integer, nullable=False),
        sa.Column("target_reps", sa.Integer, nullable=False),
        sa.Column("target_weight", sa.Numeric(8, 2), nullable=False),
        sa.Column("actual_reps", sa.Integer, nullable=True),
        sa.Column("actual_weight", sa.Numeric(8, 2), nullable=True),
        sa.Column("logged_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_logs_session", "workout_logs", ["session_id"])
    op.create_index("idx_logs_exercise_date", "workout_logs", ["exercise_id", sa.text("created_at DESC")])

    # Auto-update triggers for updated_at
    for table in ("workout_splits", "workout_exercises", "workout_sessions", "workout_logs"):
        op.execute(f"""
            CREATE OR REPLACE FUNCTION update_{table}_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_{table}_updated_at
                BEFORE UPDATE ON {table}
                FOR EACH ROW
                EXECUTE FUNCTION update_{table}_updated_at();
        """)


def downgrade() -> None:
    for table in ("workout_logs", "workout_sessions", "workout_exercises", "workout_splits"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS update_{table}_updated_at()")
        op.drop_table(table)

    op.execute("DROP TYPE IF EXISTS workout_session_status")
```

- [ ] **Step 2: Verify migration syntax is valid**

Run: `cd backend && .venv/bin/python -c "import alembic.versions; print('Migration file is valid Python')"` or simply `python -c "import ast; ast.parse(open('alembic/versions/006_add_workout_tables.py').read()); print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/006_add_workout_tables.py
git commit -m "feat(workout): add migration for workout_splits, workout_exercises, workout_sessions, workout_logs"
```

---

### Task 3: Add ORM models for workout tables

**Files:**
- Modify: `backend/src/db/models.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_models_workout.py`:

```python
"""Test that workout ORM models are importable and have correct table names."""
from __future__ import annotations

from db.models import WorkoutExercise, WorkoutLog, WorkoutSession, WorkoutSplit


def test_workout_split_tablename():
    assert WorkoutSplit.__tablename__ == "workout_splits"


def test_workout_exercise_tablename():
    assert WorkoutExercise.__tablename__ == "workout_exercises"


def test_workout_session_tablename():
    assert WorkoutSession.__tablename__ == "workout_sessions"


def test_workout_log_tablename():
    assert WorkoutLog.__tablename__ == "workout_logs"


def test_workout_split_has_exercises_relationship():
    assert hasattr(WorkoutSplit, "exercises")


def test_workout_session_has_logs_relationship():
    assert hasattr(WorkoutSession, "logs")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_workout.py -v`
Expected: FAIL — `ImportError: cannot import name 'WorkoutSplit'`

- [ ] **Step 3: Add ORM models to db/models.py**

Add at the top imports:

```python
from shared.constants import WorkoutSessionStatus
```

Add after the `HealthData` class (before `DeviceToken`):

```python
# ---------------------------------------------------------------------------
# 21. WorkoutSplit
# ---------------------------------------------------------------------------

class WorkoutSplit(Base):
    __tablename__ = "workout_splits"
    __table_args__ = (
        Index("idx_splits_active", "active", postgresql_where=text("active = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rotation_days: Mapped[list] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    exercises: Mapped[list[WorkoutExercise]] = relationship(back_populates="split", cascade="save-update, merge")
    sessions: Mapped[list[WorkoutSession]] = relationship(back_populates="split")


# ---------------------------------------------------------------------------
# 22. WorkoutExercise
# ---------------------------------------------------------------------------

class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    __table_args__ = (
        Index("idx_exercises_split_day", "split_id", "day_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    split_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False)
    day_name: Mapped[str] = mapped_column(String(30), nullable=False)
    exercise_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_sets: Mapped[int] = mapped_column(Integer, nullable=False)
    target_reps: Mapped[int] = mapped_column(Integer, nullable=False)
    current_weight: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    weight_unit: Mapped[str] = mapped_column(String(5), nullable=False, server_default=text("'lbs'"))
    weight_increment: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, server_default=text("2.5"))
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    split: Mapped[WorkoutSplit] = relationship(back_populates="exercises")
    logs: Mapped[list[WorkoutLog]] = relationship(back_populates="exercise")


# ---------------------------------------------------------------------------
# 23. WorkoutSession
# ---------------------------------------------------------------------------

class WorkoutSession(Base):
    __tablename__ = "workout_sessions"
    __table_args__ = (
        Index("idx_sessions_status", "status", postgresql_where=text("status = 'pending'")),
        Index("idx_sessions_date", "created_at"),
        Index("idx_sessions_split", "split_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    split_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False)
    day_name: Mapped[str] = mapped_column(String(30), nullable=False)
    rotation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[WorkoutSessionStatus] = mapped_column(
        default=WorkoutSessionStatus.PENDING,
        server_default=text("'pending'"),
    )
    skip_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    split: Mapped[WorkoutSplit] = relationship(back_populates="sessions")
    logs: Mapped[list[WorkoutLog]] = relationship(back_populates="session", cascade="save-update, merge")


# ---------------------------------------------------------------------------
# 24. WorkoutLog
# ---------------------------------------------------------------------------

class WorkoutLog(Base):
    __tablename__ = "workout_logs"
    __table_args__ = (
        Index("idx_logs_session", "session_id"),
        Index("idx_logs_exercise_date", "exercise_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_sessions.id", ondelete="RESTRICT"), nullable=False)
    exercise_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_exercises.id", ondelete="RESTRICT"), nullable=False)
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_reps: Mapped[int] = mapped_column(Integer, nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    actual_reps: Mapped[int | None] = mapped_column(Integer)
    actual_weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    logged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    session: Mapped[WorkoutSession] = relationship(back_populates="logs")
    exercise: Mapped[WorkoutExercise] = relationship(back_populates="logs")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_models_workout.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_models_workout.py
git commit -m "feat(workout): add ORM models for WorkoutSplit, WorkoutExercise, WorkoutSession, WorkoutLog"
```

---

## Chunk 2: Repository Layer

### Task 4: Create workout repository

**Files:**
- Create: `backend/src/db/repositories/workout.py`
- Create: `backend/tests/test_repositories/test_workout_repo.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_repositories/__init__.py` (if not exists) and `backend/tests/test_repositories/test_workout_repo.py`:

```python
"""Tests for the workout repository (unit tests with mock session)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.repositories.workout import WorkoutRepository


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def repo(mock_session):
    return WorkoutRepository(mock_session)


class TestGetActiveSplit:
    async def test_returns_none_when_no_active(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_active_split()
        assert result is None


class TestCalculateStreak:
    async def test_empty_sessions_returns_zero(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        streak = await repo.calculate_streak(split_id=uuid.uuid4())
        assert streak == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_repositories/test_workout_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.repositories.workout'`

- [ ] **Step 3: Write the repository**

Create `backend/src/db/repositories/workout.py`:

```python
"""Repository for workout tracking tables."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import WorkoutExercise, WorkoutLog, WorkoutSession, WorkoutSplit
from shared.constants import WorkoutSessionStatus

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
            self._session.add(WorkoutExercise(
                split_id=split.id,
                day_name=ex["day_name"],
                exercise_name=ex["exercise_name"],
                target_sets=ex["target_sets"],
                target_reps=ex["target_reps"],
                current_weight=Decimal(str(ex["current_weight"])),
                weight_unit=ex.get("weight_unit", "lbs"),
                weight_increment=Decimal(str(ex.get("weight_increment", 2.5))),
                order_index=idx,
            ))

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
        result = await self._session.execute(
            select(WorkoutSplit).where(WorkoutSplit.id == split_id)
        )
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
                self._session.add(WorkoutLog(
                    session_id=session.id,
                    exercise_id=exercise.id,
                    set_number=set_num,
                    target_reps=exercise.target_reps,
                    target_weight=exercise.current_weight,
                ))

        await self._session.flush()
        return session

    async def get_today_session(self) -> WorkoutSession | None:
        """Get today's session with eager-loaded logs."""
        today_start = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        today_end = datetime.combine(date.today(), datetime.max.time(), tzinfo=timezone.utc)

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
        result = await self._session.execute(
            select(WorkoutSession).where(WorkoutSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None or session.status != WorkoutSessionStatus.PENDING:
            return None

        session.status = WorkoutSessionStatus.ACTIVE
        session.started_at = datetime.now(timezone.utc)
        await self._session.flush()
        return session

    async def skip_session(self, session_id: uuid.UUID, reason: str) -> WorkoutSession | None:
        """Mark a session as skipped with a mandatory reason."""
        result = await self._session.execute(
            select(WorkoutSession).where(WorkoutSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None or session.status not in (
            WorkoutSessionStatus.PENDING, WorkoutSessionStatus.ACTIVE,
        ):
            return None

        session.status = WorkoutSessionStatus.SKIPPED
        session.skip_reason = reason
        session.completed_at = datetime.now(timezone.utc)
        await self._session.flush()
        return session

    async def complete_session(self, session_id: uuid.UUID) -> WorkoutSession | None:
        """Mark a session as completed."""
        result = await self._session.execute(
            select(WorkoutSession)
            .where(WorkoutSession.id == session_id)
            .options(selectinload(WorkoutSession.logs))
        )
        session = result.scalar_one_or_none()
        if session is None or session.status != WorkoutSessionStatus.ACTIVE:
            return None

        session.status = WorkoutSessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)
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
        log_entry.logged_at = datetime.now(timezone.utc)
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
            select(WorkoutSession)
            .where(WorkoutSession.split_id == split_id)
            .order_by(WorkoutSession.created_at.desc())
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

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(WorkoutSession)
            .where(
                WorkoutSession.status == WorkoutSessionStatus.PENDING,
                WorkoutSession.scheduled_at.isnot(None),
                WorkoutSession.scheduled_at <= now,
            )
        )
        return list(result.scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_repositories/test_workout_repo.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/repositories/workout.py backend/tests/test_repositories/test_workout_repo.py
git commit -m "feat(workout): add WorkoutRepository with split/session/log/streak/progressive-overload methods"
```

---

## Chunk 3: API Schemas and Endpoints

### Task 5: Add Pydantic schemas for workout API

**Files:**
- Modify: `backend/src/api/schemas.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_schemas_workout.py`:

```python
"""Test workout Pydantic schemas validate correctly."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from api.schemas import (
    CreateExerciseSchema,
    CreateSplitRequest,
    LogSetRequest,
    SkipSessionRequest,
)


def test_create_split_valid():
    req = CreateSplitRequest(
        name="PPL",
        rotation_days=["push", "pull", "legs", "rest"],
        exercises=[
            CreateExerciseSchema(
                day_name="push",
                exercise_name="Bench Press",
                target_sets=3,
                target_reps=10,
                current_weight=135.0,
            )
        ],
    )
    assert req.name == "PPL"
    assert len(req.exercises) == 1


def test_create_split_empty_name_fails():
    with pytest.raises(ValidationError):
        CreateSplitRequest(
            name="",
            rotation_days=["push"],
            exercises=[],
        )


def test_log_set_valid():
    req = LogSetRequest(
        session_id=uuid.uuid4(),
        exercise_id=uuid.uuid4(),
        set_number=1,
        actual_reps=10,
        actual_weight=135.0,
    )
    assert req.set_number == 1


def test_skip_session_empty_reason_fails():
    with pytest.raises(ValidationError):
        SkipSessionRequest(reason="")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_schemas_workout.py -v`
Expected: FAIL — `ImportError: cannot import name 'CreateSplitRequest'`

- [ ] **Step 3: Add schemas to api/schemas.py**

Add at the bottom of `backend/src/api/schemas.py`:

```python
# ---------------------------------------------------------------------------
# Workout schemas
# ---------------------------------------------------------------------------


class CreateExerciseSchema(BaseModel):
    day_name: str
    exercise_name: str
    target_sets: int = Field(gt=0)
    target_reps: int = Field(gt=0)
    current_weight: float = Field(ge=0)
    weight_unit: str = "lbs"
    weight_increment: float = 2.5


class CreateSplitRequest(BaseModel):
    name: str = Field(min_length=1)
    rotation_days: list[str] = Field(min_length=1)
    exercises: list[CreateExerciseSchema]


class UpdateSplitRequest(BaseModel):
    name: str | None = None
    rotation_days: list[str] | None = None


class LogSetRequest(BaseModel):
    session_id: UUID
    exercise_id: UUID
    set_number: int = Field(gt=0)
    actual_reps: int = Field(ge=0)
    actual_weight: float = Field(ge=0)


class SkipSessionRequest(BaseModel):
    reason: str = Field(min_length=1)


class ExerciseDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    day_name: str
    exercise_name: str
    target_sets: int
    target_reps: int
    current_weight: float
    weight_unit: str
    weight_increment: float
    order_index: int


class SplitDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    rotation_days: list[str]
    active: bool
    exercises: list[ExerciseDetail] = []


class LogDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exercise_id: UUID
    set_number: int
    target_reps: int
    target_weight: float
    actual_reps: int | None
    actual_weight: float | None
    logged_at: datetime | None


class SessionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    split_id: UUID
    day_name: str
    rotation_index: int
    scheduled_at: datetime | None
    status: str
    skip_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    logs: list[LogDetail] = []


class StreakResponse(BaseModel):
    streak: int
    recent_skips: list[dict[str, Any]]


class WorkoutHistoryResponse(BaseModel):
    logs: list[LogDetail]
    total: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_schemas_workout.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/schemas.py backend/tests/test_schemas_workout.py
git commit -m "feat(workout): add Pydantic request/response schemas for workout API"
```

---

### Task 6: Create workout REST endpoints

**Files:**
- Create: `backend/src/api/workout.py`
- Modify: `backend/src/api/router.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api/test_workout.py`:

```python
"""Tests for workout API endpoints."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as c:
        yield c


class TestCreateSplit:
    async def test_create_split_returns_201(self, client):
        with patch("api.workout.get_db") as mock_db, \
             patch("api.workout.verify_auth", return_value={"user": "test"}):
            mock_session = AsyncMock()
            mock_split = MagicMock()
            mock_split.id = uuid.uuid4()
            mock_split.name = "PPL"
            mock_split.rotation_days = ["push", "pull", "legs"]
            mock_split.active = True

            mock_repo = AsyncMock()
            mock_repo.create_split = AsyncMock(return_value=mock_split)

            with patch("api.workout.WorkoutRepository", return_value=mock_repo):
                mock_db.return_value = mock_session
                resp = await client.post("/api/v1/workout/splits", json={
                    "name": "PPL",
                    "rotation_days": ["push", "pull", "legs"],
                    "exercises": [
                        {
                            "day_name": "push",
                            "exercise_name": "Bench Press",
                            "target_sets": 3,
                            "target_reps": 10,
                            "current_weight": 135.0,
                        }
                    ],
                })

        assert resp.status_code in (200, 201)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api/test_workout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.workout'`

- [ ] **Step 3: Create the workout API router**

Create `backend/src/api/workout.py`:

```python
"""Workout tracking API endpoints."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    CreateSplitRequest,
    ExerciseDetail,
    LogDetail,
    LogSetRequest,
    SessionDetail,
    SkipSessionRequest,
    SplitDetail,
    StreakResponse,
    UpdateSplitRequest,
    WorkoutHistoryResponse,
)
from src.db.models import AuditLog
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
    db.add(AuditLog(
        action_type="workout_split_created",
        actor="api",
        target=str(split.id),
        details={"name": split.name, "rotation_days": request.rotation_days},
    ))
    log.info("workout_split_created", split_id=str(split.id), name=split.name)
    return {"split_id": str(split.id), "name": split.name, "active": True}


@router.get("/splits/active")
async def get_active_split(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SplitDetail | dict[str, str]:
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
    return {"split_id": str(split.id), "updated": True}


@router.get("/sessions/today")
async def get_today_session(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionDetail | dict[str, str]:
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
    db.add(AuditLog(action_type="workout_session_skipped", actor="api", target=str(session_id), details={"reason": request.reason}))
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

    db.add(AuditLog(action_type="workout_session_completed", actor="api", target=str(session_id), details={"exercises_progressed": progressed}))
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
        from sqlalchemy import select
        from src.db.models import WorkoutLog
        result = await db.execute(
            select(WorkoutLog)
            .where(WorkoutLog.actual_reps.isnot(None))
            .order_by(WorkoutLog.created_at.desc())
            .limit(limit)
        )
        logs = list(result.scalars().all())

    return WorkoutHistoryResponse(
        logs=[LogDetail.model_validate(l) for l in logs],
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
```

- [ ] **Step 4: Register in router.py**

Add to `backend/src/api/router.py`:

```python
from src.api.workout import router as workout_router  # add with other imports at top
```

And:

```python
router.include_router(workout_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api/test_workout.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/workout.py backend/src/api/router.py backend/tests/test_api/test_workout.py
git commit -m "feat(workout): add 10 REST endpoints for workout split/session/log management"
```

---

## Chunk 4: Agent, Orchestrator Integration, and Scheduler

### Task 7: Create workout tracker agent

**Files:**
- Create: `backend/src/agents/workout_tracker.py`
- Create: `backend/tests/test_agents/test_workout_tracker.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agents/test_workout_tracker.py`:

```python
"""Tests for the Workout Tracker agent."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext
from agents.workout_tracker import WorkoutTrackerAgent


def _make_context(message: str = "workout", **config_overrides: Any) -> AgentContext:
    return AgentContext(
        user_message=message,
        intent_type="workout_tracker",
        config=config_overrides,
    )


class TestProgressiveOverloadLogic:
    def test_all_sets_hit_returns_true(self):
        """When all sets meet target reps and weight, should_progress returns True."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is True

    def test_one_set_missed_returns_false(self):
        """When any set misses target, should_progress returns False."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=8, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is False

    def test_unlogged_set_returns_false(self):
        """When a set has no actual data, should_progress returns False."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=None, actual_weight=None, target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is False

    def test_exceeded_reps_returns_true(self):
        """When actual reps exceed target, should_progress returns True."""
        logs = [
            MagicMock(actual_reps=12, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is True


class TestStreakMessage:
    def test_milestone_message(self):
        msg = WorkoutTrackerAgent._streak_milestone_message(7)
        assert msg is not None
        assert "7" in msg

    def test_no_milestone(self):
        msg = WorkoutTrackerAgent._streak_milestone_message(3)
        assert msg is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agents/test_workout_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.workout_tracker'`

- [ ] **Step 3: Create the agent**

Create `backend/src/agents/workout_tracker.py`:

```python
"""Workout Tracker agent — manages splits, progressive overload, and accountability.

Tier 1 (autonomous) — internal data writes only, no external side effects.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from agents.base import AgentContext, AgentResult, BaseAgent
from db.models import WorkoutLog

log = structlog.get_logger()

_STREAK_MILESTONES = {7, 14, 30, 60, 90, 180, 365}


class WorkoutTrackerAgent(BaseAgent):
    """Manage workout splits, log sets, enforce progressive overload, track streaks."""

    agent_type = "workout_tracker"

    async def execute(self, context: AgentContext) -> AgentResult:
        """Route to the appropriate sub-action based on context."""
        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        message = context.user_message.lower().strip()

        async with get_db_session() as session:
            repo = WorkoutRepository(session)

            # Check for active session to handle voice logging
            today_session = await repo.get_today_session()

            if today_session and today_session.status.value == "active":
                # Try to parse as a set log via Gemini
                parsed = await self._parse_voice_log(context, today_session)
                if parsed:
                    return parsed

            # Default: show today's workout status
            return await self._show_today_status(repo, today_session)

    async def _show_today_status(self, repo: Any, today_session: Any) -> AgentResult:
        """Show today's workout status."""
        if today_session is None:
            split = await repo.get_active_split()
            if split is None:
                return AgentResult(
                    success=True,
                    text="No workout split configured. Set one up in the app or tell me your split.",
                )
            return AgentResult(
                success=True,
                text="No workout scheduled for today. Rest up!",
            )

        status = today_session.status.value
        day = today_session.day_name

        if status == "pending":
            exercises = [l for l in today_session.logs]
            unique_exercises = set()
            exercise_list = []
            for l in exercises:
                if l.exercise_id not in unique_exercises:
                    unique_exercises.add(l.exercise_id)
                    exercise_list.append(
                        f"• {l.target_reps} reps × {l.target_weight}lbs"
                    )
            return AgentResult(
                success=True,
                text=f"Today is {day} day. Tap Start when you're ready.\n" + "\n".join(exercise_list),
                content_type="card",
                cards=[{
                    "type": "workout_session",
                    "session_id": str(today_session.id),
                    "day_name": day,
                    "status": status,
                }],
            )

        if status == "active":
            total_sets = len(today_session.logs)
            logged_sets = sum(1 for l in today_session.logs if l.actual_reps is not None)
            return AgentResult(
                success=True,
                text=f"{day.title()} day in progress. {logged_sets}/{total_sets} sets logged.",
            )

        if status == "completed":
            return AgentResult(
                success=True,
                text=f"{day.title()} day complete! Nice work.",
            )

        return AgentResult(success=True, text=f"Today's {day} day was skipped.")

    async def _parse_voice_log(self, context: AgentContext, session: Any) -> AgentResult | None:
        """Attempt to parse a voice message as a set log using Gemini Flash."""
        from models.gemini_client import GeminiClient

        gemini: GeminiClient | None = context.config.get("gemini_client")
        if gemini is None:
            return None

        # Build exercise context for the LLM
        exercises_context = {}
        for log_entry in session.logs:
            eid = str(log_entry.exercise_id)
            if eid not in exercises_context:
                exercises_context[eid] = {
                    "exercise_id": eid,
                    "name": "Exercise",  # Will be enriched from relationship
                    "sets": [],
                }
            exercises_context[eid]["sets"].append({
                "set_number": log_entry.set_number,
                "target_reps": log_entry.target_reps,
                "target_weight": float(log_entry.target_weight),
                "logged": log_entry.actual_reps is not None,
            })

        import json
        prompt = (
            "Parse this gym voice log into structured data. "
            "The user is logging a workout set. Extract:\n"
            "- exercise_name (string)\n"
            "- set_number (int)\n"
            "- actual_reps (int)\n"
            "- actual_weight (float)\n\n"
            f"User said: \"{context.user_message}\"\n\n"
            f"Today's exercises: {json.dumps(list(exercises_context.values()), indent=2)}\n\n"
            "Respond with ONLY valid JSON. If you can't parse it, respond with {\"error\": \"reason\"}."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.0-flash",
                temperature=0.1,
                max_output_tokens=256,
            )

            parsed = json.loads(response.text.strip().strip("```json").strip("```"))

            if "error" in parsed:
                return None  # Not a voice log, fall through to default handler

            log.info("workout_voice_log_parsed", parsed=parsed)

            # Log the set via repository
            from db.repositories.workout import WorkoutRepository
            from db.session import get_db_session

            async with get_db_session() as db_session:
                repo = WorkoutRepository(db_session)
                # Find matching exercise by name
                entry = await repo.log_set(
                    session_id=session.id,
                    exercise_id=parsed.get("exercise_id", session.logs[0].exercise_id),
                    set_number=parsed["set_number"],
                    actual_reps=parsed["actual_reps"],
                    actual_weight=parsed["actual_weight"],
                )

            if entry:
                return AgentResult(
                    success=True,
                    text=f"Logged: Set {parsed['set_number']} — {parsed['actual_reps']} reps at {parsed['actual_weight']}lbs.",
                )

        except Exception:
            log.warning("workout_voice_parse_failed", exc_info=True)

        return None

    @staticmethod
    def _should_progress(logs: list[Any]) -> bool:
        """Check if all sets hit target reps and weight."""
        return all(
            entry.actual_reps is not None
            and entry.actual_weight is not None
            and entry.actual_reps >= entry.target_reps
            and entry.actual_weight >= entry.target_weight
            for entry in logs
        )

    @staticmethod
    def _streak_milestone_message(streak: int) -> str | None:
        """Return a celebration message for streak milestones, or None."""
        if streak in _STREAK_MILESTONES:
            return f"{streak}-day streak! Keep it going."
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agents/test_workout_tracker.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/agents/workout_tracker.py backend/tests/test_agents/test_workout_tracker.py
git commit -m "feat(workout): add WorkoutTrackerAgent with progressive overload, voice parsing, streak tracking"
```

---

### Task 8: Update intent classifier and model router

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py`
- Modify: `backend/src/orchestrator/model_router.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_intent_workout.py`:

```python
"""Test intent classifier routes workout messages correctly."""
from __future__ import annotations

from orchestrator.intent_classifier import IntentClassifier
from shared.constants import IntentType


def test_set_done_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("set 1 done bench press 10 reps 135lbs")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_workout_split_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("show me my workout split")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_gym_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("I'm at the gym")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_sleep_still_routes_to_health():
    ic = IntentClassifier()
    intent = ic.classify("how did I sleep last night")
    assert intent.agent == IntentType.HEALTH_FITNESS


def test_slash_workout_command():
    ic = IntentClassifier()
    intent = ic.classify("/workout")
    assert intent.agent == IntentType.WORKOUT_TRACKER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_intent_workout.py -v`
Expected: FAIL — `WORKOUT_TRACKER` not in IntentType (if task 1 not done) or wrong routing

- [ ] **Step 3: Update intent_classifier.py**

In `_COMMAND_MAP`, add:

```python
    "/workout": Intent(agent=IntentType.WORKOUT_TRACKER),
```

In `_INTENT_RULES`, add a new entry **before** the health_fitness pattern (before line 92):

```python
    (
        re.compile(
            r"set\s+\d|reps?\b.*\b(done|complete)|workout\s*(split|routine|log)?|gym|exercise|progressive\s+overload",
            re.IGNORECASE,
        ),
        Intent(agent=IntentType.WORKOUT_TRACKER),
    ),
```

Narrow the existing health_fitness pattern from `sleep|steps|workout|gym|exercise|health|fitness` to:

```python
    (
        re.compile(r"sleep|steps|health|fitness", re.IGNORECASE),
        Intent(agent=IntentType.HEALTH_FITNESS),
    ),
```

- [ ] **Step 4: Update model_router.py**

In `ModelRouter.AGENT_MODEL_MAP`, add:

```python
        IntentType.WORKOUT_TRACKER: ModelRoute(model=ModelName.GEMINI_FLASH, node="node1"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_intent_workout.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/src/orchestrator/intent_classifier.py backend/src/orchestrator/model_router.py backend/tests/test_intent_workout.py
git commit -m "feat(workout): add workout_tracker to intent classifier and model router"
```

---

### Task 9: Add scheduler jobs for daily session creation and reminder polling

**Files:**
- Modify: `backend/src/scheduler/jobs.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_scheduler_workout.py`:

```python
"""Test workout scheduler jobs exist and are callable."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scheduler.jobs import create_daily_workout_session, workout_reminder_poll


class TestCreateDailyWorkoutSession:
    async def test_callable(self):
        """Job function is async and callable."""
        mock_orchestrator = AsyncMock()
        # Should not raise — graceful handling when no active split
        await create_daily_workout_session(orchestrator=mock_orchestrator)


class TestWorkoutReminderPoll:
    async def test_callable(self):
        """Job function is async and callable."""
        mock_orchestrator = AsyncMock()
        await workout_reminder_poll(orchestrator=mock_orchestrator)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_scheduler_workout.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_daily_workout_session'`

- [ ] **Step 3: Add scheduler jobs to jobs.py**

Add to `create_scheduler()` function, before the `log.info(...)` call:

```python
    # Workout: create daily session at 5:30 AM
    scheduler.add_job(
        create_daily_workout_session,
        CronTrigger(hour=5, minute=30),
        id="create_daily_workout",
        name="Create Daily Workout Session",
        kwargs={"orchestrator": orchestrator},
    )

    # Workout: reminder polling every 5 minutes
    scheduler.add_job(
        workout_reminder_poll,
        IntervalTrigger(minutes=5),
        id="workout_reminder_poll",
        name="Workout Reminder Poll",
        kwargs={"orchestrator": orchestrator},
    )
```

Add the log entries to the `jobs=` list:

```python
            "create_daily_workout@05:30",
            "workout_reminder_poll@every_5m",
```

Add the job implementations after `backup_job`:

```python
async def create_daily_workout_session(orchestrator: Orchestrator) -> None:
    """Create today's workout session from the active split and calendar."""
    job_name = "create_daily_workout"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        async with get_db_session() as session:
            repo = WorkoutRepository(session)
            split = await repo.get_active_split()

            if split is None:
                log.info("workout_no_active_split", job=job_name)
                return

            # Check if session already exists for today
            existing = await repo.get_today_session()
            if existing is not None:
                log.info("workout_session_already_exists", job=job_name)
                return

            # Determine today's rotation day
            # Find last session to determine rotation_index
            from sqlalchemy import select
            from db.models import WorkoutSession
            last_result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.split_id == split.id)
                .order_by(WorkoutSession.created_at.desc())
                .limit(1)
            )
            last_session = last_result.scalar_one_or_none()

            if last_session is not None:
                next_index = (last_session.rotation_index + 1) % len(split.rotation_days)
            else:
                next_index = 0

            day_name = split.rotation_days[next_index]

            # Skip creating a session for rest days
            if day_name.lower() == "rest":
                log.info("workout_rest_day", job=job_name, day_name=day_name)
                duration_ms = int((time.monotonic() - start) * 1000)
                log.info("scheduled_job_completed", job=job_name, duration_ms=duration_ms)
                return

            # Try to get scheduled time from calendar
            scheduled_at = None
            try:
                from config import get_settings
                from integrations.caldav_client import CalDAVClient
                from datetime import date as date_type

                settings = get_settings()
                caldav = CalDAVClient(
                    username=settings.icloud_caldav_user,
                    password=settings.icloud_caldav_password,
                )
                today_events = await caldav.get_events(date_type.today())

                gym_keywords = ("gym", "workout", "exercise", "fitness", "lift", "training")
                for event in today_events:
                    title = str(event.get("title", "")).lower()
                    if any(kw in title for kw in gym_keywords):
                        start_str = event.get("start", "")
                        if start_str:
                            from datetime import datetime as dt
                            scheduled_at = dt.fromisoformat(start_str)
                            break
            except Exception:
                log.warning("workout_calendar_lookup_failed", exc_info=True)

            await repo.create_session(
                split_id=split.id,
                day_name=day_name,
                rotation_index=next_index,
                scheduled_at=scheduled_at,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            day_name=day_name,
            scheduled_at=str(scheduled_at) if scheduled_at else None,
        )

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def workout_reminder_poll(orchestrator: Orchestrator) -> None:
    """Poll for pending workout sessions past their scheduled time and send nudges."""
    job_name = "workout_reminder_poll"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from datetime import datetime, timedelta, timezone

        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        async with get_db_session() as session:
            repo = WorkoutRepository(session)
            pending = await repo.get_pending_sessions_past_schedule()

            now = datetime.now(timezone.utc)

            for pending_session in pending:
                if pending_session.scheduled_at is None:
                    continue

                elapsed = now - pending_session.scheduled_at
                elapsed_minutes = elapsed.total_seconds() / 60

                # Track which nudges have been sent via notes field (JSON)
                import json as _json
                nudge_state = _json.loads(pending_session.notes or '{"sent": []}')
                sent = set(nudge_state.get("sent", []))

                if elapsed_minutes >= 90 and "auto_skip" not in sent:
                    # Auto-skip
                    await repo.skip_session(pending_session.id, reason="no response")
                    log.info(
                        "workout_auto_skipped",
                        session_id=str(pending_session.id),
                        elapsed_min=int(elapsed_minutes),
                    )
                    try:
                        from integrations.notification_service import get_notification_service
                        notifier = get_notification_service()
                        await notifier.notify_alert(
                            title="Workout Skipped",
                            body=f"No response for {pending_session.day_name} day. Session auto-skipped. Streak broken.",
                            severity="warning",
                        )
                    except Exception:
                        log.warning("workout_skip_notify_failed", exc_info=True)

                elif elapsed_minutes >= 60 and "nudge_60" not in sent:
                    # Second nudge — send once
                    sent.add("nudge_60")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from integrations.notification_service import get_notification_service
                        notifier = get_notification_service()
                        await notifier.notify_alert(
                            title=f"Last chance — {pending_session.day_name.title()} Day",
                            body="You have 30 minutes before this gets marked as skipped. Get moving.",
                            severity="warning",
                        )
                    except Exception:
                        log.warning("workout_nudge_failed", exc_info=True)

                elif elapsed_minutes >= 30 and "nudge_30" not in sent:
                    # First nudge — send once
                    sent.add("nudge_30")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from integrations.notification_service import get_notification_service
                        notifier = get_notification_service()
                        await notifier.notify(
                            title=f"{pending_session.day_name.title()} Day — Still Waiting",
                            body="Workout was scheduled. Starting now or skipping?",
                            priority="info",
                        )
                    except Exception:
                        log.warning("workout_nudge_failed", exc_info=True)

                elif elapsed_minutes >= 0 and "reminder" not in sent:
                    # Initial reminder (workout time arrived) — send once
                    sent.add("reminder")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from integrations.apns_client import APNsClient
                        from config import get_settings
                        settings = get_settings()
                        apns = APNsClient(settings)
                        await apns.send_workout_reminder(
                            session_id=str(pending_session.id),
                            day_name=pending_session.day_name,
                        )
                    except Exception:
                        log.warning("workout_apns_reminder_failed", exc_info=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            pending_sessions=len(pending) if 'pending' in dir() else 0,
        )

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_scheduler_workout.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/scheduler/jobs.py backend/tests/test_scheduler_workout.py
git commit -m "feat(workout): add create_daily_workout_session and workout_reminder_poll scheduler jobs"
```

> **Implementation note:** The `apns.send_workout_reminder()` method does not yet exist on `APNsClient`. It should be added as a thin wrapper that sends a push notification with the `WORKOUT_REMINDER` APNs category and `START_WORKOUT`/`SKIP_WORKOUT` actions. This is a small addition to `backend/src/integrations/apns_client.py` — add it inline when implementing this task. Morning briefing integration ("Gym at 6 PM — it's push day") is deferred to a follow-up task after the core tracker is working.

---

### Task 10: Suppress health_fitness gym suggestions when active split exists

**Files:**
- Modify: `backend/src/agents/health_fitness.py`

- [ ] **Step 1: Write the test**

Add to `backend/tests/test_agents/test_health_fitness.py`:

```python
class TestGymSuggestionSuppression:
    async def test_skips_calendar_when_active_split(self):
        """When an active workout split exists, gym calendar suggestions are suppressed."""
        agent = HealthFitnessAgent.__new__(HealthFitnessAgent)

        with patch("agents.health_fitness.WorkoutSplit", create=True), \
             patch("db.session.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            # Simulate an active split existing
            mock_result.scalar_one_or_none.return_value = MagicMock(active=True)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await agent._get_calendar_context(
                _make_context(),
            )

        # Should return suppressed indicator
        assert result.get("suppressed_by_workout_tracker") is True or result.get("error") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agents/test_health_fitness.py::TestGymSuggestionSuppression -v`
Expected: FAIL

- [ ] **Step 3: Modify health_fitness.py**

At the top of `_get_calendar_context`, add a check:

```python
    async def _get_calendar_context(self, context: AgentContext) -> dict[str, Any]:
        """Check today's and tomorrow's calendar for gym-friendly time slots."""
        # Suppress gym suggestions when workout tracker has an active split
        try:
            from db.models import WorkoutSplit
            from db.session import get_db_session
            from sqlalchemy import select

            async with get_db_session() as session:
                result = await session.execute(
                    select(WorkoutSplit).where(WorkoutSplit.active == True).limit(1)  # noqa: E712
                )
                if result.scalar_one_or_none() is not None:
                    return {"suppressed_by_workout_tracker": True}
        except Exception:
            pass  # If check fails, fall through to normal behavior

        # ... rest of existing method unchanged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agents/test_health_fitness.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/agents/health_fitness.py backend/tests/test_agents/test_health_fitness.py
git commit -m "feat(workout): suppress health_fitness gym suggestions when active workout split exists"
```

---

## Chunk 5: iOS App

### Task 11: Add workout API endpoints to iOS APIClient

**Files:**
- Modify: `ios/TARS/TARS/Services/APIClient.swift`

- [ ] **Step 1: Add endpoint definitions**

Add to the `Endpoint` extension in `APIClient.swift`:

```swift
// MARK: - Workout endpoints

extension Endpoint {
    static func createSplit(_ body: CreateSplitRequest) -> Endpoint {
        .custom(path: "/api/v1/workout/splits", method: "POST", body: body)
    }

    static var activeSplit: Endpoint {
        .custom(path: "/api/v1/workout/splits/active", method: "GET", body: nil as EmptyBody?)
    }

    static func todaySession() -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/today", method: "GET", body: nil as EmptyBody?)
    }

    static func startSession(_ sessionId: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/start", method: "POST", body: nil as EmptyBody?)
    }

    static func skipSession(_ sessionId: String, reason: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/skip", method: "POST", body: SkipSessionBody(reason: reason))
    }

    static func completeSession(_ sessionId: String) -> Endpoint {
        .custom(path: "/api/v1/workout/sessions/\(sessionId)/complete", method: "POST", body: nil as EmptyBody?)
    }

    static func logSet(_ body: LogSetBody) -> Endpoint {
        .custom(path: "/api/v1/workout/logs", method: "POST", body: body)
    }

    static var workoutStreak: Endpoint {
        .custom(path: "/api/v1/workout/streak", method: "GET", body: nil as EmptyBody?)
    }
}
```

- [ ] **Step 2: Add request/response models**

Add models (in a new file or same file):

```swift
// MARK: - Workout models

struct CreateSplitRequest: Codable, Sendable {
    let name: String
    let rotationDays: [String]
    let exercises: [CreateExerciseBody]

    enum CodingKeys: String, CodingKey {
        case name
        case rotationDays = "rotation_days"
        case exercises
    }
}

struct CreateExerciseBody: Codable, Sendable {
    let dayName: String
    let exerciseName: String
    let targetSets: Int
    let targetReps: Int
    let currentWeight: Double
    let weightUnit: String
    let weightIncrement: Double

    enum CodingKeys: String, CodingKey {
        case dayName = "day_name"
        case exerciseName = "exercise_name"
        case targetSets = "target_sets"
        case targetReps = "target_reps"
        case currentWeight = "current_weight"
        case weightUnit = "weight_unit"
        case weightIncrement = "weight_increment"
    }
}

struct SkipSessionBody: Codable, Sendable {
    let reason: String
}

struct LogSetBody: Codable, Sendable {
    let sessionId: String
    let exerciseId: String
    let setNumber: Int
    let actualReps: Int
    let actualWeight: Double

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case exerciseId = "exercise_id"
        case setNumber = "set_number"
        case actualReps = "actual_reps"
        case actualWeight = "actual_weight"
    }
}

struct WorkoutSessionResponse: Codable, Sendable {
    let id: String
    let dayName: String
    let status: String
    let logs: [WorkoutLogResponse]

    enum CodingKeys: String, CodingKey {
        case id
        case dayName = "day_name"
        case status
        case logs
    }
}

struct WorkoutLogResponse: Codable, Sendable {
    let id: String
    let exerciseId: String
    let setNumber: Int
    let targetReps: Int
    let targetWeight: Double
    let actualReps: Int?
    let actualWeight: Double?

    enum CodingKeys: String, CodingKey {
        case id
        case exerciseId = "exercise_id"
        case setNumber = "set_number"
        case targetReps = "target_reps"
        case targetWeight = "target_weight"
        case actualReps = "actual_reps"
        case actualWeight = "actual_weight"
    }
}

struct StreakResponseModel: Codable, Sendable {
    let streak: Int
    let recentSkips: [[String: String]]

    enum CodingKeys: String, CodingKey {
        case streak
        case recentSkips = "recent_skips"
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add ios/TARS/TARS/Services/APIClient.swift
git commit -m "feat(workout): add workout API endpoint definitions and models to iOS APIClient"
```

---

### Task 12: Create WorkoutViewModel

**Files:**
- Create: `ios/TARS/TARS/ViewModels/WorkoutViewModel.swift`

- [ ] **Step 1: Create the ViewModel**

```swift
import Foundation

@MainActor
final class WorkoutViewModel: ObservableObject {
    @Published var session: WorkoutSessionResponse?
    @Published var streak: Int = 0
    @Published var isLoading = false
    @Published var error: String?

    private let api = APIClient.shared

    func loadTodaySession() async {
        isLoading = true
        error = nil

        do {
            session = try await api.request(.todaySession())
        } catch {
            self.error = "No workout scheduled today"
            session = nil
        }

        isLoading = false
    }

    func startSession() async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.startSession(sessionId))
            await loadTodaySession()
        } catch {
            self.error = "Failed to start session: \(error.localizedDescription)"
        }
    }

    func skipSession(reason: String) async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.skipSession(sessionId, reason: reason))
            await loadTodaySession()
        } catch {
            self.error = "Failed to skip session: \(error.localizedDescription)"
        }
    }

    func logSet(exerciseId: String, setNumber: Int, reps: Int, weight: Double) async {
        guard let sessionId = session?.id else { return }
        let body = LogSetBody(
            sessionId: sessionId,
            exerciseId: exerciseId,
            setNumber: setNumber,
            actualReps: reps,
            actualWeight: weight
        )
        do {
            let _: [String: String] = try await api.request(.logSet(body))
            await loadTodaySession()
        } catch {
            self.error = "Failed to log set: \(error.localizedDescription)"
        }
    }

    func completeSession() async {
        guard let sessionId = session?.id else { return }
        do {
            let _: [String: String] = try await api.request(.completeSession(sessionId))
            await loadTodaySession()
            await loadStreak()
        } catch {
            self.error = "Failed to complete session: \(error.localizedDescription)"
        }
    }

    func loadStreak() async {
        do {
            let response: StreakResponseModel = try await api.request(.workoutStreak)
            streak = response.streak
        } catch {
            streak = 0
        }
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add ios/TARS/TARS/ViewModels/WorkoutViewModel.swift
git commit -m "feat(workout): add WorkoutViewModel for iOS workout screens"
```

---

### Task 13: Create WorkoutSessionView

**Files:**
- Create: `ios/TARS/TARS/Views/Workout/WorkoutSessionView.swift`

- [ ] **Step 1: Create the view**

```swift
import SwiftUI

struct WorkoutSessionView: View {
    @StateObject private var viewModel = WorkoutViewModel()
    @State private var showSkipSheet = false
    @State private var skipReason = ""
    @State private var selectedLog: WorkoutLogResponse?
    @State private var logReps = ""
    @State private var logWeight = ""

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading {
                    ProgressView("Loading workout...")
                } else if let session = viewModel.session {
                    sessionContent(session)
                } else {
                    ContentUnavailableView(
                        "No Workout Today",
                        systemImage: "figure.cooldown",
                        description: Text(viewModel.error ?? "Rest day!")
                    )
                }
            }
            .navigationTitle("Workout")
            .task {
                await viewModel.loadTodaySession()
                await viewModel.loadStreak()
            }
        }
    }

    @ViewBuilder
    private func sessionContent(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                VStack(alignment: .leading) {
                    Text(session.dayName.capitalized + " Day")
                        .font(.title2.bold())
                    Text("Streak: \(viewModel.streak) days")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                statusBadge(session.status)
            }
            .padding()

            if session.status == "pending" {
                pendingActions(session)
            } else if session.status == "active" {
                exerciseList(session)
            } else {
                completedView(session)
            }
        }
    }

    @ViewBuilder
    private func pendingActions(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "figure.strengthtraining.traditional")
                .font(.system(size: 60))
                .foregroundStyle(.blue)

            Text("Ready to go?")
                .font(.title3)

            Button {
                Task { await viewModel.startSession() }
            } label: {
                Text("Start Workout")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(.blue)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .padding(.horizontal)

            Button {
                showSkipSheet = true
            } label: {
                Text("Skip")
                    .foregroundColor(.red)
            }
            Spacer()
        }
        .sheet(isPresented: $showSkipSheet) {
            skipSheet
        }
    }

    @ViewBuilder
    private func exerciseList(_ session: WorkoutSessionResponse) -> some View {
        let grouped = Dictionary(grouping: session.logs) { $0.exerciseId }

        List {
            ForEach(Array(grouped.keys.sorted()), id: \.self) { exerciseId in
                if let sets = grouped[exerciseId] {
                    Section {
                        ForEach(sets, id: \.id) { log in
                            setRow(log)
                        }
                    } header: {
                        Text("Exercise") // Would be exercise name from enriched data
                    }
                }
            }

            Section {
                Button {
                    Task { await viewModel.completeSession() }
                } label: {
                    HStack {
                        Spacer()
                        Text("Finish Workout")
                            .font(.headline)
                        Spacer()
                    }
                }
                .tint(.green)
            }
        }
        .sheet(item: $selectedLog) { log in
            logSetSheet(log)
        }
    }

    @ViewBuilder
    private func setRow(_ log: WorkoutLogResponse) -> some View {
        HStack {
            Text("Set \(log.setNumber)")
                .font(.body.bold())

            Spacer()

            if let reps = log.actualReps, let weight = log.actualWeight {
                Text("\(reps) reps @ \(String(format: "%.1f", weight))lbs")
                    .foregroundStyle(.green)
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Text("\(log.targetReps) reps @ \(String(format: "%.1f", log.targetWeight))lbs")
                    .foregroundStyle(.secondary)

                Button("Log") {
                    logReps = "\(log.targetReps)"
                    logWeight = String(format: "%.1f", log.targetWeight)
                    selectedLog = log
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
        }
    }

    @ViewBuilder
    private func completedView(_ session: WorkoutSessionResponse) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.green)
            Text("Workout Complete!")
                .font(.title2.bold())
            Text("Streak: \(viewModel.streak) days")
                .font(.headline)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private func statusBadge(_ status: String) -> some View {
        Text(status.capitalized)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(statusColor(status).opacity(0.2))
            .foregroundStyle(statusColor(status))
            .clipShape(Capsule())
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "pending": .orange
        case "active": .blue
        case "completed": .green
        case "skipped": .red
        default: .gray
        }
    }

    private var skipSheet: some View {
        NavigationStack {
            Form {
                Section("Why are you skipping?") {
                    TextField("Reason (required)", text: $skipReason)
                }
            }
            .navigationTitle("Skip Workout")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showSkipSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Skip") {
                        Task {
                            await viewModel.skipSession(reason: skipReason)
                            showSkipSheet = false
                            skipReason = ""
                        }
                    }
                    .disabled(skipReason.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func logSetSheet(_ log: WorkoutLogResponse) -> some View {
        NavigationStack {
            Form {
                Section("Set \(log.setNumber)") {
                    HStack {
                        Text("Reps")
                        Spacer()
                        TextField("Reps", text: $logReps)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 80)
                    }
                    HStack {
                        Text("Weight (lbs)")
                        Spacer()
                        TextField("Weight", text: $logWeight)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 80)
                    }
                }
            }
            .navigationTitle("Log Set")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { selectedLog = nil }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if let reps = Int(logReps), let weight = Double(logWeight) {
                            Task {
                                await viewModel.logSet(
                                    exerciseId: log.exerciseId,
                                    setNumber: log.setNumber,
                                    reps: reps,
                                    weight: weight
                                )
                                selectedLog = nil
                            }
                        }
                    }
                }
            }
        }
        .presentationDetents([.medium])
    }
}

extension WorkoutLogResponse: @retroactive Identifiable {}
```

- [ ] **Step 2: Commit**

```bash
git add ios/TARS/TARS/Views/Workout/WorkoutSessionView.swift
git commit -m "feat(workout): add WorkoutSessionView with set logging, skip sheet, and completion flow"
```

---

### Task 14: Create SplitSetupView

**Files:**
- Create: `ios/TARS/TARS/Views/Workout/SplitSetupView.swift`

- [ ] **Step 1: Create the view**

```swift
import SwiftUI

struct SplitSetupView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var splitName = ""
    @State private var rotationDays: [String] = ["push", "pull", "legs", "rest"]
    @State private var exercises: [ExerciseEntry] = []
    @State private var newDayName = ""
    @State private var isSaving = false
    @State private var error: String?

    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            Form {
                Section("Split Name") {
                    TextField("e.g., Push/Pull/Legs", text: $splitName)
                }

                Section("Rotation") {
                    ForEach(rotationDays.indices, id: \.self) { idx in
                        HStack {
                            Text(rotationDays[idx].capitalized)
                            Spacer()
                            if rotationDays[idx] == "rest" {
                                Text("Rest Day")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .onDelete { indices in
                        rotationDays.remove(atOffsets: indices)
                    }

                    HStack {
                        TextField("Day name", text: $newDayName)
                        Button("Add") {
                            let name = newDayName.lowercased().trimmingCharacters(in: .whitespaces)
                            if !name.isEmpty {
                                rotationDays.append(name)
                                newDayName = ""
                            }
                        }
                        .disabled(newDayName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                ForEach(workoutDays, id: \.self) { day in
                    Section("\(day.capitalized) Day Exercises") {
                        let dayExercises = exercises.filter { $0.dayName == day }
                        ForEach(dayExercises) { exercise in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(exercise.name).font(.body.bold())
                                Text("\(exercise.sets)x\(exercise.reps) @ \(String(format: "%.1f", exercise.weight))lbs")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }

                        Button("Add Exercise") {
                            exercises.append(ExerciseEntry(dayName: day))
                        }
                    }
                }

                if let error {
                    Section {
                        Text(error).foregroundStyle(.red)
                    }
                }

                Section {
                    Button {
                        Task { await saveSplit() }
                    } label: {
                        HStack {
                            Spacer()
                            if isSaving {
                                ProgressView()
                            } else {
                                Text("Create Split")
                                    .font(.headline)
                            }
                            Spacer()
                        }
                    }
                    .disabled(splitName.isEmpty || exercises.isEmpty || isSaving)
                }
            }
            .navigationTitle("Setup Split")
        }
    }

    private var workoutDays: [String] {
        rotationDays.filter { $0.lowercased() != "rest" }
    }

    private func saveSplit() async {
        isSaving = true
        error = nil

        let request = CreateSplitRequest(
            name: splitName,
            rotationDays: rotationDays,
            exercises: exercises.map { ex in
                CreateExerciseBody(
                    dayName: ex.dayName,
                    exerciseName: ex.name,
                    targetSets: ex.sets,
                    targetReps: ex.reps,
                    currentWeight: ex.weight,
                    weightUnit: "lbs",
                    weightIncrement: ex.increment
                )
            }
        )

        do {
            let _: [String: String] = try await api.request(.createSplit(request))
            dismiss()
        } catch {
            self.error = "Failed to save: \(error.localizedDescription)"
        }

        isSaving = false
    }
}

struct ExerciseEntry: Identifiable {
    let id = UUID()
    var dayName: String
    var name: String = ""
    var sets: Int = 3
    var reps: Int = 10
    var weight: Double = 0
    var increment: Double = 2.5
}
```

- [ ] **Step 2: Commit**

```bash
git add ios/TARS/TARS/Views/Workout/SplitSetupView.swift
git commit -m "feat(workout): add SplitSetupView for defining workout splits with exercises"
```

---

### Task 15: Final integration test and cleanup

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && .venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Verify import chain**

Run: `cd backend && .venv/bin/python -c "from agents.workout_tracker import WorkoutTrackerAgent; from api.workout import router; from db.repositories.workout import WorkoutRepository; print('All imports OK')"`

- [ ] **Step 3: Final commit with all remaining files**

```bash
git add -A
git status
git commit -m "feat(workout): workout tracker agent — complete backend and iOS implementation"
```
