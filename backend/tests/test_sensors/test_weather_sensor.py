"""Tests for ``sensors.weather.WeatherSensor``.

TDD coverage:
- collect() returns the expected merged payload shape
- tick() writes a WorldState row with sensor == "weather"
- cadence guard prevents a second rapid tick
- pub/sub publishes to ``tars:world:weather``
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from db.models import WorldState
from sensors.weather import WeatherSensor

# ---------------------------------------------------------------------------
# Fakes (verbatim from test_base_sensor.py)
# ---------------------------------------------------------------------------


class _FakeSession:
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


# ---------------------------------------------------------------------------
# Mock WeatherClient helper
# ---------------------------------------------------------------------------


def _make_mock_weather_client() -> MagicMock:
    client = MagicMock()
    client.get_current = AsyncMock(
        return_value={
            "temp_c": 18.0,
            "temp_f": 64.4,
            "conditions": "partly cloudy",
            "humidity": 65,
            "wind_mph": 8.0,
            "icon": "02d",
            "location": "Wooster",
        }
    )
    client.get_daily_summary = AsyncMock(
        return_value={
            "summary": "18°C, partly cloudy, no rain expected.",
            "high": 22.0,
            "low": 14.0,
            "needs_umbrella": False,
            "suggestion": "Great weather — good day for outdoor activities.",
        }
    )
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_collect_returns_expected_shape(redis, session_pair):
    factory, _session = session_pair
    weather_client = _make_mock_weather_client()
    sensor = WeatherSensor(session_factory=factory, redis=redis, weather_client=weather_client)

    result = await sensor.collect()

    required_keys = {
        "temp_c",
        "temp_f",
        "conditions",
        "humidity",
        "wind_mph",
        "icon",
        "location",
        "daily_summary",
        "high",
        "low",
        "needs_umbrella",
        "suggestion",
    }
    assert required_keys == set(result.keys())

    # Spot-check values from the mock
    assert result["temp_c"] == 18.0
    assert result["temp_f"] == 64.4
    assert result["conditions"] == "partly cloudy"
    assert result["humidity"] == 65
    assert result["wind_mph"] == 8.0
    assert result["icon"] == "02d"
    assert result["location"] == "Wooster"
    assert result["daily_summary"] == "18°C, partly cloudy, no rain expected."
    assert result["high"] == 22.0
    assert result["low"] == 14.0
    assert result["needs_umbrella"] is False
    assert result["suggestion"] == "Great weather — good day for outdoor activities."


async def test_publish_writes_world_state_row(redis, session_pair):
    factory, session = session_pair
    weather_client = _make_mock_weather_client()
    sensor = WeatherSensor(session_factory=factory, redis=redis, weather_client=weather_client)

    ok = await sensor.tick()

    assert ok is True
    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    row = world_rows[0]
    assert row.sensor == "weather"
    assert "temp_c" in row.payload


async def test_cadence_guard_prevents_second_tick(redis, session_pair):
    factory, session = session_pair
    weather_client = _make_mock_weather_client()
    sensor = WeatherSensor(session_factory=factory, redis=redis, weather_client=weather_client)

    first = await sensor.tick()
    second = await sensor.tick()

    assert first is True
    assert second is False
    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1


async def test_pubsub_channel_is_tars_world_weather(redis, session_pair):
    factory, _session = session_pair
    weather_client = _make_mock_weather_client()
    sensor = WeatherSensor(session_factory=factory, redis=redis, weather_client=weather_client)

    pubsub = redis.pubsub()
    await pubsub.subscribe("tars:world:weather")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    try:
        payload = await sensor.collect()
        await sensor.publish(payload)

        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        assert message is not None
        assert message["type"] == "message"
        assert message["channel"] == "tars:world:weather"
        body = json.loads(message["data"])
        assert body["sensor"] == "weather"
        assert "temp_c" in body["payload"]
    finally:
        await pubsub.unsubscribe("tars:world:weather")
        await pubsub.aclose()
