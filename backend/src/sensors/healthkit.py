"""HealthKitSensor — reads today's health_data rows and publishes a summary.

This sensor is READ-ONLY with respect to ``health_data``.  The existing
``/api/health_data`` endpoint owns all writes to that table.  This sensor
only queries it and forwards an aggregated snapshot into ``world_state``
for the rest of the pipeline.

Cadence: every 300 s (5 minutes) — aligned with the typical HealthKit
background-delivery window on iOS.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select

from db.models import HealthData
from sensors.base import BaseSensor, SessionFactory

log = structlog.get_logger()


class HealthKitSensor(BaseSensor):
    """Aggregate today's HealthKit readings into a world_state snapshot.

    Queries ``health_data`` for rows where ``recorded_date == date.today()``,
    groups them by ``data_type``, and picks the most recent value per type
    (ordered by ``synced_at`` descending).

    Output payload shape::

        {
            "date": "2026-04-26",
            "readings": {
                "steps":      {"value": 8500.0, "unit": "count",  "recorded_date": "2026-04-26"},
                "heart_rate": {"value": 72.0,   "unit": "bpm",    "recorded_date": "2026-04-26"},
            },
            "types_present": ["heart_rate", "steps"],
        }
    """

    sensor_name = "healthkit"
    min_interval_seconds = 300.0

    def __init__(self, *, session_factory: SessionFactory, redis: Redis) -> None:
        super().__init__(session_factory=session_factory, redis=redis)

    async def collect(self) -> dict[str, Any]:
        today = date.today()
        today_iso = today.isoformat()

        async with self._session_factory() as session:
            result = await session.execute(
                select(HealthData).where(HealthData.recorded_date == today).order_by(HealthData.synced_at.desc())
            )
            rows: list[HealthData] = list(result.scalars().all())

        if not rows:
            return {"date": today_iso, "readings": {}, "types_present": []}

        # Group by data_type — because rows are already synced_at DESC,
        # the first row seen per type is the most recent.
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.data_type not in seen:
                seen[row.data_type] = {
                    "value": float(row.value),
                    "unit": row.unit,
                    "recorded_date": row.recorded_date.isoformat(),
                }

        types_present = sorted(seen.keys())

        log.debug(
            "healthkit_collect",
            date=today_iso,
            types_present=types_present,
        )

        return {
            "date": today_iso,
            "readings": seen,
            "types_present": types_present,
        }


__all__ = ["HealthKitSensor"]
