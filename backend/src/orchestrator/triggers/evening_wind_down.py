"""evening_wind_down — P4-15 first concrete trigger.

Conditions to fire (all must hold):
1. Sensor event came from ``tailscale_presence`` (home-proxy until P4-02 lands).
2. The named "home device" (TASIN_HOME_DEVICE env, default "tasins-macbook-pro")
   is online in the payload.
3. Local time is within evening window (default 18:00-22:30 user-local).
4. No CalDAV event starts within next NO_MEETING_WINDOW (default 2h).

Dedup: at most once per local-calendar-day. Tracked via DB ``audit_log``
row (action_type="trigger_fired", actor="evening_wind_down",
details={"trigger": "evening_wind_down", "day": "YYYY-MM-DD"}).
On fire: insert audit_log row + log structlog event. Side-effect
notification itself is left for a follow-up (out of scope this PR).

Location proxy note: P4-02 (dedicated location sensor) is not yet shipped.
Until it lands, ``tailscale_presence`` serves as home-proxy — when
Tasin's MacBook is online on the tailnet, we treat that as "at home".
This is a deliberate approximation documented here and in the PR body.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AuditLog
from db.repositories.audit_log import AuditLogRepository
from integrations.caldav_client import CalDAVClient
from orchestrator.trigger_engine import Trigger, TriggerEvent

log = structlog.get_logger()

DEFAULT_EVENING_START = time(18, 0)
DEFAULT_EVENING_END = time(22, 30)
DEFAULT_NO_MEETING_WINDOW = timedelta(hours=2)
DEFAULT_HOME_DEVICE = "tasins-macbook-pro"
DEFAULT_TZ = "America/New_York"  # Tasin's locale

_ACTION_TYPE = "trigger_fired"
_ACTOR = "evening_wind_down"


class EveningWindDownTrigger(Trigger):
    """Fire once per evening when home + evening-window + no meeting within 2h.

    Uses tailscale_presence as a home-proxy until P4-02 (location sensor)
    lands. Deduplication is via audit_log: at most one row per local day.
    """

    name = "evening_wind_down"
    subscribed_sensors = ["tailscale_presence"]

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        caldav: CalDAVClient | None = None,
        home_device: str | None = None,
        evening_start: time = DEFAULT_EVENING_START,
        evening_end: time = DEFAULT_EVENING_END,
        no_meeting_window: timedelta = DEFAULT_NO_MEETING_WINDOW,
        tz: tzinfo | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._caldav = caldav
        self._home_device = home_device or os.environ.get("TASIN_HOME_DEVICE", DEFAULT_HOME_DEVICE)
        self._evening_start = evening_start
        self._evening_end = evening_end
        self._no_meeting_window = no_meeting_window
        self._tz = tz or ZoneInfo(DEFAULT_TZ)
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Trigger interface
    # ------------------------------------------------------------------

    async def match(self, event: TriggerEvent) -> bool:
        """Return True when all four conditions hold."""
        # 1. Correct sensor channel
        if event.sensor != "tailscale_presence":
            return False

        # 2. Home device online
        devices: list[dict[str, Any]] = event.payload.get("devices", [])
        home_online = any(d.get("name") == self._home_device and d.get("online") for d in devices)
        if not home_online:
            return False

        # 3. Local time within evening window
        now_local = self._clock().astimezone(self._tz)
        local_time = now_local.time().replace(tzinfo=None)
        if not (self._evening_start <= local_time <= self._evening_end):
            return False

        # 4. Not already fired today
        if await self._already_fired_today():
            return False

        # 5. No upcoming meeting within window
        if await self._has_meeting_within(self._no_meeting_window):
            return False

        return True

    async def fire(self, event: TriggerEvent) -> None:
        """Record the trigger fired; notification side-effect deferred."""
        try:
            # Race-safe re-check before writing
            if await self._already_fired_today():
                log.debug("evening_wind_down.already_fired", sensor=event.sensor)
                return

            await self._record_fired(event)
            log.info(
                "evening_wind_down.fired",
                sensor=event.sensor,
                idempotency_token=event.idempotency_token,
            )
        except Exception:  # noqa: BLE001 — HC-09 fail-soft
            log.exception("evening_wind_down.fire_error")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _has_meeting_within(self, window: timedelta) -> bool:
        """Return True when CalDAV has an event starting within *window*."""
        if self._caldav is None:
            return False
        try:
            now_utc = self._clock()
            end_utc = now_utc + window
            # CalDAVClient.get_events takes date objects (start_date, end_date)
            events = await self._caldav.get_events(
                start_date=now_utc.date(),
                end_date=end_utc.date() + timedelta(days=1),  # inclusive upper bound
            )
            if not events:
                return False
            # Filter to events whose start falls within [now_utc, now_utc + window]
            now_local = self._clock().astimezone(self._tz)
            window_end_local = now_local + window
            for ev in events:
                start_str = ev.get("start", "")
                if not start_str:
                    continue
                try:
                    ev_start = datetime.fromisoformat(start_str)
                    if ev_start.tzinfo is None:
                        ev_start = ev_start.replace(tzinfo=self._tz)
                    if now_local <= ev_start <= window_end_local:
                        return True
                except ValueError:
                    continue
            return False
        except Exception:  # noqa: BLE001 — HC-09 fail-soft
            log.warning("evening_wind_down.caldav_error", exc_info=True)
            return False

    async def _already_fired_today(self) -> bool:
        """Return True when an audit_log row exists for today (local)."""
        today_str = self._local_today().isoformat()
        try:
            async with self._session_factory() as session:
                stmt = (
                    select(AuditLog)
                    .where(AuditLog.action_type == _ACTION_TYPE)
                    .where(AuditLog.actor == _ACTOR)
                    .where(AuditLog.details["day"].as_string() == today_str)
                    .limit(1)
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None
        except Exception:  # noqa: BLE001 — HC-09 fail-soft
            log.warning("evening_wind_down.already_fired_check_error", exc_info=True)
            return False

    async def _record_fired(self, event: TriggerEvent) -> None:
        """Insert an audit_log row marking the trigger fired today."""
        today_str = self._local_today().isoformat()
        async with self._session_factory() as session:
            repo = AuditLogRepository(session)
            await repo.create(
                action_type=_ACTION_TYPE,
                actor=_ACTOR,
                details={
                    "trigger": "evening_wind_down",
                    "day": today_str,
                    "idempotency_token": event.idempotency_token,
                },
            )
            await session.commit()

    def _local_today(self) -> date:
        """Return today's date in the user's local timezone."""
        return self._clock().astimezone(self._tz).date()
