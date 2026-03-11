"""Tests for the iCloud CalDAV client with mocked caldav library."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from integrations.caldav_client import CalDAVClient, _normalise_dt, _parse_event

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers to build mock caldav objects
# ---------------------------------------------------------------------------


def _make_mock_event(
    uid: str = "test-uid-1",
    summary: str = "Team Meeting",
    dtstart: datetime | date | None = None,
    dtend: datetime | date | None = None,
    location: str | None = "Conference Room",
    description: str | None = None,
) -> MagicMock:
    """Build a mock caldav.Event whose icalendar_component returns sane data."""
    if dtstart is None:
        dtstart = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
    if dtend is None:
        dtend = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)

    # icalendar component mock — comp["field"] returns a mock with .dt
    dtstart_prop = MagicMock()
    dtstart_prop.dt = dtstart
    dtend_prop = MagicMock()
    dtend_prop.dt = dtend

    comp = MagicMock()
    _comp_data: dict[str, Any] = {
        "uid": uid,
        "summary": summary,
        "location": location or "",
        "description": description or "",
        "dtstart": dtstart_prop,
        "dtend": dtend_prop,
    }
    comp.get = lambda key, default="": _comp_data.get(key, default)

    event = MagicMock()
    event.icalendar_component = comp
    event.data = f"BEGIN:VCALENDAR\nSUMMARY:{summary}\nEND:VCALENDAR"
    return event


def _make_mock_calendar(name: str = "Work", events: list | None = None) -> MagicMock:
    cal = MagicMock()
    cal.name = name
    cal.id = f"cal-{name.lower()}"
    cal.url = f"https://caldav.icloud.com/{name.lower()}"
    cal.search.return_value = events or []
    cal.save_event.return_value = _make_mock_event(uid="new-event-1", summary="New Event")
    return cal


def _make_mock_principal(calendars: list | None = None) -> MagicMock:
    principal = MagicMock()
    principal.calendars.return_value = calendars or []
    return principal


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


class TestParseEvent:
    def test_parses_basic_event(self) -> None:
        ev = _make_mock_event(
            uid="abc-123",
            summary="Standup",
            dtstart=datetime(2026, 3, 10, 13, 0, tzinfo=UTC),
            dtend=datetime(2026, 3, 10, 13, 30, tzinfo=UTC),
            location="Zoom",
        )
        result = _parse_event(ev, "Work")
        assert result is not None
        assert result["id"] == "abc-123"
        assert result["title"] == "Standup"
        assert result["location"] == "Zoom"
        assert result["calendar"] == "Work"
        assert result["all_day"] is False

    def test_parses_all_day_event(self) -> None:
        ev = _make_mock_event(
            dtstart=date(2026, 3, 10),
            dtend=date(2026, 3, 11),
        )
        result = _parse_event(ev, "Personal")
        assert result is not None
        assert result["all_day"] is True
        assert result["start"] == "2026-03-10"

    def test_none_location_becomes_none(self) -> None:
        ev = _make_mock_event(location=None)
        result = _parse_event(ev, "Cal")
        assert result is not None
        assert result["location"] is None

    def test_returns_none_on_unparseable(self) -> None:
        ev = MagicMock(spec=[])  # empty spec — no attributes auto-created
        ev.data = "garbage"
        # Both access paths raise
        type(ev).icalendar_component = property(
            lambda self: (_ for _ in ()).throw(AttributeError("no ical")),
        )
        type(ev).vobject_instance = property(
            lambda self: (_ for _ in ()).throw(AttributeError("no vobj")),
        )
        result = _parse_event(ev, "X")
        assert result is None


# ---------------------------------------------------------------------------
# Timezone normalisation
# ---------------------------------------------------------------------------


class TestNormaliseDt:
    def test_utc_to_eastern(self) -> None:
        dt = datetime(2026, 3, 10, 18, 0, tzinfo=UTC)
        result = _normalise_dt(dt)
        assert "2026-03-10T14:00:00" in result
        assert "-04:00" in result  # EDT in March 2026

    def test_naive_datetime_gets_local_tz(self) -> None:
        dt = datetime(2026, 3, 10, 10, 0)
        result = _normalise_dt(dt)
        assert "2026-03-10T10:00:00" in result

    def test_date_returns_date_string(self) -> None:
        d = date(2026, 3, 10)
        assert _normalise_dt(d) == "2026-03-10"

    def test_none_returns_empty(self) -> None:
        assert _normalise_dt(None) == ""


# ---------------------------------------------------------------------------
# CalDAVClient — get_events
# ---------------------------------------------------------------------------


class TestGetEvents:
    @pytest.mark.asyncio
    async def test_fetches_and_sorts_events(self) -> None:
        later = _make_mock_event(
            uid="ev2", summary="Afternoon",
            dtstart=datetime(2026, 3, 10, 18, 0, tzinfo=UTC),
            dtend=datetime(2026, 3, 10, 19, 0, tzinfo=UTC),
        )
        earlier = _make_mock_event(
            uid="ev1", summary="Morning",
            dtstart=datetime(2026, 3, 10, 13, 0, tzinfo=UTC),
            dtend=datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
        )
        cal = _make_mock_calendar("Work", events=[later, earlier])
        principal = _make_mock_principal(calendars=[cal])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            events = await client.get_events(date(2026, 3, 10))

        assert len(events) == 2
        assert events[0]["title"] == "Morning"
        assert events[1]["title"] == "Afternoon"

    @pytest.mark.asyncio
    async def test_defaults_end_date_to_next_day(self) -> None:
        cal = _make_mock_calendar("Work", events=[])
        principal = _make_mock_principal(calendars=[cal])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            await client.get_events(date(2026, 3, 10))

        cal.search.assert_called_once_with(
            start=date(2026, 3, 10),
            end=date(2026, 3, 11),
            event=True,
            expand=True,
        )

    @pytest.mark.asyncio
    async def test_merges_multiple_calendars(self) -> None:
        ev1 = _make_mock_event(uid="w1", summary="Work Event")
        ev2 = _make_mock_event(uid="p1", summary="Personal Event")
        work_cal = _make_mock_calendar("Work", events=[ev1])
        personal_cal = _make_mock_calendar("Personal", events=[ev2])
        principal = _make_mock_principal(calendars=[work_cal, personal_cal])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            events = await client.get_events(date(2026, 3, 10))

        assert len(events) == 2
        calendars = {e["calendar"] for e in events}
        assert calendars == {"Work", "Personal"}

    @pytest.mark.asyncio
    async def test_empty_calendars_returns_empty(self) -> None:
        principal = _make_mock_principal(calendars=[])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            events = await client.get_events(date(2026, 3, 10))

        assert events == []


# ---------------------------------------------------------------------------
# CalDAVClient — get_calendars
# ---------------------------------------------------------------------------


class TestGetCalendars:
    @pytest.mark.asyncio
    async def test_lists_calendars(self) -> None:
        cal1 = _make_mock_calendar("Work")
        cal2 = _make_mock_calendar("Personal")
        principal = _make_mock_principal(calendars=[cal1, cal2])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            cals = await client.get_calendars()

        assert len(cals) == 2
        assert cals[0]["name"] == "Work"
        assert cals[1]["name"] == "Personal"


# ---------------------------------------------------------------------------
# CalDAVClient — create_event
# ---------------------------------------------------------------------------


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_creates_event_on_named_calendar(self) -> None:
        work_cal = _make_mock_calendar("Work")
        personal_cal = _make_mock_calendar("Personal")
        principal = _make_mock_principal(calendars=[work_cal, personal_cal])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            result = await client.create_event(
                title="New Meeting",
                start=datetime(2026, 3, 11, 10, 0, tzinfo=ET),
                end=datetime(2026, 3, 11, 11, 0, tzinfo=ET),
                location="Office",
                calendar_name="Work",
            )

        work_cal.save_event.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_falls_back_to_first_calendar(self) -> None:
        cal = _make_mock_calendar("Default")
        principal = _make_mock_principal(calendars=[cal])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            result = await client.create_event(
                title="Quick Event",
                start=datetime(2026, 3, 11, 9, 0),
                end=datetime(2026, 3, 11, 9, 30),
            )

        cal.save_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_no_calendars(self) -> None:
        principal = _make_mock_principal(calendars=[])

        client = CalDAVClient("user@icloud.com", "app-password")
        with patch.object(client._client, "principal", return_value=principal):
            with pytest.raises(RuntimeError, match="No calendars found"):
                await client.create_event(
                    title="Fail",
                    start=datetime(2026, 3, 11, 9, 0),
                    end=datetime(2026, 3, 11, 9, 30),
                )
