"""Tests for ``db.repositories.world_state.WorldStateRepository``.

Covers the create/get_latest/get_latest_per_sensor/get_recent paths
needed by the Phase 3.5 sensor pipeline. PG-backed tests exercise
ORDER BY + LIMIT semantics (which the in-memory shim doesn't model).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from db.repositories.world_state import WorldStateRepository

_HAS_PG = bool(os.environ.get("TEST_DATABASE_URL", ""))
requires_pg = pytest.mark.skipif(not _HAS_PG, reason="Requires real PostgreSQL")


@pytest.mark.asyncio
async def test_create_inserts_row(test_db) -> None:
    repo = WorldStateRepository(test_db)
    row = await repo.create(sensor="weather", payload={"temp_f": 72})
    assert row.sensor == "weather"
    assert row.payload["temp_f"] == 72


@pytest.mark.asyncio
async def test_create_accepts_explicit_ts(test_db) -> None:
    repo = WorldStateRepository(test_db)
    fixed = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    row = await repo.create(sensor="weather", payload={"x": 1}, ts=fixed)
    assert row.ts == fixed


@requires_pg
@pytest.mark.asyncio
async def test_get_latest_returns_most_recent(test_db) -> None:
    repo = WorldStateRepository(test_db)
    base = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    await repo.create(sensor="weather", payload={"temp_f": 60}, ts=base)
    await repo.create(sensor="weather", payload={"temp_f": 75}, ts=base + timedelta(minutes=15))
    await repo.create(sensor="weather", payload={"temp_f": 70}, ts=base + timedelta(minutes=5))

    latest = await repo.get_latest("weather")
    assert latest is not None
    assert latest.payload["temp_f"] == 75


@requires_pg
@pytest.mark.asyncio
async def test_get_latest_returns_none_when_empty(test_db) -> None:
    repo = WorldStateRepository(test_db)
    assert await repo.get_latest("missing") is None


@requires_pg
@pytest.mark.asyncio
async def test_get_recent_respects_limit(test_db) -> None:
    repo = WorldStateRepository(test_db)
    base = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    for offset in range(5):
        await repo.create(
            sensor="weather",
            payload={"i": offset},
            ts=base + timedelta(minutes=offset),
        )
    rows = await repo.get_recent("weather", limit=3)
    assert len(rows) == 3
    assert [r.payload["i"] for r in rows] == [4, 3, 2]


@requires_pg
@pytest.mark.asyncio
async def test_get_latest_per_sensor_one_row_each(test_db) -> None:
    repo = WorldStateRepository(test_db)
    base = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    for sensor in ("weather", "healthkit", "tailscale", "spotify"):
        await repo.create(sensor=sensor, payload={"v": 1}, ts=base)
        await repo.create(sensor=sensor, payload={"v": 2}, ts=base + timedelta(minutes=5))

    snapshot = await repo.get_latest_per_sensor()
    assert set(snapshot) == {"weather", "healthkit", "tailscale", "spotify"}
    for row in snapshot.values():
        assert row.payload["v"] == 2


@pytest.mark.asyncio
async def test_create_each_sensor_distinct_writes(test_db) -> None:
    """In-memory friendly smoke: every sensor publishes once and the row sticks."""
    repo = WorldStateRepository(test_db)
    sensors = ("weather", "healthkit", "tailscale", "spotify", "location")
    for sensor in sensors:
        await repo.create(sensor=sensor, payload={"hello": sensor})
    # No PG-required ORDER BY, just confirm round-trip via direct in-memory store.
    if hasattr(test_db, "_store"):
        stored = [obj.sensor for obj in test_db._store]
        assert sorted(stored) == sorted(sensors)
