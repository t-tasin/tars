"""iCloud CalDAV client for reading and creating calendar events."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import caldav
import structlog
from zoneinfo import ZoneInfo

from utils.resilience import get_service_health_registry

log = structlog.get_logger()

LOCAL_TZ = ZoneInfo("America/New_York")
_SERVICE_NAME = "caldav"


class CalDAVClient:
    """iCloud CalDAV client. All sync caldav calls wrapped in asyncio.to_thread().

    Includes circuit breaker integration for health reporting.
    """

    ICLOUD_CALDAV_URL = "https://caldav.icloud.com"

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client = caldav.DAVClient(
            url=self.ICLOUD_CALDAV_URL,
            username=username,
            password=password,
        )
        self._cb = get_service_health_registry().get_or_create(_SERVICE_NAME)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_events(
        self,
        start_date: date,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch events for a date range, sorted by start time.

        Args:
            start_date: First day to include.
            end_date: Last day (exclusive). Defaults to ``start_date + 1 day``.

        Returns:
            List of event dicts with normalised fields.
        """
        if end_date is None:
            end_date = start_date + timedelta(days=1)

        events: list[dict[str, Any]] = []
        calendars = await asyncio.to_thread(self._get_calendars_sync)

        for cal in calendars:
            raw_events = await asyncio.to_thread(
                self._search_events_sync, cal, start_date, end_date,
            )
            cal_name = str(cal.name) if cal.name else "Unknown"
            for ev in raw_events:
                parsed = _parse_event(ev, cal_name)
                if parsed:
                    events.append(parsed)

        events.sort(key=lambda e: e["start"])
        self._cb.record_success()
        log.info("caldav_events_fetched", count=len(events), start=str(start_date), end=str(end_date))
        return events

    async def get_today_events(self) -> list[dict[str, Any]]:
        """Convenience: get today's events, sorted by start time."""
        today = date.today()
        return await self.get_events(today)

    async def get_calendars(self) -> list[dict[str, Any]]:
        """List available calendars."""
        calendars = await asyncio.to_thread(self._get_calendars_sync)
        return [
            {
                "name": str(cal.name) if cal.name else "Unknown",
                "id": str(cal.id) if cal.id else str(cal.url),
            }
            for cal in calendars
        ]

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        location: str | None = None,
        calendar_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new calendar event.

        NOTE: This is a side-effect action — the caller must route
        through the approval queue before invoking this method.
        """
        cal = await self._resolve_calendar(calendar_name)

        kwargs: dict[str, Any] = {
            "dtstart": start,
            "dtend": end,
            "summary": title,
        }
        if location:
            kwargs["location"] = location
        if description:
            kwargs["description"] = description

        event = await asyncio.to_thread(cal.save_event, **kwargs)
        parsed = _parse_event(event, str(cal.name) if cal.name else "Unknown")

        log.info("caldav_event_created", title=title, calendar=cal.name)
        return parsed or {"title": title, "start": start.isoformat(), "end": end.isoformat()}

    # ------------------------------------------------------------------
    # Sync helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    def _get_calendars_sync(self) -> list[caldav.Calendar]:
        principal = self._client.principal()
        return principal.calendars()

    @staticmethod
    def _search_events_sync(
        cal: caldav.Calendar,
        start_date: date,
        end_date: date,
    ) -> list[caldav.Event]:
        return cal.search(
            start=start_date,
            end=end_date,
            event=True,
            expand=True,
        )

    async def _resolve_calendar(self, calendar_name: str | None) -> caldav.Calendar:
        """Find a calendar by name, or return the first one."""
        calendars = await asyncio.to_thread(self._get_calendars_sync)
        if not calendars:
            raise RuntimeError("No calendars found on this CalDAV account")

        if calendar_name:
            for cal in calendars:
                if str(cal.name).lower() == calendar_name.lower():
                    return cal
            log.warning("caldav_calendar_not_found", requested=calendar_name, using=calendars[0].name)

        return calendars[0]


# ----------------------------------------------------------------------
# iCalendar parsing helpers
# ----------------------------------------------------------------------


def _parse_event(event: caldav.Event, calendar_name: str) -> dict[str, Any] | None:
    """Extract a clean dict from a caldav Event object."""
    try:
        comp = event.icalendar_component
    except Exception:
        try:
            instance = event.vobject_instance
            comp = None
            vevent = instance.vevent
        except Exception:
            log.warning("caldav_parse_failed", data=str(event.data)[:200])
            return None
    else:
        vevent = None

    if comp is not None:
        return _parse_from_icalendar(comp, calendar_name)
    if vevent is not None:
        return _parse_from_vobject(vevent, calendar_name)
    return None


def _parse_from_icalendar(comp: Any, calendar_name: str) -> dict[str, Any]:
    """Parse from icalendar component (comp is a VEVENT)."""
    uid = str(comp.get("uid", ""))
    summary = str(comp.get("summary", ""))
    location = str(comp.get("location", "")) or None
    description = str(comp.get("description", "")) or None

    dtstart_val = comp.get("dtstart")
    dtend_val = comp.get("dtend")

    start_dt = dtstart_val.dt if dtstart_val else None
    end_dt = dtend_val.dt if dtend_val else None

    all_day = isinstance(start_dt, date) and not isinstance(start_dt, datetime)

    start_str = _normalise_dt(start_dt)
    end_str = _normalise_dt(end_dt)

    return {
        "id": uid,
        "title": summary,
        "start": start_str,
        "end": end_str,
        "location": location,
        "calendar": calendar_name,
        "all_day": all_day,
        "description": description,
    }


def _parse_from_vobject(vevent: Any, calendar_name: str) -> dict[str, Any]:
    """Fallback parser using vobject VEVENT."""
    uid = str(getattr(vevent, "uid", None) and vevent.uid.value or "")
    summary = str(getattr(vevent, "summary", None) and vevent.summary.value or "")
    location_attr = getattr(vevent, "location", None)
    location = str(location_attr.value) if location_attr else None
    desc_attr = getattr(vevent, "description", None)
    description = str(desc_attr.value) if desc_attr else None

    start_dt = vevent.dtstart.value if hasattr(vevent, "dtstart") else None
    end_dt = vevent.dtend.value if hasattr(vevent, "dtend") else None

    all_day = isinstance(start_dt, date) and not isinstance(start_dt, datetime)

    return {
        "id": uid,
        "title": summary,
        "start": _normalise_dt(start_dt),
        "end": _normalise_dt(end_dt),
        "location": location,
        "calendar": calendar_name,
        "all_day": all_day,
        "description": description,
    }


def _normalise_dt(dt: date | datetime | None) -> str:
    """Convert a date/datetime to an ISO string in the local timezone."""
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ).isoformat()
    # All-day events: return date string only
    return dt.isoformat()
