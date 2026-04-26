"""Tests for ``sensors.tailscale.TailscaleSensor``.

Covers:
- Online/offline classification based on lastSeen timestamp
- Empty device list edge case
- publish pipeline (WorldState row written)
- Cadence guard prevents a second tick too soon
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from db.models import WorldState
from sensors.tailscale import TailscaleSensor

# ---------------------------------------------------------------------------
# Fakes (mirrored from test_base_sensor.py)
# ---------------------------------------------------------------------------


class _FakeSession:
    """In-memory async session that records every ``add()``."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = 0
        self.committed = 0

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


def _session_factory() -> tuple[Any, _FakeSession]:
    """Build a callable that yields a single fake session via async ctx mgr."""
    session = _FakeSession()

    @asynccontextmanager
    async def factory():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return factory, session


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


@pytest.fixture()
def session_pair():
    factory, session = _session_factory()
    return factory, session


@pytest.fixture()
def sensor(session_pair, redis):
    factory, _session = session_pair
    return TailscaleSensor(
        session_factory=factory,
        redis=redis,
        api_key="test-tailscale-key",
    )


# ---------------------------------------------------------------------------
# Helper: build a mock httpx response
# ---------------------------------------------------------------------------


def _make_mock_response(devices_payload: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=devices_payload)
    return mock_response


# ---------------------------------------------------------------------------
# Test 1: parses online / offline devices correctly
# ---------------------------------------------------------------------------


async def test_collect_parses_online_devices(sensor):
    now = datetime.now(UTC)
    payload = {
        "devices": [
            {
                "id": "abc",
                "hostname": "tars1",
                "displayName": "tars1",
                "ipAddresses": ["100.119.114.125"],
                "os": "linux",
                "lastSeen": (now - timedelta(seconds=30)).isoformat(),
                "online": True,
            },
            {
                "id": "def",
                "hostname": "macbook",
                "displayName": "macbook",
                "ipAddresses": ["100.100.100.100"],
                "os": "macOS",
                "lastSeen": (now - timedelta(hours=2)).isoformat(),
                "online": False,
            },
        ]
    }
    mock_response = _make_mock_response(payload)

    with patch.object(sensor._http, "get", new=AsyncMock(return_value=mock_response)):
        result = await sensor.collect()

    assert result["online_count"] == 1
    assert result["total_count"] == 2
    assert len(result["devices"]) == 2

    online_device = next(d for d in result["devices"] if d["hostname"] == "tars1")
    assert online_device["online"] is True
    assert online_device["ip"] == "100.119.114.125"
    assert online_device["os"] == "linux"

    offline_device = next(d for d in result["devices"] if d["hostname"] == "macbook")
    assert offline_device["online"] is False


# ---------------------------------------------------------------------------
# Test 2: empty device list
# ---------------------------------------------------------------------------


async def test_collect_empty_devices(sensor):
    mock_response = _make_mock_response({"devices": []})

    with patch.object(sensor._http, "get", new=AsyncMock(return_value=mock_response)):
        result = await sensor.collect()

    assert result["online_count"] == 0
    assert result["total_count"] == 0
    assert result["devices"] == []


# ---------------------------------------------------------------------------
# Test 3: tick() writes a WorldState row
# ---------------------------------------------------------------------------


async def test_publish_writes_world_state_row(session_pair, redis):
    factory, session = session_pair
    s = TailscaleSensor(
        session_factory=factory,
        redis=redis,
        api_key="test-tailscale-key",
    )
    now = datetime.now(UTC)
    payload = {
        "devices": [
            {
                "id": "abc",
                "hostname": "tars1",
                "displayName": "tars1",
                "ipAddresses": ["100.119.114.125"],
                "os": "linux",
                "lastSeen": (now - timedelta(seconds=30)).isoformat(),
                "online": True,
            }
        ]
    }
    mock_response = _make_mock_response(payload)

    with patch.object(s._http, "get", new=AsyncMock(return_value=mock_response)):
        ok = await s.tick()

    assert ok is True
    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    row = world_rows[0]
    assert row.sensor == "tailscale_presence"
    assert "online_count" in row.payload


# ---------------------------------------------------------------------------
# Test 4: cadence guard prevents second tick too soon
# ---------------------------------------------------------------------------


async def test_cadence_guard_prevents_second_tick(session_pair, redis):
    factory, session = session_pair
    s = TailscaleSensor(
        session_factory=factory,
        redis=redis,
        api_key="test-tailscale-key",
    )
    now = datetime.now(UTC)
    payload = {
        "devices": [
            {
                "id": "abc",
                "hostname": "tars1",
                "displayName": "tars1",
                "ipAddresses": ["100.119.114.125"],
                "os": "linux",
                "lastSeen": (now - timedelta(seconds=30)).isoformat(),
                "online": True,
            }
        ]
    }
    mock_response = _make_mock_response(payload)

    with patch.object(s._http, "get", new=AsyncMock(return_value=mock_response)):
        first = await s.tick()
        second = await s.tick()

    assert first is True
    assert second is False

    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
