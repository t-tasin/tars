"""Repository for the agent_outputs table."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AgentOutput

log = structlog.get_logger()


class AgentOutputRepository:
    """CRUD operations for the ``agent_outputs`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> AgentOutput:
        """Insert a new agent output record."""
        output = AgentOutput(**kwargs)
        self._session.add(output)
        await self._session.flush()
        return output

    async def get_by_task(self, task_id: uuid.UUID) -> list[AgentOutput]:
        """Fetch all outputs for a specific agent task."""
        result = await self._session.execute(
            select(AgentOutput)
            .where(AgentOutput.task_id == task_id)
            .order_by(AgentOutput.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_recent_by_agent(
        self,
        output_type: str,
        limit: int = 20,
    ) -> list[AgentOutput]:
        """Fetch recent outputs filtered by output_type."""
        result = await self._session.execute(
            select(AgentOutput)
            .where(AgentOutput.output_type == output_type)
            .order_by(AgentOutput.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
