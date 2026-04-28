"""Pub/sub trigger engine — P4-10.

Subscribes to every ``tars:world:<sensor>`` channel via a single
``PSUBSCRIBE tars:world:*``, decodes incoming sensor payloads (the JSON
emitted by :class:`sensors.base.BaseSensor.publish`), and dispatches
each event to registered :class:`Trigger` instances.

Concrete triggers (P4-15..24 — evening_wind_down, meeting_prep,
sleep_recovery, ...) plug in via :meth:`TriggerEngine.register`. Each
trigger declares which sensor slugs it cares about (``["weather"]`` /
``["*"]`` / ``[]`` for all) and implements ``match()`` + ``fire()``.

Idempotency is *not* handled here. Sensors stamp every publish with a
fresh ``idempotency_token`` (uuid4); the engine simply forwards it to
each trigger as part of :class:`TriggerEvent`. Triggers are responsible
for their own dedup (DB row, in-memory LRU, etc.) so a misbehaving
trigger can't leak state into its peers.

HC-09: a trigger that raises in ``match`` or ``fire`` is logged and
skipped — the engine keeps draining the channel. A malformed JSON
payload, a publish on a non-``tars:world:*`` channel, or a transient
Redis hiccup are all logged and swallowed for the same reason: the
local loop must always continue.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()

CHANNEL_PATTERN = "tars:world:*"
_CHANNEL_PREFIX = "tars:world:"


@dataclass(frozen=True)
class TriggerEvent:
    """Decoded sensor publish — what triggers receive.

    ``payload`` is the *full* decoded message body (matches what
    :meth:`sensors.base.BaseSensor.publish` writes), not just the
    inner ``payload`` field. ``idempotency_token`` is hoisted out for
    convenience; it mirrors ``payload["idempotency_token"]``.
    """

    sensor: str
    channel: str
    payload: dict[str, Any]
    idempotency_token: str | None


class Trigger(ABC):
    """Base class for concrete triggers (P4-15..24).

    Subclasses live under ``orchestrator/triggers/<slug>.py`` and set:

    * ``name`` — stable slug used for logging + per-trigger dedup keys.
    * ``subscribed_sensors`` — list of sensor slugs this trigger reacts
      to. ``[]`` or ``["*"]`` means *every* sensor.
    """

    name: str = ""
    subscribed_sensors: list[str] = []

    @abstractmethod
    async def match(self, event: TriggerEvent) -> bool:
        """Return ``True`` when ``event`` should fire this trigger."""

    @abstractmethod
    async def fire(self, event: TriggerEvent) -> None:
        """Side effect for a matched event.

        Implementations MUST be idempotent on
        ``event.idempotency_token`` so a redelivered publish does not
        double-fire downstream actions.
        """


class TriggerEngine:
    """Async pub/sub engine that fans sensor publishes out to triggers.

    Lifecycle mirrors :class:`integrations.job_queue.JobQueue` consumers:
    construct → :meth:`register` triggers → :meth:`start` →
    :meth:`stop` for graceful shutdown.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        channel_pattern: str = CHANNEL_PATTERN,
        get_message_timeout: float = 1.0,
    ) -> None:
        self._redis = redis
        self._channel_pattern = channel_pattern
        self._get_message_timeout = get_message_timeout
        self._triggers: dict[str, Trigger] = {}
        self._task: asyncio.Task[None] | None = None
        self._pubsub: Any | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, trigger: Trigger) -> None:
        """Register ``trigger``. Re-registering the same name *replaces*.

        Replacing rather than raising lets the orchestrator hot-swap a
        trigger by re-importing its module without a full engine
        restart.
        """
        if not trigger.name:
            raise ValueError(f"Trigger {type(trigger).__name__} must set .name")
        if trigger.name in self._triggers:
            log.info("trigger_replaced", trigger=trigger.name)
        self._triggers[trigger.name] = trigger
        log.info(
            "trigger_registered",
            trigger=trigger.name,
            sensors=list(trigger.subscribed_sensors) or ["*"],
        )

    def unregister(self, name: str) -> None:
        """Remove a previously registered trigger. No-op if missing."""
        if self._triggers.pop(name, None) is not None:
            log.info("trigger_unregistered", trigger=name)

    def registered(self) -> list[str]:
        """Return the list of currently registered trigger names."""
        return list(self._triggers.keys())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to the channel pattern and spawn the run loop.

        Idempotent: a second call while running is a no-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(self._channel_pattern)
        self._task = asyncio.create_task(
            self._run(),
            name="trigger-engine",
        )
        log.info("trigger_engine_started", pattern=self._channel_pattern)

    async def stop(self) -> None:
        """Cancel the run loop, unsubscribe, and close the pubsub."""
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        pubsub = self._pubsub
        self._pubsub = None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.punsubscribe(self._channel_pattern)
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        log.info("trigger_engine_stopped")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Drain the pubsub until cancelled."""
        assert self._pubsub is not None  # set by start()
        pubsub = self._pubsub
        try:
            while not self._stop_event.is_set():
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self._get_message_timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — HC-09 fail-soft
                    log.warning(
                        "trigger_engine_get_message_failed",
                        error=str(exc),
                        exc_info=True,
                    )
                    await asyncio.sleep(0.05)
                    continue
                if message is None:
                    continue
                if message.get("type") not in {"pmessage", "message"}:
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Decode one raw pubsub message into a :class:`TriggerEvent`."""
        channel = _coerce_str(message.get("channel"))
        if channel is None or not channel.startswith(_CHANNEL_PREFIX):
            log.debug(
                "trigger_engine_skip_non_world_channel",
                channel=channel,
            )
            return

        raw = message.get("data")
        try:
            body = _decode_payload(raw)
        except Exception as exc:  # noqa: BLE001 — HC-09 fail-soft
            log.warning(
                "trigger_engine_decode_failed",
                channel=channel,
                error=str(exc),
            )
            return

        if not isinstance(body, dict):
            log.warning(
                "trigger_engine_decode_failed",
                channel=channel,
                error="payload_not_object",
            )
            return

        sensor = _coerce_str(body.get("sensor")) or channel.removeprefix(_CHANNEL_PREFIX)
        token = _coerce_str(body.get("idempotency_token"))
        event = TriggerEvent(
            sensor=sensor,
            channel=channel,
            payload=body,
            idempotency_token=token,
        )
        await self._dispatch(event)

    async def _dispatch(self, event: TriggerEvent) -> None:
        """Run every interested trigger's match→fire under isolation."""
        for trigger in list(self._triggers.values()):
            if not _trigger_listens_to(trigger, event.sensor):
                continue
            await self._run_trigger(trigger, event)

    async def _run_trigger(self, trigger: Trigger, event: TriggerEvent) -> None:
        """Run ``trigger.match`` then ``trigger.fire`` with full isolation."""
        try:
            matched = await trigger.match(event)
        except Exception as exc:  # noqa: BLE001 — HC-09 fail-soft
            log.error(
                "trigger_match_failed",
                trigger=trigger.name,
                sensor=event.sensor,
                idempotency_token=event.idempotency_token,
                error=str(exc),
                exc_info=True,
            )
            return

        if not matched:
            log.debug(
                "trigger_no_match",
                trigger=trigger.name,
                sensor=event.sensor,
                idempotency_token=event.idempotency_token,
            )
            return

        log.info(
            "trigger_matched",
            trigger=trigger.name,
            sensor=event.sensor,
            idempotency_token=event.idempotency_token,
        )
        try:
            await trigger.fire(event)
        except Exception as exc:  # noqa: BLE001 — HC-09 fail-soft
            log.error(
                "trigger_fire_failed",
                trigger=trigger.name,
                sensor=event.sensor,
                idempotency_token=event.idempotency_token,
                error=str(exc),
                exc_info=True,
            )
            return

        log.info(
            "trigger_fired",
            trigger=trigger.name,
            sensor=event.sensor,
            idempotency_token=event.idempotency_token,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> str | None:
    """Decode bytes→str, pass through str, return ``None`` otherwise."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        return value
    return None


def _decode_payload(raw: Any) -> Any:
    """Decode a pubsub ``data`` field (str or bytes) into a Python object."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise TypeError(f"unexpected pubsub data type: {type(raw).__name__}")
    return json.loads(raw)


def _trigger_listens_to(trigger: Trigger, sensor: str) -> bool:
    """Return True when ``trigger`` should receive events for ``sensor``."""
    sensors = trigger.subscribed_sensors
    if not sensors or "*" in sensors:
        return True
    return sensor in sensors


__all__ = [
    "CHANNEL_PATTERN",
    "Trigger",
    "TriggerEngine",
    "TriggerEvent",
]
