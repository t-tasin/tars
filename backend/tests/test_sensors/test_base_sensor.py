"""Tests for ``sensors.base.BaseSensor``.

Covers the sensor publish pipeline:
- happy path (collect → world_state row + Redis pub/sub + audit_log)
- fail-soft on collect exception
- cadence guard via ``min_interval_seconds``
- idempotency token in payload (downstream dedup)
- pubsub channel naming (``tars:world:<sensor>``)
- repeat-publish keeps token (caller controls dedup, sensor stays honest)
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import fakeredis.aioredis
import pytest

from db.models import AuditLog, WorldState
from sensors.base import BaseSensor

# ---------------------------------------------------------------------------
# Fakes
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
# Concrete sensors used in tests
# ---------------------------------------------------------------------------


class _DummySensor(BaseSensor):
    sensor_name = "dummy"
    min_interval_seconds = 0.0  # off by default; individual tests override

    def __init__(self, *args: Any, payload: dict | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._payload = payload or {"value": 42}
        self.collect_calls = 0

    async def collect(self) -> dict[str, Any]:
        self.collect_calls += 1
        return dict(self._payload)


class _ExplodingSensor(BaseSensor):
    sensor_name = "boom"
    min_interval_seconds = 0.0

    async def collect(self) -> dict[str, Any]:  # noqa: D401 - intentional raise
        raise RuntimeError("collect failed")


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
# Happy path
# ---------------------------------------------------------------------------


async def test_publish_writes_world_state_row(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)

    await sensor.publish({"value": 42})

    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    row = world_rows[0]
    assert row.sensor == "dummy"
    assert row.payload["value"] == 42


async def test_publish_emits_redis_pubsub_message(redis, session_pair):
    factory, _session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)

    pubsub = redis.pubsub()
    await pubsub.subscribe("tars:world:dummy")
    # Drain the subscribe ack message before publishing.
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    try:
        await sensor.publish({"value": 7})
        # fakeredis pub/sub is synchronous in-process; no need to sleep.
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        assert message is not None
        assert message["type"] == "message"
        assert message["channel"] == "tars:world:dummy"
        body = json.loads(message["data"])
        assert body["sensor"] == "dummy"
        assert body["payload"] == {"value": 7}
        assert "idempotency_token" in body
        assert "ts" in body
    finally:
        await pubsub.unsubscribe("tars:world:dummy")
        await pubsub.aclose()


async def test_publish_writes_audit_log_entry(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)

    await sensor.publish({"value": 1})

    audit = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit) == 1
    entry = audit[0]
    assert entry.action_type == "sensor_publish"
    assert entry.actor == "sensor"
    assert entry.target == "dummy"
    assert entry.details["sensor"] == "dummy"
    assert "idempotency_token" in entry.details


async def test_publish_payload_includes_idempotency_token(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)

    await sensor.publish({"value": 1})
    await sensor.publish({"value": 1})

    rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(rows) == 2
    tokens = {row.payload["idempotency_token"] for row in rows}
    # Idempotency token is unique per publish call; downstream dedup is
    # the consumer's job. We only guarantee the token field exists and
    # is populated.
    assert all(tokens), tokens
    assert len(tokens) == 2


# ---------------------------------------------------------------------------
# tick(): cadence + fail-soft
# ---------------------------------------------------------------------------


async def test_tick_runs_collect_then_publish(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis, payload={"x": 1})

    ok = await sensor.tick()
    assert ok is True
    assert sensor.collect_calls == 1
    rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(rows) == 1
    assert rows[0].payload["x"] == 1


async def test_tick_fail_soft_on_collect_exception(redis, session_pair):
    factory, session = session_pair
    sensor = _ExplodingSensor(session_factory=factory, redis=redis)

    ok = await sensor.tick()

    assert ok is False
    assert [obj for obj in session.added if isinstance(obj, WorldState)] == []


async def test_tick_cadence_guard_skips_when_too_soon(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)
    sensor.min_interval_seconds = 60.0

    first = await sensor.tick()
    second = await sensor.tick()

    assert first is True
    assert second is False
    rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(rows) == 1


async def test_tick_cadence_resets_after_interval(redis, session_pair):
    factory, session = session_pair
    sensor = _DummySensor(session_factory=factory, redis=redis)
    sensor.min_interval_seconds = 0.05

    assert (await sensor.tick()) is True
    await asyncio.sleep(0.1)
    assert (await sensor.tick()) is True

    rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Channel naming
# ---------------------------------------------------------------------------


async def test_pubsub_channel_uses_sensor_name(redis, session_pair):
    factory, _session = session_pair

    class WeatherSensor(_DummySensor):
        sensor_name = "weather"

    sensor = WeatherSensor(session_factory=factory, redis=redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe("tars:world:weather")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
    try:
        await sensor.publish({"temp_f": 70})
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "tars:world:weather"
    finally:
        await pubsub.unsubscribe("tars:world:weather")
        await pubsub.aclose()


async def test_sensor_name_required_on_subclass(redis, session_pair):
    factory, _session = session_pair

    class _NoName(BaseSensor):
        async def collect(self) -> dict[str, Any]:
            return {}

    with pytest.raises((TypeError, AttributeError, NotImplementedError)):
        _NoName(session_factory=factory, redis=redis)
