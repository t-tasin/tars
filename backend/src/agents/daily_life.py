"""Daily life manager agent — scheduling, reminders, and daily task management.

Handles natural-language scheduling requests using Gemini Flash for intent
parsing, CalDAV for calendar operations, and Notion for task/list management.

Calendar event creation is a side effect and always routes through the
approval system (tier 2, HC-01).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any

import structlog

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are T.A.R.S., Tasin's personal AI assistant. "
    "Parse scheduling and daily-life requests into structured JSON. "
    "Today's date will be provided. Always resolve relative dates "
    "(e.g. 'Thursday' means the next upcoming Thursday). "
    "Use 24-hour time internally but be friendly in text."
)

# Sub-intents this agent handles.
_SCHEDULE_KEYWORDS = frozenset({
    "schedule", "book", "plan", "set up", "add to calendar",
    "calendar event", "create event", "block time", "block off",
})
_SHOW_SCHEDULE_KEYWORDS = frozenset({
    "show schedule", "my schedule", "today's schedule", "what's on",
    "calendar today", "events today", "what do i have",
    "/schedule", "upcoming events", "what's happening",
})
_REMINDER_KEYWORDS = frozenset({
    "remind me", "reminder", "don't forget", "remember to",
    "add task", "todo", "to-do", "to do",
})
_FREE_TIME_KEYWORDS = frozenset({
    "free time", "open slots", "availability", "when am i free",
    "find time", "gaps in schedule", "available time",
})
_GROCERY_KEYWORDS = frozenset({
    "grocery", "groceries", "shopping list", "add to list",
    "buy", "pick up",
})


class DailyLifeAgent(BaseAgent):
    """Scheduling, reminders, calendar queries, and daily task management.

    Sub-intents:
        - **schedule**: Create a calendar event (requires approval).
        - **show_schedule**: Show today's (or a specific day's) events.
        - **reminder**: Create a task/reminder in Notion.
        - **free_time**: Analyse calendar gaps for a date range.
        - **grocery**: Add items to the Notion grocery list.
    """

    agent_type = "daily_life"

    def __init__(self) -> None:
        from config import get_settings
        from integrations.caldav_client import CalDAVClient

        settings = get_settings()

        self._caldav = CalDAVClient(
            username=settings.icloud_caldav_user,
            password=settings.icloud_caldav_password,
        )

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def execute(self, context: AgentContext) -> AgentResult:
        """Route to the appropriate sub-handler based on the user's message."""
        gemini: GeminiClient | None = context.config.get("gemini_client")
        sub_intent = _classify_sub_intent(context.user_message)

        log.info(
            "daily_life_execute",
            sub_intent=sub_intent,
            message_preview=context.user_message[:80],
        )

        if sub_intent == "show_schedule":
            return await self._handle_show_schedule(context)
        if sub_intent == "schedule":
            return await self._handle_schedule_event(context, gemini)
        if sub_intent == "reminder":
            return await self._handle_reminder(context, gemini)
        if sub_intent == "free_time":
            return await self._handle_free_time(context)
        if sub_intent == "grocery":
            return await self._handle_grocery(context, gemini)

        # Fallback: try to parse with Gemini if available
        if gemini:
            return await self._handle_schedule_event(context, gemini)

        return AgentResult(
            success=False,
            text=(
                "I'm not sure what you'd like me to do. Try something like:\n"
                "- \"schedule gym Thursday 6pm\"\n"
                "- \"show schedule\"\n"
                "- \"remind me to call dentist\"\n"
                "- \"find free time this week\""
            ),
            error="unrecognised_sub_intent",
        )

    # ------------------------------------------------------------------
    # Sub-handlers
    # ------------------------------------------------------------------

    async def _handle_show_schedule(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """Show today's calendar events."""
        try:
            events = await self._caldav.get_today_events()
        except Exception:
            log.error("daily_life_calendar_fetch_failed", exc_info=True)
            return AgentResult(
                success=False,
                text="I couldn't reach your calendar right now. Please try again shortly.",
                error="calendar_unavailable",
            )

        if not events:
            return AgentResult(
                success=True,
                text="Your calendar is clear today — no events scheduled.",
                data={"events": []},
            )

        lines = [f"Here's your schedule for today ({date.today().strftime('%A, %B %-d')}):"]
        cards: list[dict[str, Any]] = []
        for ev in events:
            time_str = _format_event_time(ev)
            title = ev.get("title", "Untitled")
            location = ev.get("location")
            line = f"  {time_str} — {title}"
            if location:
                line += f" ({location})"
            lines.append(line)
            cards.append({
                "type": "event",
                "title": title,
                "time": time_str,
                "location": location or "",
            })

        return AgentResult(
            success=True,
            text="\n".join(lines),
            content_type="card",
            cards=cards,
            data={"events": events, "count": len(events)},
        )

    async def _handle_schedule_event(
        self,
        context: AgentContext,
        gemini: GeminiClient | None,
    ) -> AgentResult:
        """Parse a scheduling request and return an approval-required result."""
        parsed = await self._parse_schedule_request(context.user_message, gemini)

        if not parsed:
            return AgentResult(
                success=False,
                text="I couldn't understand that scheduling request. Try something like \"schedule gym Thursday 6pm\".",
                error="parse_failed",
            )

        title = parsed.get("title", "")
        start_str = parsed.get("start", "")
        end_str = parsed.get("end", "")
        location = parsed.get("location")
        description = parsed.get("description")

        if not title or not start_str:
            return AgentResult(
                success=False,
                text="I need at least an event title and time. Try: \"schedule gym Thursday 6pm\".",
                error="incomplete_request",
            )

        # Build a human-readable preview
        try:
            start_dt = datetime.fromisoformat(start_str)
            end_dt = datetime.fromisoformat(end_str) if end_str else start_dt + timedelta(hours=1)
        except ValueError:
            return AgentResult(
                success=False,
                text=f"I had trouble parsing the date/time \"{start_str}\". Could you rephrase?",
                error="invalid_datetime",
            )

        preview_time = _format_preview_time(start_dt, end_dt)
        preview_text = f"{title} — {preview_time}"
        if location:
            preview_text += f" at {location}"

        log.info(
            "daily_life_schedule_parsed",
            title=title,
            start=start_str,
            end=end_str,
            location=location,
        )

        return AgentResult(
            success=True,
            text=f"I'll create this event for you:\n  {preview_text}\n\nPlease approve to add it to your calendar.",
            content_type="approval",
            has_side_effects=True,
            action_type="create_event",
            approval_title=f"Create event: {title}",
            preview={
                "title": title,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "location": location,
                "description": description,
                "display": preview_text,
            },
            data={"parsed": parsed},
        )

    async def _handle_reminder(
        self,
        context: AgentContext,
        gemini: GeminiClient | None,
    ) -> AgentResult:
        """Parse a reminder request and create a Notion task (requires approval)."""
        parsed = await self._parse_reminder_request(context.user_message, gemini)

        if not parsed or not parsed.get("task"):
            return AgentResult(
                success=False,
                text="I couldn't understand that reminder. Try: \"remind me to call the dentist tomorrow\".",
                error="parse_failed",
            )

        task_title = parsed["task"]
        due_date = parsed.get("due_date")
        priority = parsed.get("priority", "normal")

        preview_text = task_title
        if due_date:
            preview_text += f" (due: {due_date})"

        log.info(
            "daily_life_reminder_parsed",
            task=task_title,
            due_date=due_date,
            priority=priority,
        )

        return AgentResult(
            success=True,
            text=f"I'll create a reminder:\n  {preview_text}\n\nPlease approve to add it to your tasks.",
            content_type="approval",
            has_side_effects=True,
            action_type="create_notion",
            approval_title=f"Create reminder: {task_title}",
            preview={
                "task": task_title,
                "due_date": due_date,
                "priority": priority,
                "display": preview_text,
            },
        )

    async def _handle_free_time(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """Analyse calendar gaps for the next 7 days."""
        today = date.today()
        end = today + timedelta(days=7)

        try:
            events = await self._caldav.get_events(today, end)
        except Exception:
            log.error("daily_life_free_time_fetch_failed", exc_info=True)
            return AgentResult(
                success=False,
                text="I couldn't reach your calendar right now. Please try again shortly.",
                error="calendar_unavailable",
            )

        gaps = _find_free_slots(events, today, days=7)

        if not gaps:
            return AgentResult(
                success=True,
                text="Your week looks packed — I couldn't find any significant gaps. Consider rescheduling something if you need free time.",
                data={"gaps": []},
            )

        lines = ["Here are your free slots this week:"]
        for gap in gaps[:10]:
            lines.append(f"  {gap['day']} — {gap['start']} to {gap['end']} ({gap['duration']})")

        return AgentResult(
            success=True,
            text="\n".join(lines),
            data={"gaps": gaps},
        )

    async def _handle_grocery(
        self,
        context: AgentContext,
        gemini: GeminiClient | None,
    ) -> AgentResult:
        """Parse grocery items and add to Notion grocery list (requires approval)."""
        items = await self._parse_grocery_items(context.user_message, gemini)

        if not items:
            return AgentResult(
                success=False,
                text="I couldn't identify any items to add. Try: \"add milk and eggs to grocery list\".",
                error="parse_failed",
            )

        items_display = ", ".join(items)

        log.info("daily_life_grocery_parsed", items=items, count=len(items))

        return AgentResult(
            success=True,
            text=f"I'll add to your grocery list:\n  {items_display}\n\nPlease approve.",
            content_type="approval",
            has_side_effects=True,
            action_type="create_notion",
            approval_title=f"Add {len(items)} item{'s' if len(items) != 1 else ''} to grocery list",
            preview={
                "items": items,
                "list": "grocery",
                "display": items_display,
            },
        )

    # ------------------------------------------------------------------
    # Gemini-powered parsing
    # ------------------------------------------------------------------

    async def _parse_schedule_request(
        self,
        message: str,
        gemini: GeminiClient | None,
    ) -> dict[str, Any] | None:
        """Use Gemini Flash to parse a natural-language scheduling request."""
        if gemini is None:
            log.warning("daily_life_no_gemini", fallback="basic_parse")
            return _basic_schedule_parse(message)

        today = date.today()
        prompt = (
            f"Today is {today.strftime('%A, %B %-d, %Y')} (ISO: {today.isoformat()}).\n"
            f"Parse this scheduling request into JSON:\n\"{message}\"\n\n"
            "Return a JSON object with these fields:\n"
            "- title: string (event name)\n"
            "- start: string (ISO 8601 datetime with timezone offset, e.g. 2025-03-13T18:00:00-04:00)\n"
            "- end: string (ISO 8601 datetime, default to 1 hour after start if not specified)\n"
            "- location: string or null\n"
            "- description: string or null\n\n"
            "Resolve relative days: 'Thursday' means the NEXT upcoming Thursday. "
            "Use America/New_York timezone. Return ONLY valid JSON."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=512,
                response_format="json",
            )
            parsed = json.loads(response.text)
            log.info(
                "daily_life_schedule_gemini_parsed",
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )
            return parsed
        except (json.JSONDecodeError, Exception):
            log.error("daily_life_schedule_parse_failed", exc_info=True)
            return _basic_schedule_parse(message)

    async def _parse_reminder_request(
        self,
        message: str,
        gemini: GeminiClient | None,
    ) -> dict[str, Any] | None:
        """Use Gemini Flash to parse a reminder/task request."""
        if gemini is None:
            log.warning("daily_life_no_gemini_reminder", fallback="basic_parse")
            return _basic_reminder_parse(message)

        today = date.today()
        prompt = (
            f"Today is {today.strftime('%A, %B %-d, %Y')} (ISO: {today.isoformat()}).\n"
            f"Parse this reminder/task request into JSON:\n\"{message}\"\n\n"
            "Return a JSON object with:\n"
            "- task: string (what to remember/do)\n"
            "- due_date: string (ISO date, or null if no date mentioned)\n"
            "- priority: string (\"high\", \"normal\", or \"low\")\n\n"
            "Return ONLY valid JSON."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=256,
                response_format="json",
            )
            parsed = json.loads(response.text)
            log.info(
                "daily_life_reminder_gemini_parsed",
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
            )
            return parsed
        except (json.JSONDecodeError, Exception):
            log.error("daily_life_reminder_parse_failed", exc_info=True)
            return _basic_reminder_parse(message)

    async def _parse_grocery_items(
        self,
        message: str,
        gemini: GeminiClient | None,
    ) -> list[str]:
        """Use Gemini Flash to extract grocery items from a message."""
        if gemini is None:
            return _basic_grocery_parse(message)

        prompt = (
            f"Extract grocery/shopping items from this message:\n\"{message}\"\n\n"
            "Return a JSON array of item strings. Example: [\"milk\", \"eggs\", \"bread\"]\n"
            "Return ONLY a JSON array."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=256,
                response_format="json",
            )
            items = json.loads(response.text)
            if isinstance(items, list):
                return [str(i) for i in items if i]
        except (json.JSONDecodeError, Exception):
            log.error("daily_life_grocery_parse_failed", exc_info=True)

        return _basic_grocery_parse(message)

    # ------------------------------------------------------------------
    # Execution after approval
    # ------------------------------------------------------------------

    async def execute_approved_action(
        self,
        action_type: str,
        preview: dict[str, Any],
    ) -> AgentResult:
        """Execute a previously approved action.

        Called by the orchestrator after the user approves a side-effect action.
        """
        if action_type == "create_event":
            return await self._execute_create_event(preview)

        if action_type == "create_notion":
            return await self._execute_create_notion(preview)

        return AgentResult(
            success=False,
            text=f"Unknown action type: {action_type}",
            error="unknown_action",
        )

    async def _execute_create_event(
        self,
        preview: dict[str, Any],
    ) -> AgentResult:
        """Create a calendar event after approval."""
        title = preview["title"]
        start = datetime.fromisoformat(preview["start"])
        end = datetime.fromisoformat(preview["end"])
        location = preview.get("location")
        description = preview.get("description")

        try:
            event = await self._caldav.create_event(
                title=title,
                start=start,
                end=end,
                location=location,
                description=description,
            )
            log.info(
                "daily_life_event_created",
                title=title,
                start=preview["start"],
            )
            return AgentResult(
                success=True,
                text=f"Done! \"{title}\" has been added to your calendar.",
                data={"event": event},
            )
        except Exception:
            log.error("daily_life_event_create_failed", exc_info=True)
            return AgentResult(
                success=False,
                text=f"I couldn't create the event \"{title}\". Please try again.",
                error="event_creation_failed",
            )

    async def _execute_create_notion(
        self,
        preview: dict[str, Any],
    ) -> AgentResult:
        """Create a Notion task or grocery list entry after approval.

        Stub until NotionClient is implemented — stores intent for later execution.
        """
        # TODO: Replace with actual NotionClient calls when available.
        list_type = preview.get("list")

        if list_type == "grocery":
            items = preview.get("items", [])
            log.info("daily_life_grocery_stub", items=items)
            return AgentResult(
                success=True,
                text=f"Added {len(items)} item{'s' if len(items) != 1 else ''} to your grocery list.",
                data={"items": items, "stub": True},
            )

        task = preview.get("task", "")
        due_date = preview.get("due_date")
        log.info("daily_life_reminder_stub", task=task, due_date=due_date)
        return AgentResult(
            success=True,
            text=f"Reminder set: {task}",
            data={"task": task, "due_date": due_date, "stub": True},
        )


# ---------------------------------------------------------------------------
# Sub-intent classification (zero AI tokens)
# ---------------------------------------------------------------------------


def _classify_sub_intent(message: str) -> str:
    """Classify the user message into a daily-life sub-intent.

    Uses keyword matching — zero AI tokens.
    """
    msg_lower = message.lower().strip()

    # Check show_schedule first (more specific than schedule)
    for kw in _SHOW_SCHEDULE_KEYWORDS:
        if kw in msg_lower:
            return "show_schedule"

    for kw in _REMINDER_KEYWORDS:
        if kw in msg_lower:
            return "reminder"

    for kw in _FREE_TIME_KEYWORDS:
        if kw in msg_lower:
            return "free_time"

    for kw in _GROCERY_KEYWORDS:
        if kw in msg_lower:
            return "grocery"

    for kw in _SCHEDULE_KEYWORDS:
        if kw in msg_lower:
            return "schedule"

    return "unknown"


# ---------------------------------------------------------------------------
# Fallback parsers (no AI, basic heuristics)
# ---------------------------------------------------------------------------


def _basic_schedule_parse(message: str) -> dict[str, Any] | None:
    """Best-effort schedule parsing without AI."""
    # Very basic — just extract what we can
    import re

    title_match = re.search(
        r"(?:schedule|book|plan)\s+(.+?)(?:\s+(?:on|at|for|this|next)\s)",
        message,
        re.IGNORECASE,
    )
    title = title_match.group(1).strip() if title_match else ""

    if not title:
        # Try: everything after "schedule" up to a time/date word
        title_match = re.search(
            r"(?:schedule|book|plan)\s+(.+)",
            message,
            re.IGNORECASE,
        )
        title = title_match.group(1).strip() if title_match else message.strip()

    return {"title": title} if title else None


def _basic_reminder_parse(message: str) -> dict[str, Any] | None:
    """Best-effort reminder parsing without AI."""
    import re

    task_match = re.search(
        r"(?:remind me to|reminder|remember to|don't forget to?)\s+(.+)",
        message,
        re.IGNORECASE,
    )
    task = task_match.group(1).strip() if task_match else ""

    if not task:
        # Try: everything after "add task"
        task_match = re.search(r"(?:add task|todo|to-do)\s*:?\s*(.+)", message, re.IGNORECASE)
        task = task_match.group(1).strip() if task_match else ""

    return {"task": task} if task else None


def _basic_grocery_parse(message: str) -> list[str]:
    """Best-effort grocery item extraction without AI."""
    import re

    # Remove common prefixes
    cleaned = re.sub(
        r"(?:add|buy|pick up|get)\s+",
        "",
        message,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(?:to (?:my )?(?:grocery|shopping) list)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Split on "and", commas, or "&"
    items = re.split(r"\s*(?:,|\band\b|&)\s*", cleaned.strip())
    return [item.strip() for item in items if item.strip() and len(item.strip()) > 1]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_event_time(event: dict[str, Any]) -> str:
    """Format an event's time for display."""
    if event.get("all_day"):
        return "All day"

    start = event.get("start", "")
    end = event.get("end", "")

    try:
        start_dt = datetime.fromisoformat(start)
        start_str = start_dt.strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return str(start)

    try:
        end_dt = datetime.fromisoformat(end)
        end_str = end_dt.strftime("%-I:%M %p")
        return f"{start_str} – {end_str}"
    except (ValueError, TypeError):
        return start_str


def _format_preview_time(start: datetime, end: datetime) -> str:
    """Format start/end times for the approval preview."""
    day = start.strftime("%A, %B %-d")
    start_str = start.strftime("%-I:%M %p")
    end_str = end.strftime("%-I:%M %p")
    return f"{day}, {start_str} – {end_str}"


def _find_free_slots(
    events: list[dict[str, Any]],
    start_date: date,
    days: int = 7,
    min_gap_minutes: int = 60,
    day_start_hour: int = 8,
    day_end_hour: int = 22,
) -> list[dict[str, str]]:
    """Find free time slots across a date range.

    Only considers gaps within waking hours (8am–10pm) that are at
    least ``min_gap_minutes`` long.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    gaps: list[dict[str, str]] = []

    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        day_start = datetime.combine(current_date, dt_time(day_start_hour, 0), tzinfo=tz)
        day_end = datetime.combine(current_date, dt_time(day_end_hour, 0), tzinfo=tz)

        # Collect events for this day
        day_events: list[tuple[datetime, datetime]] = []
        for ev in events:
            if ev.get("all_day"):
                continue
            try:
                ev_start = datetime.fromisoformat(ev["start"])
                ev_end = datetime.fromisoformat(ev["end"])
                # Check if event overlaps this day
                if ev_start < day_end and ev_end > day_start:
                    day_events.append((
                        max(ev_start, day_start),
                        min(ev_end, day_end),
                    ))
            except (ValueError, KeyError, TypeError):
                continue

        day_events.sort()

        # Walk through the day finding gaps
        cursor = day_start
        for ev_start, ev_end in day_events:
            if ev_start > cursor:
                gap_minutes = (ev_start - cursor).total_seconds() / 60
                if gap_minutes >= min_gap_minutes:
                    gaps.append({
                        "day": current_date.strftime("%A, %B %-d"),
                        "start": cursor.strftime("%-I:%M %p"),
                        "end": ev_start.strftime("%-I:%M %p"),
                        "duration": _format_duration(int(gap_minutes)),
                    })
            cursor = max(cursor, ev_end)

        # Gap after last event
        if cursor < day_end:
            gap_minutes = (day_end - cursor).total_seconds() / 60
            if gap_minutes >= min_gap_minutes:
                gaps.append({
                    "day": current_date.strftime("%A, %B %-d"),
                    "start": cursor.strftime("%-I:%M %p"),
                    "end": day_end.strftime("%-I:%M %p"),
                    "duration": _format_duration(int(gap_minutes)),
                })

    return gaps


def _format_duration(minutes: int) -> str:
    """Format a duration in minutes as a human-readable string."""
    if minutes < 60:
        return f"{minutes}min"
    hours = minutes // 60
    remaining = minutes % 60
    if remaining == 0:
        return f"{hours}h"
    return f"{hours}h {remaining}min"
