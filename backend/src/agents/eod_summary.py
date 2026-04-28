"""End-of-day summary agent — gathers today's activity, composes narrative via Gemini Pro.

Collects: tasks completed, emails classified, approvals processed,
tomorrow's calendar, AI usage, spending, health data, and pending items.
Stores the result as a briefing with type=end_of_day.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import structlog
from shared.constants import ApprovalStatus, AutonomyClass, TaskStatus
from sqlalchemy import func, select

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

_NARRATIVE_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant for Tasin. "
    "You are warm, concise, and encouraging. You speak directly to Tasin in second person."
)

_FALLBACK_NARRATIVE = (
    "Good evening, Tasin. I had trouble composing your end-of-day summary, "
    "but your data is ready for review. Rest well!"
)


class EODSummaryAgent(BaseAgent):
    """End-of-day summary agent — gathers today's activity data and composes a summary."""

    agent_type = "eod_summary"
    autonomy_class = AutonomyClass.WRITE_LOCAL

    async def execute(self, context: AgentContext) -> AgentResult:
        """Generate end-of-day summary.

        1. Gather today's activity data from DB.
        2. Fetch tomorrow's calendar events.
        3. Compose narrative via Gemini Pro.
        4. Store as briefing (type=end_of_day).
        """
        raw_data = await self._gather_today_data(context)

        gemini: GeminiClient | None = context.config.get("gemini_client")
        narrative = await self._compose_narrative(raw_data, gemini)

        briefing_id = await self._store_briefing(raw_data, narrative)

        log.info(
            "eod_summary_generated",
            briefing_id=str(briefing_id),
            narrative_len=len(narrative),
        )

        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=narrative,
            content_type="briefing",
            data={
                "briefing_id": str(briefing_id),
                "payload": raw_data,
                "type": "end_of_day",
            },
        )

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    async def _gather_today_data(self, context: AgentContext) -> dict[str, Any]:
        """Gather all end-of-day data sources in parallel."""
        results = await asyncio.gather(
            self._fetch_tasks_completed(),
            self._fetch_emails_classified(),
            self._fetch_approvals_processed(),
            self._fetch_tomorrow_calendar(context),
            self._fetch_ai_usage(),
            self._fetch_spending(),
            self._fetch_health_data(),
            self._fetch_pending_items(),
            return_exceptions=True,
        )

        keys = [
            "tasks_completed",
            "emails_classified",
            "approvals_processed",
            "tomorrow_calendar",
            "ai_usage",
            "spending",
            "health",
            "pending_items",
        ]
        defaults: dict[str, Any] = {
            "tasks_completed": [],
            "emails_classified": {"total": 0, "by_tier": {}},
            "approvals_processed": {"approved": 0, "rejected": 0, "total": 0},
            "tomorrow_calendar": [],
            "ai_usage": {},
            "spending": {"total": 0, "transactions": []},
            "health": {},
            "pending_items": {"pending_approvals": 0, "open_tasks": 0},
        }

        payload: dict[str, Any] = {"date": date.today().isoformat(), "type": "end_of_day"}
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                log.warning("eod_fetch_failed", source=key, error=str(result))
                payload[key] = defaults[key]
            else:
                payload[key] = result

        # Compute suggested alarm time from tomorrow's first event
        payload["suggested_alarm"] = self._compute_alarm_time(payload.get("tomorrow_calendar", []))

        return payload

    async def _fetch_tasks_completed(self) -> list[dict[str, Any]]:
        """Fetch tasks completed today from agent_tasks."""
        from db.models import AgentTask
        from db.session import get_db_session

        today_start, today_end = _today_range()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    AgentTask.agent_type,
                    AgentTask.status,
                    AgentTask.completed_at,
                    AgentTask.duration_ms,
                )
                .where(
                    AgentTask.completed_at >= today_start,
                    AgentTask.completed_at < today_end,
                    AgentTask.status == TaskStatus.COMPLETED,
                )
                .order_by(AgentTask.completed_at)
            )
            rows = result.all()

        return [
            {
                "agent_type": row.agent_type,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "duration_ms": row.duration_ms,
            }
            for row in rows
        ]

    async def _fetch_emails_classified(self) -> dict[str, Any]:
        """Fetch email classifications from today."""
        from db.models import EmailClassification
        from db.session import get_db_session

        today_start, today_end = _today_range()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    EmailClassification.classified_tier,
                    func.count().label("count"),
                )
                .where(
                    EmailClassification.created_at >= today_start,
                    EmailClassification.created_at < today_end,
                )
                .group_by(EmailClassification.classified_tier)
            )
            rows = result.all()

        by_tier = {str(row.classified_tier): row.count for row in rows}
        total = sum(by_tier.values())

        return {"total": total, "by_tier": by_tier}

    async def _fetch_approvals_processed(self) -> dict[str, int]:
        """Fetch approval decisions made today."""
        from db.models import Approval
        from db.session import get_db_session

        today_start, today_end = _today_range()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    Approval.status,
                    func.count().label("count"),
                )
                .where(
                    Approval.decided_at >= today_start,
                    Approval.decided_at < today_end,
                )
                .group_by(Approval.status)
            )
            rows = result.all()

        approved = 0
        rejected = 0
        for row in rows:
            if row.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED, ApprovalStatus.EDITED):
                approved += row.count
            elif row.status == ApprovalStatus.REJECTED:
                rejected += row.count

        return {"approved": approved, "rejected": rejected, "total": approved + rejected}

    async def _fetch_tomorrow_calendar(self, context: AgentContext) -> list[dict[str, Any]]:
        """Fetch tomorrow's calendar events."""
        from config import get_settings
        from integrations.caldav_client import CalDAVClient

        settings = get_settings()
        caldav = CalDAVClient(
            username=settings.icloud_caldav_user,
            password=settings.icloud_caldav_password,
        )

        tomorrow = date.today() + timedelta(days=1)
        events = await caldav.get_events(tomorrow)

        return events

    async def _fetch_ai_usage(self) -> dict[str, Any]:
        """Fetch today's AI usage summary from model_usage."""
        from db.models import ModelUsage
        from db.session import get_db_session

        today_start, today_end = _today_range()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    ModelUsage.model,
                    func.count().label("calls"),
                    func.coalesce(func.sum(ModelUsage.tokens_input), 0).label("tokens_in"),
                    func.coalesce(func.sum(ModelUsage.tokens_output), 0).label("tokens_out"),
                    func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("cost"),
                )
                .where(
                    ModelUsage.created_at >= today_start,
                    ModelUsage.created_at < today_end,
                )
                .group_by(ModelUsage.model)
            )
            rows = result.all()

        by_model = {}
        total_calls = 0
        for row in rows:
            by_model[row.model] = {
                "calls": row.calls,
                "tokens_input": int(row.tokens_in),
                "tokens_output": int(row.tokens_out),
                "estimated_cost": str(row.cost),
            }
            total_calls += row.calls

        return {"total_calls": total_calls, "by_model": by_model}

    async def _fetch_spending(self) -> dict[str, Any]:
        """Fetch today's spending from transactions."""
        from db.models import Transaction
        from db.session import get_db_session

        today = date.today()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    Transaction.merchant_name,
                    Transaction.amount,
                    Transaction.category,
                )
                .where(
                    Transaction.transaction_date == today,
                    Transaction.pending == False,  # noqa: E712
                )
                .order_by(Transaction.amount.desc())
            )
            rows = result.all()

        transactions = [
            {
                "merchant": row.merchant_name or "Unknown",
                "amount": float(row.amount),
                "category": row.category,
            }
            for row in rows
        ]
        total = sum(t["amount"] for t in transactions)

        return {"total": round(total, 2), "transactions": transactions}

    async def _fetch_health_data(self) -> dict[str, Any]:
        """Fetch today's health data (steps, workouts)."""
        from db.models import HealthData
        from db.session import get_db_session

        today = date.today()

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    HealthData.data_type,
                    HealthData.value,
                    HealthData.unit,
                )
                .where(HealthData.recorded_date == today)
                .order_by(HealthData.data_type)
            )
            rows = result.all()

        if not rows:
            return {}

        data: dict[str, Any] = {}
        for data_type, value, unit in rows:
            data[data_type] = {"value": float(value), "unit": unit}

        return data

    async def _fetch_pending_items(self) -> dict[str, int]:
        """Fetch counts of pending approvals and open tasks."""
        from db.models import AgentTask, Approval
        from db.session import get_db_session

        async with get_db_session() as session:
            pending_approvals_result = await session.execute(
                select(func.count()).select_from(Approval).where(Approval.status == ApprovalStatus.PENDING)
            )
            pending_approvals = pending_approvals_result.scalar() or 0

            open_tasks_result = await session.execute(
                select(func.count())
                .select_from(AgentTask)
                .where(AgentTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]))
            )
            open_tasks = open_tasks_result.scalar() or 0

        return {"pending_approvals": pending_approvals, "open_tasks": open_tasks}

    # ------------------------------------------------------------------
    # Alarm time suggestion
    # ------------------------------------------------------------------

    def _compute_alarm_time(self, tomorrow_events: list[dict[str, Any]]) -> str | None:
        """Suggest alarm time based on tomorrow's first event.

        Assumes 60 minutes to prepare + commute for the first event.
        """
        for event in tomorrow_events:
            start = event.get("start", "")
            if not start:
                continue
            try:
                if "T" in str(start):
                    dt = datetime.fromisoformat(str(start))
                else:
                    parts = str(start).split(":")
                    dt = datetime.now().replace(
                        hour=int(parts[0]),
                        minute=int(parts[1]) if len(parts) > 1 else 0,
                    )
                alarm = dt - timedelta(minutes=60)
                return alarm.strftime("%-I:%M %p")
            except (ValueError, IndexError):
                continue
        return None

    # ------------------------------------------------------------------
    # Narrative composition (Gemini Pro)
    # ------------------------------------------------------------------

    async def _compose_narrative(
        self,
        payload: dict[str, Any],
        gemini: GeminiClient | None,
    ) -> str:
        """Compose EOD summary narrative via Gemini Pro."""
        if gemini is None:
            log.warning("eod_no_gemini", fallback="plain_text")
            return _FALLBACK_NARRATIVE

        prompt = (
            "Compose a brief end-of-day summary for Tasin from this data.\n"
            "Include: what was accomplished, what's pending, tomorrow's schedule, "
            "suggested wake-up time based on first event, and an encouraging close.\n"
            "Speak directly to Tasin in second person. Be warm but concise.\n"
            "No markdown, no bullet points — flowing sentences.\n"
            'Start with "Good evening, Tasin." and end with an encouraging note about rest.\n'
            "Keep it under 400 words.\n\n"
            f"Data:\n{json.dumps(payload, indent=2, default=str)}"
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-pro",
                system_instruction=_NARRATIVE_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=2048,
            )
            log.info(
                "eod_narrative_composed",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )
            return response.text.strip()

        except Exception:
            log.error("eod_narrative_failed", exc_info=True)
            return _FALLBACK_NARRATIVE

    # ------------------------------------------------------------------
    # Database storage
    # ------------------------------------------------------------------

    async def _store_briefing(
        self,
        payload: dict[str, Any],
        narrative: str,
    ) -> uuid.UUID:
        """Store the EOD summary as a briefing with type=end_of_day."""
        from db.models import Briefing
        from db.session import get_db_session

        today = date.today()
        briefing_id = uuid.uuid4()

        async with get_db_session() as session:
            briefing = Briefing(
                id=briefing_id,
                briefing_type="end_of_day",
                briefing_date=today,
                payload=payload,
                narrative=narrative,
            )
            session.add(briefing)

        log.info(
            "eod_briefing_stored",
            briefing_id=str(briefing_id),
            briefing_date=today.isoformat(),
        )
        return briefing_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_range() -> tuple[datetime, datetime]:
    """Return (start, end) datetime range for today in UTC."""
    today = date.today()
    start = datetime.combine(today, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    return start, end
