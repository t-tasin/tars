"""Tests for orchestrator.triggers.evening_wind_down (P4-15).

Covers the 12 cases called out in the spec:
 1. match True — home + evening + no meeting + not fired today
 2. match False — wrong sensor
 3. match False — home device offline
 4. match False — home device missing from payload
 5. match False — outside evening window (15:00)
 6. match False — outside evening window (23:30)
 7. match False — meeting within 2h
 8. match False — already fired today
 9. fire writes audit_log row with correct fields
10. fire is idempotent — second same-day call is a no-op
11. HC-09 — CalDAV exception in match doesn't propagate; treated as no-meeting
12. clock injection — pinned 19:00 → match True; pinned 16:00 → match False
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from orchestrator.trigger_engine import TriggerEvent
from orchestrator.triggers.evening_wind_down import (
    _ACTION_TYPE,
    _ACTOR,
    DEFAULT_HOME_DEVICE,
    DEFAULT_TZ,
    EveningWindDownTrigger,
)

# ---------------------------------------------------------------------------
# Constants / shared fixtures
# ---------------------------------------------------------------------------

_ET = ZoneInfo(DEFAULT_TZ)
_HOME_DEVICE = DEFAULT_HOME_DEVICE

# A timestamp firmly in the evening window: 19:00 ET
_EVENING_UTC = datetime(2026, 4, 28, 23, 0, 0, tzinfo=UTC)  # 19:00 ET
# A timestamp outside the window: 15:00 ET
_AFTERNOON_UTC = datetime(2026, 4, 28, 19, 0, 0, tzinfo=UTC)  # 15:00 ET
# A timestamp outside the window: 23:30 ET
_LATE_NIGHT_UTC = datetime(2026, 4, 29, 3, 30, 0, tzinfo=UTC)  # 23:30 ET


def _make_event(
    sensor: str = "tailscale_presence",
    devices: list[dict[str, Any]] | None = None,
    idempotency_token: str | None = None,
) -> TriggerEvent:
    """Build a minimal TriggerEvent for tailscale_presence."""
    if devices is None:
        devices = [{"name": _HOME_DEVICE, "online": True, "last_seen": "2026-04-28T19:00:00"}]
    payload: dict[str, Any] = {
        "sensor": sensor,
        "online_count": sum(1 for d in devices if d.get("online")),
        "total_count": len(devices),
        "devices": devices,
    }
    if idempotency_token:
        payload["idempotency_token"] = idempotency_token
    return TriggerEvent(
        sensor=sensor,
        channel=f"tars:world:{sensor}",
        payload=payload,
        idempotency_token=idempotency_token or str(uuid.uuid4()),
    )


def _make_session_factory(fired_today: bool = False):
    """Return an async context-manager session factory.

    The session's ``execute`` result is wired to simulate whether an
    audit_log row already exists for today.
    """
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = object() if fired_today else None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=scalar_result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield session

    return factory, session


def _make_trigger(
    *,
    fired_today: bool = False,
    clock: Any = None,
    caldav: Any = None,
    evening_start: time = time(18, 0),
    evening_end: time = time(22, 30),
) -> tuple[EveningWindDownTrigger, Any]:
    """Build an EveningWindDownTrigger with mocked deps."""
    if clock is None:
        clock = lambda: _EVENING_UTC  # noqa: E731  # default: 19:00 ET

    factory, session = _make_session_factory(fired_today=fired_today)
    trigger = EveningWindDownTrigger(
        session_factory=factory,
        caldav=caldav,
        home_device=_HOME_DEVICE,
        evening_start=evening_start,
        evening_end=evening_end,
        tz=_ET,
        clock=clock,
    )
    return trigger, session


# ---------------------------------------------------------------------------
# Test 1 — match True: all conditions satisfied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_true_all_conditions():
    """match() returns True when home + evening + no meeting + not fired."""
    trigger, _ = _make_trigger()
    event = _make_event()
    result = await trigger.match(event)
    assert result is True


# ---------------------------------------------------------------------------
# Test 2 — match False: wrong sensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_wrong_sensor():
    """match() returns False for a non-tailscale_presence sensor."""
    trigger, _ = _make_trigger()
    event = _make_event(sensor="weather")
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 3 — match False: home device offline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_device_offline():
    """match() returns False when the home device is offline."""
    trigger, _ = _make_trigger()
    devices = [{"name": _HOME_DEVICE, "online": False, "last_seen": "2026-04-28T12:00:00"}]
    event = _make_event(devices=devices)
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 4 — match False: home device missing from payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_device_missing():
    """match() returns False when the home device is absent from devices."""
    trigger, _ = _make_trigger()
    devices = [{"name": "some-other-device", "online": True}]
    event = _make_event(devices=devices)
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 5 — match False: outside evening window (15:00 ET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_afternoon():
    """match() returns False at 15:00 ET (before evening window)."""
    trigger, _ = _make_trigger(clock=lambda: _AFTERNOON_UTC)
    event = _make_event()
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 6 — match False: outside evening window (23:30 ET)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_late_night():
    """match() returns False at 23:30 ET (after evening window closes)."""
    trigger, _ = _make_trigger(clock=lambda: _LATE_NIGHT_UTC)
    event = _make_event()
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 7 — match False: meeting within 2h
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_meeting_within_window():
    """match() returns False when CalDAV reports a meeting within 2h."""
    # A meeting 45 min from now (well within the 2h window)
    meeting_start = _EVENING_UTC + timedelta(minutes=45)
    meeting_start_local = meeting_start.astimezone(_ET)

    caldav = AsyncMock()
    caldav.get_events = AsyncMock(
        return_value=[
            {
                "id": "abc",
                "title": "Sprint Planning",
                "start": meeting_start_local.isoformat(),
                "end": (meeting_start_local + timedelta(hours=1)).isoformat(),
                "all_day": False,
            }
        ]
    )
    trigger, _ = _make_trigger(caldav=caldav)
    event = _make_event()
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 8 — match False: already fired today
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_false_already_fired_today():
    """match() returns False when the trigger already fired today."""
    trigger, _ = _make_trigger(fired_today=True)
    event = _make_event()
    result = await trigger.match(event)
    assert result is False


# ---------------------------------------------------------------------------
# Test 9 — fire writes audit_log row with correct fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_writes_audit_log():
    """fire() creates an audit_log row with correct action_type, actor, details."""
    trigger, session = _make_trigger(fired_today=False)
    token = str(uuid.uuid4())
    event = _make_event(idempotency_token=token)

    created_kwargs: dict[str, Any] = {}

    with patch("orchestrator.triggers.evening_wind_down.AuditLogRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(side_effect=lambda **kw: created_kwargs.update(kw))
        MockRepo.return_value = mock_repo

        await trigger.fire(event)

    assert created_kwargs["action_type"] == _ACTION_TYPE
    assert created_kwargs["actor"] == _ACTOR
    details = created_kwargs["details"]
    assert details["trigger"] == "evening_wind_down"
    assert details["day"] == _EVENING_UTC.astimezone(_ET).date().isoformat()
    assert details["idempotency_token"] == token
    session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 10 — fire is idempotent: second call same day is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_idempotent_second_call():
    """fire() skips the audit_log insert on a second same-day call."""
    trigger, session = _make_trigger(fired_today=True)  # already fired
    event = _make_event()

    with patch("orchestrator.triggers.evening_wind_down.AuditLogRepository") as MockRepo:
        mock_repo = AsyncMock()
        MockRepo.return_value = mock_repo

        await trigger.fire(event)

    # create() must NOT have been called
    mock_repo.create.assert_not_awaited()
    session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 11 — HC-09: CalDAV exception treated as no-meeting (match still True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_caldav_exception_treated_as_no_meeting():
    """CalDAV exception in _has_meeting_within → fail-soft → no-meeting → match True."""
    caldav = AsyncMock()
    caldav.get_events = AsyncMock(side_effect=RuntimeError("caldav down"))

    trigger, _ = _make_trigger(caldav=caldav)
    event = _make_event()
    # Should not raise; CalDAV error → treat as no meeting → match proceeds
    result = await trigger.match(event)
    assert result is True


# ---------------------------------------------------------------------------
# Test 12 — clock injection: 19:00 → True; 16:00 → False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clock_injection_evening_true():
    """Pinned clock at 19:00 ET → match returns True."""
    clock = lambda: datetime(2026, 4, 28, 23, 0, tzinfo=UTC)  # 19:00 ET  # noqa: E731
    trigger, _ = _make_trigger(clock=clock)
    event = _make_event()
    assert await trigger.match(event) is True


@pytest.mark.asyncio
async def test_clock_injection_afternoon_false():
    """Pinned clock at 16:00 ET → match returns False (before window)."""
    clock = lambda: datetime(2026, 4, 28, 20, 0, tzinfo=UTC)  # 16:00 ET  # noqa: E731
    trigger, _ = _make_trigger(clock=clock)
    event = _make_event()
    assert await trigger.match(event) is False


# ---------------------------------------------------------------------------
# Additional edge-case tests (beyond the 12 minimum)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_at_exact_evening_start():
    """match() returns True exactly at 18:00 ET (boundary inclusive)."""
    clock = lambda: datetime(2026, 4, 28, 22, 0, tzinfo=UTC)  # 18:00 ET  # noqa: E731
    trigger, _ = _make_trigger(clock=clock)
    event = _make_event()
    assert await trigger.match(event) is True


@pytest.mark.asyncio
async def test_match_at_exact_evening_end():
    """match() returns True exactly at 22:30 ET (boundary inclusive)."""
    clock = lambda: datetime(2026, 4, 29, 2, 30, tzinfo=UTC)  # 22:30 ET  # noqa: E731
    trigger, _ = _make_trigger(clock=clock)
    event = _make_event()
    assert await trigger.match(event) is True


@pytest.mark.asyncio
async def test_match_no_devices_in_payload():
    """match() returns False when devices list is empty."""
    trigger, _ = _make_trigger()
    event = _make_event(devices=[])
    assert await trigger.match(event) is False


@pytest.mark.asyncio
async def test_match_caldav_none_treated_as_no_meeting():
    """When caldav=None, _has_meeting_within returns False → match can succeed."""
    trigger, _ = _make_trigger(caldav=None)
    event = _make_event()
    assert await trigger.match(event) is True


@pytest.mark.asyncio
async def test_fire_hc09_swallows_db_exception():
    """fire() must not propagate a DB exception (HC-09 fail-soft)."""
    trigger, session = _make_trigger(fired_today=False)
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    event = _make_event()
    # Should not raise
    await trigger.fire(event)


@pytest.mark.asyncio
async def test_match_meeting_outside_window_not_blocked():
    """A meeting 3h from now (outside 2h window) should NOT block match."""
    # Meeting starts 3 hours after current evening time
    meeting_start = _EVENING_UTC + timedelta(hours=3)
    meeting_start_local = meeting_start.astimezone(_ET)

    caldav = AsyncMock()
    caldav.get_events = AsyncMock(
        return_value=[
            {
                "id": "xyz",
                "title": "Late Night Call",
                "start": meeting_start_local.isoformat(),
                "end": (meeting_start_local + timedelta(hours=1)).isoformat(),
                "all_day": False,
            }
        ]
    )
    trigger, _ = _make_trigger(caldav=caldav)
    event = _make_event()
    assert await trigger.match(event) is True


@pytest.mark.asyncio
async def test_trigger_name_and_sensors():
    """Verify class-level attributes match the spec."""
    factory, _ = _make_session_factory()
    trigger = EveningWindDownTrigger(session_factory=factory)
    assert trigger.name == "evening_wind_down"
    assert trigger.subscribed_sensors == ["tailscale_presence"]
