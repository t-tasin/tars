"""Tests for ``orchestrator.trigger_engine`` (P4-10).

Pub/sub end-to-end against ``fakeredis.aioredis`` — the same shape used
by ``test_base_sensor.py`` so the engine consumes whatever the real
sensor emits.

Each test publishes a message on ``tars:world:<sensor>`` and waits a
short interval for the engine's run loop to drain it. The loop's
``get_message`` timeout is dialed down (``0.05s``) so tests stay fast
without sleeping for whole seconds.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import fakeredis.aioredis
import pytest

from orchestrator.trigger_engine import (
    CHANNEL_PATTERN,
    Trigger,
    TriggerEngine,
    TriggerEvent,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingTrigger(Trigger):
    """Trigger that records every match/fire call."""

    def __init__(
        self,
        name: str,
        *,
        subscribed_sensors: list[str] | None = None,
        match_result: bool = True,
        match_raises: BaseException | None = None,
        fire_raises: BaseException | None = None,
    ) -> None:
        self.name = name
        self.subscribed_sensors = list(subscribed_sensors or [])
        self._match_result = match_result
        self._match_raises = match_raises
        self._fire_raises = fire_raises
        self.match_calls: list[TriggerEvent] = []
        self.fire_calls: list[TriggerEvent] = []

    async def match(self, event: TriggerEvent) -> bool:
        self.match_calls.append(event)
        if self._match_raises is not None:
            raise self._match_raises
        return self._match_result

    async def fire(self, event: TriggerEvent) -> None:
        self.fire_calls.append(event)
        if self._fire_raises is not None:
            raise self._fire_raises


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
async def engine(redis):
    eng = TriggerEngine(redis, get_message_timeout=0.05)
    try:
        yield eng
    finally:
        await eng.stop()


def _publish_payload(sensor: str, **extras: Any) -> str:
    """Build a JSON publish body shaped like ``BaseSensor.publish``."""
    body = {
        "sensor": sensor,
        "ts": "2026-04-28T12:00:00+00:00",
        "idempotency_token": extras.pop("idempotency_token", "tok-1"),
        "payload": extras.pop("payload", {"value": 1}),
    }
    body.update(extras)
    return json.dumps(body)


async def _wait_for(predicate, timeout: float = 1.5, interval: float = 0.02) -> bool:
    """Poll ``predicate()`` until truthy or ``timeout`` elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Subscription / channel filtering
# ---------------------------------------------------------------------------


async def test_start_psubscribes_to_world_pattern(redis, engine):
    await engine.start()

    # fakeredis exposes pubsub channel state via the underlying connection;
    # the most portable check is that publishing into the pattern reaches us.
    await asyncio.sleep(0.02)

    trigger = _RecordingTrigger("probe")
    engine.register(trigger)
    await redis.publish("tars:world:weather", _publish_payload("weather"))

    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)


async def test_uses_default_channel_pattern_constant():
    assert CHANNEL_PATTERN == "tars:world:*"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_single_trigger_match_then_fire(redis, engine):
    trigger = _RecordingTrigger("only", subscribed_sensors=["weather"])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))

    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)
    assert len(trigger.match_calls) == 1
    assert trigger.match_calls[0].sensor == "weather"


async def test_subscribed_sensor_filters_others(redis, engine):
    trigger = _RecordingTrigger("weather_only", subscribed_sensors=["weather"])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:spotify", _publish_payload("spotify"))
    await asyncio.sleep(0.15)

    assert trigger.match_calls == []
    assert trigger.fire_calls == []


async def test_empty_subscribed_sensors_matches_all(redis, engine):
    trigger = _RecordingTrigger("wildcard", subscribed_sensors=[])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))
    await redis.publish("tars:world:spotify", _publish_payload("spotify"))

    assert await _wait_for(lambda: len(trigger.fire_calls) == 2)
    sensors = sorted(e.sensor for e in trigger.fire_calls)
    assert sensors == ["spotify", "weather"]


async def test_star_subscribed_sensor_matches_all(redis, engine):
    trigger = _RecordingTrigger("star", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:tailscale_presence", _publish_payload("tailscale_presence"))

    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)


async def test_multiple_triggers_fan_out(redis, engine):
    a = _RecordingTrigger("a", subscribed_sensors=["weather"])
    b = _RecordingTrigger("b", subscribed_sensors=["weather", "spotify"])
    c = _RecordingTrigger("c", subscribed_sensors=["spotify"])
    for t in (a, b, c):
        engine.register(t)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))

    assert await _wait_for(lambda: a.fire_calls and b.fire_calls)
    await asyncio.sleep(0.1)
    assert len(a.fire_calls) == 1
    assert len(b.fire_calls) == 1
    assert c.fire_calls == []


async def test_match_false_skips_fire(redis, engine):
    trigger = _RecordingTrigger("nope", subscribed_sensors=["weather"], match_result=False)
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))

    assert await _wait_for(lambda: len(trigger.match_calls) == 1)
    await asyncio.sleep(0.1)
    assert trigger.fire_calls == []


# ---------------------------------------------------------------------------
# Fail-soft / HC-09
# ---------------------------------------------------------------------------


async def test_match_raise_does_not_kill_loop(redis, engine):
    bad = _RecordingTrigger(
        "bad",
        subscribed_sensors=["weather"],
        match_raises=RuntimeError("boom"),
    )
    good = _RecordingTrigger("good", subscribed_sensors=["weather"])
    engine.register(bad)
    engine.register(good)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))

    # Good trigger still fires despite bad's match() raising.
    assert await _wait_for(lambda: len(good.fire_calls) == 1)
    assert bad.fire_calls == []

    # And the loop survives — second publish dispatches.
    await redis.publish("tars:world:weather", _publish_payload("weather", idempotency_token="tok-2"))
    assert await _wait_for(lambda: len(good.fire_calls) == 2)


async def test_fire_raise_does_not_kill_loop(redis, engine):
    bad = _RecordingTrigger(
        "explode",
        subscribed_sensors=["weather"],
        fire_raises=ValueError("kaboom"),
    )
    engine.register(bad)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))
    assert await _wait_for(lambda: len(bad.fire_calls) == 1)

    # Next event must still dispatch.
    await redis.publish("tars:world:weather", _publish_payload("weather", idempotency_token="tok-2"))
    assert await _wait_for(lambda: len(bad.fire_calls) == 2)


async def test_malformed_json_payload_is_skipped(redis, engine):
    trigger = _RecordingTrigger("listener", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:weather", "not-json{{{")
    await asyncio.sleep(0.15)
    assert trigger.match_calls == []

    # Loop still alive: a valid publish dispatches.
    await redis.publish("tars:world:weather", _publish_payload("weather"))
    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)


async def test_non_world_channel_is_ignored(redis, engine):
    """Defensive — the pattern shouldn't match, but don't crash either way."""
    trigger = _RecordingTrigger("any", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    # Publishing on a non-tars:world: channel should not reach the engine
    # (PSUBSCRIBE filter handles it). Verify nothing fires.
    await redis.publish("tars:other:bus", _publish_payload("weather"))
    await asyncio.sleep(0.15)
    assert trigger.match_calls == []


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------


async def test_register_unregister_listed(redis, engine):
    a = _RecordingTrigger("alpha", subscribed_sensors=["*"])
    b = _RecordingTrigger("beta", subscribed_sensors=["*"])

    engine.register(a)
    engine.register(b)
    assert sorted(engine.registered()) == ["alpha", "beta"]

    engine.unregister("alpha")
    assert engine.registered() == ["beta"]

    # unregister missing → no-op
    engine.unregister("ghost")
    assert engine.registered() == ["beta"]


async def test_double_register_replaces_existing(redis, engine):
    first = _RecordingTrigger("dup", subscribed_sensors=["weather"])
    second = _RecordingTrigger("dup", subscribed_sensors=["weather"])

    engine.register(first)
    engine.register(second)
    assert engine.registered() == ["dup"]

    await engine.start()
    await redis.publish("tars:world:weather", _publish_payload("weather"))

    assert await _wait_for(lambda: len(second.fire_calls) == 1)
    assert first.fire_calls == []  # the replaced one never sees events


async def test_register_rejects_unnamed_trigger(engine):
    nameless = _RecordingTrigger("", subscribed_sensors=["*"])
    with pytest.raises(ValueError, match="must set .name"):
        engine.register(nameless)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_stop_cancels_task_cleanly(redis, engine):
    trigger = _RecordingTrigger("listener", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    await redis.publish("tars:world:weather", _publish_payload("weather"))
    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)

    await engine.stop()

    # Subsequent publishes don't dispatch.
    await redis.publish("tars:world:weather", _publish_payload("weather", idempotency_token="post-stop"))
    await asyncio.sleep(0.1)
    assert len(trigger.fire_calls) == 1


async def test_start_is_idempotent(redis, engine):
    trigger = _RecordingTrigger("once", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()
    await engine.start()  # second start = no-op

    await redis.publish("tars:world:weather", _publish_payload("weather"))
    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)
    # No duplicate dispatch from a second subscriber.
    await asyncio.sleep(0.1)
    assert len(trigger.fire_calls) == 1


# ---------------------------------------------------------------------------
# Event shape
# ---------------------------------------------------------------------------


async def test_idempotency_token_echoed_onto_event(redis, engine):
    trigger = _RecordingTrigger("echo", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    await redis.publish(
        "tars:world:weather",
        _publish_payload("weather", idempotency_token="abc-123"),
    )

    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)
    event = trigger.fire_calls[0]
    assert event.idempotency_token == "abc-123"
    assert event.channel == "tars:world:weather"
    assert event.sensor == "weather"
    assert event.payload["payload"] == {"value": 1}


async def test_missing_idempotency_token_is_none(redis, engine):
    trigger = _RecordingTrigger("nochar", subscribed_sensors=["*"])
    engine.register(trigger)
    await engine.start()

    body = json.dumps(
        {
            "sensor": "weather",
            "ts": "2026-04-28T12:00:00+00:00",
            "payload": {"x": 1},
        }
    )
    await redis.publish("tars:world:weather", body)

    assert await _wait_for(lambda: len(trigger.fire_calls) == 1)
    assert trigger.fire_calls[0].idempotency_token is None
