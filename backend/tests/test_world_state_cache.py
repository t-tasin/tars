"""Tests for orchestrator.world_state_cache (P3.5-07)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.world_state_cache import (
    SENSOR_FRESHNESS_SECONDS,
    read_fresh_sensor,
)


def _row(sensor: str, payload: dict[str, Any], ts: datetime) -> Any:
    row = MagicMock()
    row.sensor = sensor
    row.payload = payload
    row.ts = ts
    return row


def _session_returning(row: Any | None) -> Any:
    """Build a session whose WorldStateRepository(...).get_latest() returns ``row``."""

    # We patch the repo via a shim: the helper instantiates
    # ``WorldStateRepository(session)`` directly, so we patch *that* class.
    return row


@pytest.fixture(autouse=True)
def _patch_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace WorldStateRepository inside world_state_cache with a stub."""
    state: dict[str, Any] = {"row": None, "raises": None}

    class _StubRepo:
        def __init__(self, session: Any) -> None:
            self._session = session

        async def get_latest(self, sensor: str) -> Any | None:
            if state["raises"] is not None:
                raise state["raises"]
            return state["row"]

    monkeypatch.setattr(
        "orchestrator.world_state_cache.WorldStateRepository", _StubRepo
    )
    return state


@pytest.mark.asyncio
async def test_returns_payload_when_fresh(_patch_repo: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    fresh_ts = now - timedelta(seconds=60)
    _patch_repo["row"] = _row("weather", {"temp_f": 71.6, "conditions": "clear"}, fresh_ts)

    payload = await read_fresh_sensor(AsyncMock(), "weather", now=now)

    assert payload is not None
    assert payload["temp_f"] == 71.6
    assert payload["conditions"] == "clear"


@pytest.mark.asyncio
async def test_returns_none_when_stale(_patch_repo: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    # Way past 3× weather min_interval (2700s default)
    stale_ts = now - timedelta(seconds=SENSOR_FRESHNESS_SECONDS["weather"] + 60)
    _patch_repo["row"] = _row("weather", {"temp_f": 50}, stale_ts)

    payload = await read_fresh_sensor(AsyncMock(), "weather", now=now)

    assert payload is None


@pytest.mark.asyncio
async def test_returns_none_when_row_missing(_patch_repo: dict[str, Any]) -> None:
    _patch_repo["row"] = None

    payload = await read_fresh_sensor(AsyncMock(), "weather")

    assert payload is None


@pytest.mark.asyncio
async def test_returns_none_when_repo_raises(_patch_repo: dict[str, Any]) -> None:
    _patch_repo["raises"] = RuntimeError("connection refused")

    # Must not propagate (HC-09 fail-soft)
    payload = await read_fresh_sensor(AsyncMock(), "weather")

    assert payload is None


@pytest.mark.asyncio
async def test_max_age_override_respected(_patch_repo: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    ts = now - timedelta(seconds=120)
    _patch_repo["row"] = _row("weather", {"temp_f": 60}, ts)

    # Fresh under default (2700s)
    fresh = await read_fresh_sensor(AsyncMock(), "weather", now=now)
    assert fresh is not None

    # Stale under tighter override (60s)
    stale = await read_fresh_sensor(AsyncMock(), "weather", max_age_seconds=60, now=now)
    assert stale is None


@pytest.mark.asyncio
async def test_naive_timestamp_treated_as_utc(_patch_repo: dict[str, Any]) -> None:
    """In-memory test sessions may surface naive timestamps; treat as UTC."""
    now = datetime.now(UTC)
    naive_ts = now.replace(tzinfo=None) - timedelta(seconds=30)
    _patch_repo["row"] = _row("spotify", {"playing": True}, naive_ts)

    payload = await read_fresh_sensor(AsyncMock(), "spotify", now=now)

    assert payload is not None
    assert payload["playing"] is True


@pytest.mark.asyncio
async def test_unknown_sensor_uses_default_threshold(
    _patch_repo: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    ts = now - timedelta(seconds=120)
    _patch_repo["row"] = _row("mystery", {"x": 1}, ts)

    payload = await read_fresh_sensor(AsyncMock(), "mystery", now=now)

    # Default threshold is 3600s, so 120s is fresh
    assert payload is not None


def test_freshness_thresholds_are_3x_min_interval() -> None:
    """Document/enforce the 3× rule for freshness windows."""
    # Pull min_interval_seconds from each sensor module
    from sensors.healthkit import HealthKitSensor
    from sensors.spotify import SpotifySensor
    from sensors.tailscale import TailscaleSensor
    from sensors.weather import WeatherSensor

    expected = {
        "weather": WeatherSensor.min_interval_seconds * 3,
        "healthkit": HealthKitSensor.min_interval_seconds * 3,
        "tailscale_presence": TailscaleSensor.min_interval_seconds * 3,
        "spotify": SpotifySensor.min_interval_seconds * 3,
    }
    for sensor, target in expected.items():
        assert SENSOR_FRESHNESS_SECONDS[sensor] == target, (
            f"{sensor}: expected {target}s (3× min_interval), got {SENSOR_FRESHNESS_SECONDS[sensor]}"
        )
