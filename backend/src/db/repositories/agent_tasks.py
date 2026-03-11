"""Repository for the agent_tasks table."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AgentTask
from shared.constants import TaskStatus

log = structlog.get_logger()


class AgentTaskRepository:
    """CRUD operations for the ``agent_tasks`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> AgentTask:
        """Insert a new agent task."""
        task = AgentTask(**kwargs)
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_by_id(self, task_id: uuid.UUID) -> AgentTask | None:
        """Fetch a task by primary key."""
        result = await self._session.execute(
            select(AgentTask).where(AgentTask.id == task_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        task_id: uuid.UUID,
        status: TaskStatus,
        **kwargs: Any,
    ) -> AgentTask | None:
        """Update a task's status and optional fields."""
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.status = status
        if status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = datetime.now(timezone.utc)
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(timezone.utc)
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        await self._session.flush()
        return task

    async def get_pending(self, limit: int = 50) -> list[AgentTask]:
        """Fetch pending tasks, ordered by priority and creation time."""
        result = await self._session.execute(
            select(AgentTask)
            .where(AgentTask.status == TaskStatus.PENDING)
            .order_by(AgentTask.priority, AgentTask.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_agent(
        self,
        agent_type: str,
        limit: int = 50,
    ) -> list[AgentTask]:
        """Fetch recent tasks for a specific agent type."""
        result = await self._session.execute(
            select(AgentTask)
            .where(AgentTask.agent_type == agent_type)
            .order_by(AgentTask.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_todays_count(self) -> int:
        """Return count of tasks created today."""
        import datetime as dt

        today_start = dt.datetime.combine(
            dt.date.today(), dt.time.min, tzinfo=timezone.utc,
        )
        result = await self._session.execute(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.created_at >= today_start)
        )
        return result.scalar_one()

    async def get_todays_by_agent(self) -> dict[str, int]:
        """Return today's task count grouped by agent_type."""
        import datetime as dt

        today_start = dt.datetime.combine(
            dt.date.today(), dt.time.min, tzinfo=timezone.utc,
        )
        result = await self._session.execute(
            select(AgentTask.agent_type, func.count().label("count"))
            .where(AgentTask.created_at >= today_start)
            .group_by(AgentTask.agent_type)
        )
        return {row.agent_type: row.count for row in result.all()}
