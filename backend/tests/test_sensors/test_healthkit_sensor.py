"""Tests for ``sensors.healthkit.HealthKitSensor``.

TDD approach: tests written to the spec; implementation must satisfy them.

Key design notes
----------------
- HealthKitSensor is READ-ONLY w.r.t. ``health_data``.  It must NEVER add
  a HealthData row.  All writes it does are WorldState + AuditLog via
  the inherited ``publish()`` path.
- ``collect()`` and ``publish()`` each open their OWN session via
  ``session_factory``.  We pass a single factory that always returns the
  SAME ``_FakeSession`` so we can inspect both the DB reads (via
  ``execute``) and the DB writes (via ``add``) in one place.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import fakeredis.aioredis
import pytest

from db.models import HealthData, WorldState
from sensors.healthkit import HealthKitSensor

# ---------------------------------------------------------------------------
# Fake HealthData-like rows
# ---------------------------------------------------------------------------


def _make_health_row(
    data_type: str,
    value: float,
    unit: str,
    recorded_date: date | None = None,
) -> Any:
    """Return a lightweight object that mimics a HealthData ORM row."""
    from unittest.mock import MagicMock

    row = MagicMock(spec=HealthData)
    row.id = uuid.uuid4()
    row.data_type = data_type
    row.value = Decimal(str(value))
    row.unit = unit
    row.recorded_date = recorded_date or date.today()
    row.synced_at = datetime.now(UTC)
    return row


# ---------------------------------------------------------------------------
# Fake session / factory
# ---------------------------------------------------------------------------


class _FakeResult:
    """Mimics the object returned by ``session.execute(stmt)``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """In-memory async session that handles both reads and writes."""

    def __init__(self, health_rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.flushed = 0
        self.committed = 0
        self._health_rows: list[Any] = health_rows or []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _FakeResult:  # noqa: ARG002
        return _FakeResult(self._health_rows)


def _make_factory(session: _FakeSession):
    """Return an async context-manager factory that always yields *session*."""

    @asynccontextmanager
    async def factory():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


# ---------------------------------------------------------------------------
# Test 1 — collect() returns expected shape when data is present
# ---------------------------------------------------------------------------


async def test_collect_with_data_returns_expected_shape(redis):
    steps_row = _make_health_row("steps", 8500.0, "count")
    hr_row = _make_health_row("heart_rate", 72.0, "bpm")

    session = _FakeSession(health_rows=[steps_row, hr_row])
    factory = _make_factory(session)
    sensor = HealthKitSensor(session_factory=factory, redis=redis)

    result = await sensor.collect()

    assert "date" in result
    assert result["date"] == date.today().isoformat()

    assert "readings" in result
    assert "steps" in result["readings"]
    assert "heart_rate" in result["readings"]

    assert result["readings"]["steps"]["value"] == 8500.0
    assert result["readings"]["steps"]["unit"] == "count"
    assert "recorded_date" in result["readings"]["steps"]

    assert result["readings"]["heart_rate"]["value"] == 72.0

    assert "types_present" in result
    assert "steps" in result["types_present"]
    assert "heart_rate" in result["types_present"]
    # Sorted alphabetically
    assert result["types_present"] == sorted(result["types_present"])


# ---------------------------------------------------------------------------
# Test 2 — collect() returns empty shape when no data for today
# ---------------------------------------------------------------------------


async def test_collect_with_no_data_returns_empty(redis):
    session = _FakeSession(health_rows=[])
    factory = _make_factory(session)
    sensor = HealthKitSensor(session_factory=factory, redis=redis)

    result = await sensor.collect()

    assert result["date"] == date.today().isoformat()
    assert result["readings"] == {}
    assert result["types_present"] == []


# ---------------------------------------------------------------------------
# Test 3 — tick() writes WorldState but NOT HealthData
# ---------------------------------------------------------------------------


async def test_publish_writes_world_state_row_not_health_data(redis):
    steps_row = _make_health_row("steps", 6000.0, "count")
    session = _FakeSession(health_rows=[steps_row])
    factory = _make_factory(session)
    sensor = HealthKitSensor(session_factory=factory, redis=redis)

    ok = await sensor.tick()
    assert ok is True

    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    assert world_rows[0].sensor == "healthkit"

    health_data_rows = [obj for obj in session.added if isinstance(obj, HealthData)]
    assert health_data_rows == [], "Sensor must NOT write to health_data table"


# ---------------------------------------------------------------------------
# Test 4 — cadence guard blocks second tick within 300 s
# ---------------------------------------------------------------------------


async def test_cadence_guard_prevents_second_tick(redis):
    session = _FakeSession(health_rows=[_make_health_row("steps", 1000.0, "count")])
    factory = _make_factory(session)
    sensor = HealthKitSensor(session_factory=factory, redis=redis)

    first = await sensor.tick()
    second = await sensor.tick()

    assert first is True
    assert second is False

    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1, "Only one WorldState row should be written"
