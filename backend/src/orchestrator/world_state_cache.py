"""Helpers for reading sensor data from the ``world_state`` table.

Phase 3.5 sensors (weather/healthkit/tailscale/spotify) write fresh readings
to ``world_state`` on a fixed cadence. ``BriefingAgent`` and ``ContextBuilder``
should prefer these cached rows over re-polling integration APIs every call.

This module exposes :func:`read_fresh_sensor` — a single fail-soft entry
point that returns the latest payload for a sensor, or ``None`` when the
row is missing, stale, or the read raises (HC-09 graceful degradation).

Staleness threshold
-------------------
We treat a reading as fresh when its ``ts`` is within ``3 ×
sensor.min_interval_seconds`` of UTC now. The 3× factor gives sensors two
missed cadences of grace before falling back to direct integration polling
— enough to absorb a transient network blip without serving stale data.

The thresholds in :data:`SENSOR_FRESHNESS_SECONDS` are kept aligned with
``backend/src/sensors/*.py`` ``min_interval_seconds`` values:

    weather              900s  (15 min)  → fresh window 2700s
    healthkit            300s  ( 5 min)  → fresh window  900s
    tailscale_presence   120s  ( 2 min)  → fresh window  360s
    spotify               30s             → fresh window   90s
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.world_state import WorldStateRepository

log = structlog.get_logger()


# 3× the sensor's min_interval_seconds (see module docstring).
SENSOR_FRESHNESS_SECONDS: dict[str, float] = {
    "weather": 2700.0,
    "healthkit": 900.0,
    "tailscale_presence": 360.0,
    "spotify": 90.0,
}


async def read_fresh_sensor(
    session: AsyncSession,
    sensor: str,
    *,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the latest payload for ``sensor`` if it is still fresh.

    Args:
        session: Active async DB session.
        sensor: Sensor name (matches ``world_state.sensor`` column).
        max_age_seconds: Override the default freshness window. Falls back
            to :data:`SENSOR_FRESHNESS_SECONDS` then to a permissive 3600s
            for unknown sensors.
        now: Override the "now" timestamp (test seam).

    Returns:
        The sensor payload dict if a fresh reading exists, else ``None``.
        Never raises — any error logs a warning and returns ``None`` so
        callers can fall back to direct integration polling (HC-09).
    """
    threshold = max_age_seconds
    if threshold is None:
        threshold = SENSOR_FRESHNESS_SECONDS.get(sensor, 3600.0)

    try:
        repo = WorldStateRepository(session)
        row = await repo.get_latest(sensor)
    except Exception as exc:  # noqa: BLE001 — HC-09 fail-soft
        log.warning("world_state_read_failed", sensor=sensor, error=str(exc), exc_info=True)
        return None

    if row is None:
        log.debug("world_state_miss", sensor=sensor, reason="no_row")
        return None

    current = now or datetime.now(UTC)
    row_ts = getattr(row, "ts", None)
    # Defensive: real rows have a tz-aware ``datetime``. The conftest noop
    # session returns ``MagicMock`` for everything; if we receive anything
    # other than a real datetime, treat it as a miss rather than a bogus hit.
    if not isinstance(row_ts, datetime):
        log.debug("world_state_miss", sensor=sensor, reason="invalid_ts")
        return None
    # In-memory shim may surface naive datetimes; treat naive as UTC.
    if row_ts.tzinfo is None:
        row_ts = row_ts.replace(tzinfo=UTC)

    age_seconds = (current - row_ts).total_seconds()
    if age_seconds > threshold:
        log.debug(
            "world_state_stale",
            sensor=sensor,
            age_seconds=age_seconds,
            threshold_seconds=threshold,
        )
        return None

    log.debug(
        "world_state_hit",
        sensor=sensor,
        age_seconds=age_seconds,
        threshold_seconds=threshold,
    )
    return dict(row.payload or {})


__all__ = ["read_fresh_sensor", "SENSOR_FRESHNESS_SECONDS"]
