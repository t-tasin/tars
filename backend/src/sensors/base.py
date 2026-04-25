"""``BaseSensor`` — abstract sensor for the Phase 3.5 world_state pipeline.

Subclasses implement :meth:`collect` to return a serialisable ``dict``.
:meth:`publish` writes that payload into the ``world_state`` table, fans
it out on Redis pub/sub channel ``tars:world:<sensor_name>``, and records
an HC-08 audit log entry. :meth:`tick` is the cadence-guarded, fail-soft
entry point used by schedulers.

The sensor never owns its DB session; callers pass an async session
factory (an ``async`` context manager that yields an ``AsyncSession``).
This keeps sensors trivially testable with the in-memory session shim.

Idempotency: every publish stamps a fresh ``idempotency_token`` (uuid4)
into the payload + audit details. Downstream consumers dedup on it; the
sensor itself stays honest and emits one token per call.
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, ClassVar

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.audit_log import AuditLogRepository
from db.repositories.world_state import WorldStateRepository

log = structlog.get_logger()


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class BaseSensor(ABC):
    """Abstract base class for every T.A.R.S. sensor.

    Class attributes
    ----------------
    sensor_name
        Slug used in the Redis channel ``tars:world:<sensor_name>`` and as
        the ``sensor`` column on ``world_state``. Subclasses MUST set this.
    min_interval_seconds
        Minimum gap between two successful :meth:`tick` calls. ``0.0``
        disables the cadence guard.
    """

    sensor_name: ClassVar[str] = ""
    min_interval_seconds: ClassVar[float] = 0.0

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis: Redis,
    ) -> None:
        if not self.sensor_name:
            raise NotImplementedError(f"{type(self).__name__} must set sensor_name")
        self._session_factory = session_factory
        self._redis = redis
        self._last_tick_at: float | None = None

    # ------------------------------------------------------------------
    # Subclass hook
    # ------------------------------------------------------------------

    @abstractmethod
    async def collect(self) -> dict[str, Any]:
        """Fetch one reading from the underlying source.

        May raise; :meth:`tick` handles exceptions fail-soft.
        """

    # ------------------------------------------------------------------
    # Public pipeline
    # ------------------------------------------------------------------

    @property
    def channel(self) -> str:
        return f"tars:world:{self.sensor_name}"

    async def publish(self, payload: dict[str, Any]) -> str:
        """Persist ``payload`` to ``world_state`` + fan out on pub/sub.

        Returns the idempotency token stamped onto the payload so callers
        can correlate with downstream consumers. Always writes an HC-08
        ``audit_log`` row with ``action_type="sensor_publish"``.
        """
        token = str(uuid.uuid4())
        now = datetime.now(UTC)
        enriched: dict[str, Any] = dict(payload)
        enriched["idempotency_token"] = token

        async with self._session_factory() as session:
            world_repo = WorldStateRepository(session)
            audit_repo = AuditLogRepository(session)
            await world_repo.create(sensor=self.sensor_name, payload=enriched, ts=now)
            await audit_repo.create(
                action_type="sensor_publish",
                actor="sensor",
                target=self.sensor_name,
                source="sensor",
                details={
                    "sensor": self.sensor_name,
                    "idempotency_token": token,
                    "ts": now.isoformat(),
                },
            )

        message = json.dumps(
            {
                "sensor": self.sensor_name,
                "ts": now.isoformat(),
                "idempotency_token": token,
                "payload": payload,
            }
        )
        await self._redis.publish(self.channel, message)

        log.info(
            "sensor_publish",
            sensor=self.sensor_name,
            idempotency_token=token,
        )
        return token

    async def tick(self) -> bool:
        """Cadence-guarded, fail-soft ``collect()`` + ``publish()``.

        Returns ``True`` on a successful publish, ``False`` when the
        cadence guard skips or :meth:`collect` raises.
        """
        if self._cadence_guard_active():
            log.debug(
                "sensor_tick_skipped_cadence",
                sensor=self.sensor_name,
                min_interval_seconds=self.min_interval_seconds,
            )
            return False

        try:
            payload = await self.collect()
        except Exception as exc:  # noqa: BLE001 — fail-soft per HC-09
            log.warning(
                "sensor_collect_failed",
                sensor=self.sensor_name,
                error=str(exc),
                exc_info=True,
            )
            return False

        await self.publish(payload)
        self._last_tick_at = time.monotonic()
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cadence_guard_active(self) -> bool:
        if self.min_interval_seconds <= 0.0 or self._last_tick_at is None:
            return False
        return (time.monotonic() - self._last_tick_at) < self.min_interval_seconds


__all__ = ["BaseSensor", "SessionFactory"]
