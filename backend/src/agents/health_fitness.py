"""Health & Fitness agent — provides insights from HealthKit data.

Queries the health_data table for recent metrics (sleep, steps, workouts,
heart rate, etc.), generates a summary, uses Gemini Flash for insights
and recommendations, and provides calendar-aware gym suggestions.

Tier 1 (autonomous) — read-only, no side effects.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import structlog
from shared.constants import AutonomyClass
from sqlalchemy import select

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant for Tasin. "
    "You provide concise, actionable health and fitness insights. "
    "Be encouraging but honest. Reference specific numbers from the data."
)

# HealthKit data_type values we track
_METRIC_LABELS: dict[str, str] = {
    "steps": "Steps",
    "sleep": "Sleep",
    "heart_rate": "Resting Heart Rate",
    "active_energy": "Active Energy",
    "exercise_minutes": "Exercise Minutes",
    "stand_hours": "Stand Hours",
    "walking_distance": "Walking Distance",
    "flights_climbed": "Flights Climbed",
    "body_mass": "Weight",
    "heart_rate_variability": "HRV",
}


class HealthFitnessAgent(BaseAgent):
    """Provide health and fitness insights from HealthKit data.

    Queries recent health data, builds a structured summary, uses Gemini Flash
    for personalized insights and recommendations, and checks the calendar for
    gym scheduling suggestions.
    """

    agent_type = "health_fitness"
    autonomy_class = AutonomyClass.WRITE_SELF

    async def execute(self, context: AgentContext) -> AgentResult:
        """Provide health and fitness insights from HealthKit data."""
        from db.models import HealthData
        from db.session import get_db_session

        days = context.config.get("lookback_days", 7)
        today = date.today()
        start_date = today - timedelta(days=days)

        # 1. Query health_data table for recent data
        async with get_db_session() as session:
            result = await session.execute(
                select(
                    HealthData.data_type,
                    HealthData.recorded_date,
                    HealthData.value,
                    HealthData.unit,
                    HealthData.metadata_,
                )
                .where(HealthData.recorded_date >= start_date)
                .order_by(HealthData.recorded_date.desc(), HealthData.data_type)
            )
            rows = result.all()

        if not rows:
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text="No health data available yet. Sync your HealthKit data from the iOS app to get started.",
            )

        # 2. Generate summary: sleep quality, step count, workout frequency
        summary = self._build_summary(rows, days)

        # 3. Calendar-aware gym suggestions
        calendar_context = await self._get_calendar_context(context)
        summary["calendar_context"] = calendar_context

        # 4. Use Gemini Flash for insights and recommendations
        gemini: GeminiClient | None = context.config.get("gemini_client")
        insights = await self._generate_insights(summary, gemini)

        log.info(
            "health_fitness_completed",
            metrics_count=len(rows),
            days=days,
            has_insights=insights is not None,
        )

        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=insights or self._build_fallback_text(summary),
            content_type="card",
            cards=[
                {
                    "type": "health_summary",
                    "title": f"Health & Fitness — Last {days} Days",
                    "data": summary,
                }
            ],
            data={"summary": summary},
        )

    # ------------------------------------------------------------------
    # Summary building
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        rows: list[Any],
        days: int,
    ) -> dict[str, Any]:
        """Build a structured summary from raw health_data rows."""
        by_type: dict[str, list[dict[str, Any]]] = {}
        for data_type, recorded_date, value, unit, metadata in rows:
            by_type.setdefault(data_type, []).append(
                {
                    "date": recorded_date.isoformat(),
                    "value": float(value),
                    "unit": unit,
                    "metadata": metadata or {},
                }
            )

        summary: dict[str, Any] = {"period_days": days, "metrics": {}}

        for data_type, entries in by_type.items():
            values = [e["value"] for e in entries]
            label = _METRIC_LABELS.get(data_type, data_type)
            unit = entries[0]["unit"]

            metric: dict[str, Any] = {
                "label": label,
                "unit": unit,
                "latest": values[0],
                "count": len(values),
            }

            if len(values) > 1:
                metric["average"] = round(sum(values) / len(values), 1)
                metric["min"] = round(min(values), 1)
                metric["max"] = round(max(values), 1)
                # Trend: compare first half vs second half
                mid = len(values) // 2
                recent_avg = sum(values[:mid]) / mid if mid > 0 else values[0]
                older_avg = sum(values[mid:]) / (len(values) - mid) if len(values) - mid > 0 else values[-1]
                if older_avg > 0:
                    change_pct = ((recent_avg - older_avg) / older_avg) * 100
                    metric["trend_pct"] = round(change_pct, 1)

            summary["metrics"][data_type] = metric

        # Derived insights
        summary["sleep_quality"] = self._assess_sleep(summary["metrics"].get("sleep"))
        summary["activity_level"] = self._assess_activity(summary["metrics"])
        summary["workout_frequency"] = self._assess_workout_frequency(
            summary["metrics"].get("exercise_minutes"),
            days,
        )

        return summary

    @staticmethod
    def _assess_sleep(sleep_metric: dict[str, Any] | None) -> str:
        if not sleep_metric:
            return "no_data"
        avg = sleep_metric.get("average", sleep_metric["latest"])
        if avg >= 7.5:
            return "excellent"
        if avg >= 7:
            return "good"
        if avg >= 6:
            return "fair"
        return "poor"

    @staticmethod
    def _assess_activity(metrics: dict[str, Any]) -> str:
        steps = metrics.get("steps")
        if not steps:
            return "no_data"
        avg = steps.get("average", steps["latest"])
        if avg >= 10000:
            return "very_active"
        if avg >= 7500:
            return "active"
        if avg >= 5000:
            return "moderate"
        return "sedentary"

    @staticmethod
    def _assess_workout_frequency(
        exercise_metric: dict[str, Any] | None,
        days: int,
    ) -> dict[str, Any]:
        if not exercise_metric:
            return {"days_with_exercise": 0, "total_days": days, "frequency": "no_data"}
        count = exercise_metric["count"]
        # Count days with meaningful exercise (>15 min)
        ratio = count / days if days > 0 else 0
        if ratio >= 0.6:
            freq = "frequent"
        elif ratio >= 0.3:
            freq = "moderate"
        else:
            freq = "infrequent"
        return {"days_with_exercise": count, "total_days": days, "frequency": freq}

    # ------------------------------------------------------------------
    # Calendar-aware gym suggestions
    # ------------------------------------------------------------------

    async def _get_calendar_context(self, context: AgentContext) -> dict[str, Any]:
        """Check today's and tomorrow's calendar for gym-friendly time slots."""
        # Suppress gym suggestions when workout tracker has an active split
        try:
            from sqlalchemy import select as sa_select

            from db.models import WorkoutSplit
            from db.session import get_db_session

            async with get_db_session() as session:
                result = await session.execute(
                    sa_select(WorkoutSplit).where(WorkoutSplit.active == True).limit(1)  # noqa: E712
                )
                if result.scalar_one_or_none() is not None:
                    return {"suppressed_by_workout_tracker": True}
        except Exception:
            pass  # If check fails, fall through to normal behavior

        try:
            from config import get_settings
            from integrations.caldav_client import CalDAVClient

            settings = get_settings()
            caldav = CalDAVClient(
                username=settings.icloud_caldav_user,
                password=settings.icloud_caldav_password,
            )

            today = date.today()
            tomorrow = today + timedelta(days=1)
            today_events = await caldav.get_events(today)
            tomorrow_events = await caldav.get_events(tomorrow)

            # Check if gym/workout is already scheduled
            has_gym_today = any(_is_gym_event(e) for e in today_events)
            has_gym_tomorrow = any(_is_gym_event(e) for e in tomorrow_events)

            # Find free slots for gym (looking for 1+ hour gaps)
            today_slots = _find_free_slots(today_events) if not has_gym_today else []
            tomorrow_slots = _find_free_slots(tomorrow_events) if not has_gym_tomorrow else []

            return {
                "has_gym_today": has_gym_today,
                "has_gym_tomorrow": has_gym_tomorrow,
                "today_free_slots": today_slots,
                "tomorrow_free_slots": tomorrow_slots,
                "today_event_count": len(today_events),
                "tomorrow_event_count": len(tomorrow_events),
            }

        except Exception:
            log.warning("health_fitness_calendar_unavailable", exc_info=True)
            return {"error": "calendar_unavailable"}

    # ------------------------------------------------------------------
    # AI insights (Gemini Flash)
    # ------------------------------------------------------------------

    async def _generate_insights(
        self,
        summary: dict[str, Any],
        gemini: GeminiClient | None,
    ) -> str | None:
        """Use Gemini Flash for personalized health insights."""
        if gemini is None:
            log.warning("health_fitness_no_gemini", fallback="static_summary")
            return None

        prompt = (
            "Analyze this health and fitness data and provide a brief, personalized summary.\n"
            "Include:\n"
            "1. Key highlights (what's going well)\n"
            "2. Areas for improvement\n"
            "3. One specific, actionable recommendation\n"
        )

        cal = summary.get("calendar_context", {})
        if cal and not cal.get("error"):
            prompt += "4. A gym/exercise suggestion based on the calendar availability\n"

        prompt += (
            f"\nData:\n{json.dumps(summary, indent=2, default=str)}\n\n"
            "Keep the response under 200 words. Speak directly to Tasin."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1024,
            )
            log.info(
                "health_fitness_insights_generated",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
            )
            return response.text.strip()

        except Exception:
            log.error("health_fitness_insights_failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Fallback text (no Gemini)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fallback_text(summary: dict[str, Any]) -> str:
        """Build a simple text summary when Gemini is unavailable."""
        parts: list[str] = []
        metrics = summary.get("metrics", {})

        sleep = metrics.get("sleep")
        if sleep:
            avg = sleep.get("average", sleep["latest"])
            parts.append(f"Sleep: {avg:.1f} hrs avg ({summary['sleep_quality']})")

        steps = metrics.get("steps")
        if steps:
            avg = steps.get("average", steps["latest"])
            parts.append(f"Steps: {int(avg):,} avg/day")

        exercise = metrics.get("exercise_minutes")
        if exercise:
            avg = exercise.get("average", exercise["latest"])
            parts.append(f"Exercise: {int(avg)} min avg/day")

        wf = summary.get("workout_frequency", {})
        if wf.get("days_with_exercise"):
            parts.append(f"Workouts: {wf['days_with_exercise']}/{wf['total_days']} days")

        cal = summary.get("calendar_context", {})
        if cal.get("has_gym_today"):
            parts.append("Gym is on your calendar today.")
        elif cal.get("today_free_slots"):
            parts.append(f"Free slot today: {cal['today_free_slots'][0]} — good time for a workout.")

        return "\n".join(parts) if parts else "Health data synced. Check back for insights."


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_gym_event(event: dict[str, Any]) -> bool:
    """Check if a calendar event is gym/workout related."""
    title = str(event.get("title", "")).lower()
    keywords = ("gym", "workout", "exercise", "fitness", "run", "yoga", "lift", "swim", "crossfit")
    return any(kw in title for kw in keywords)


def _find_free_slots(events: list[dict[str, Any]]) -> list[str]:
    """Find 1+ hour free slots during typical gym hours (6am-10pm).

    Returns human-readable slot descriptions like "2:00 PM - 4:00 PM".
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    today = now.date()

    # Define gym hours: 6am to 10pm
    gym_start = datetime(today.year, today.month, today.day, 6, 0, tzinfo=tz)
    gym_end = datetime(today.year, today.month, today.day, 22, 0, tzinfo=tz)

    # Don't suggest past slots
    if now > gym_start:
        gym_start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    if gym_start >= gym_end:
        return []

    # Parse event times into busy intervals
    busy: list[tuple[datetime, datetime]] = []
    for event in events:
        if event.get("all_day"):
            continue
        start_str = event.get("start", "")
        end_str = event.get("end", "")
        if not start_str or not end_str:
            continue
        try:
            s = datetime.fromisoformat(start_str)
            e = datetime.fromisoformat(end_str)
            busy.append((s, e))
        except ValueError:
            continue

    busy.sort(key=lambda x: x[0])

    # Find gaps of 1+ hours
    slots: list[str] = []
    cursor = gym_start
    for bstart, bend in busy:
        if bstart > cursor and (bstart - cursor).total_seconds() >= 3600:
            slots.append(f"{cursor.strftime('%-I:%M %p')} - {bstart.strftime('%-I:%M %p')}")
        if bend > cursor:
            cursor = bend

    # Check gap after last event
    if (gym_end - cursor).total_seconds() >= 3600:
        slots.append(f"{cursor.strftime('%-I:%M %p')} - {gym_end.strftime('%-I:%M %p')}")

    return slots[:3]  # Return up to 3 suggestions
