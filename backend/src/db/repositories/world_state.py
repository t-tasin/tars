"""Repository for the world_state table (Phase 3.5 sensor pipeline)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WorldState

log = structlog.get_logger()


class WorldStateRepository:
    """CRUD operations for the ``world_state`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        sensor: str,
        payload: dict[str, Any],
        ts: datetime | None = None,
    ) -> WorldState:
        """Insert a new sensor reading."""
        kwargs: dict[str, Any] = {"sensor": sensor, "payload": payload}
        if ts is not None:
            kwargs["ts"] = ts
        record = WorldState(**kwargs)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_latest(self, sensor: str) -> WorldState | None:
        """Return the most recent reading for a single sensor."""
        result = await self._session.execute(
            select(WorldState).where(WorldState.sensor == sensor).order_by(WorldState.ts.desc()).limit(1)
        )
        return result.scalars().first()

    async def get_recent(self, sensor: str, limit: int = 50) -> list[WorldState]:
        """Return the N most recent readings for a sensor."""
        result = await self._session.execute(
            select(WorldState).where(WorldState.sensor == sensor).order_by(WorldState.ts.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_per_sensor(self) -> dict[str, WorldState]:
        """Return the most recent reading for every sensor (one row each)."""
        # Postgres-friendly DISTINCT ON wrapped via SQLAlchemy core would be
        # cleaner, but the simpler portable form keeps tests pure-Python.
        result = await self._session.execute(select(WorldState).order_by(WorldState.sensor, WorldState.ts.desc()))
        out: dict[str, WorldState] = {}
        for row in result.scalars().all():
            if row.sensor not in out:
                out[row.sensor] = row
        return out
