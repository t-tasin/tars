"""AI model usage tracking for budget monitoring. HC-12 compliance."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ModelUsage
from shared.constants import ModelName

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Cost rates per 1M tokens (USD)
# ---------------------------------------------------------------------------

_COST_PER_1M: dict[str, tuple[Decimal, Decimal]] = {
    # (input_per_1M, output_per_1M)
    ModelName.GEMINI_FLASH: (Decimal("0.10"), Decimal("0.40")),
    ModelName.GEMINI_PRO: (Decimal("1.25"), Decimal("5.00")),
    ModelName.GEMINI_VISION: (Decimal("1.25"), Decimal("5.00")),
    ModelName.CLAUDE_CODE: (Decimal("0"), Decimal("0")),  # Max plan, no per-token cost
    ModelName.LOCAL: (Decimal("0"), Decimal("0")),
}

# Budget thresholds
CLAUDE_WEEKLY_LIMIT = 70
CLAUDE_DAILY_LIMIT = 15
BUDGET_ALERT_PCT = 0.70  # Alert at 70% of limit


class UsageTracker:
    """Tracks all AI model usage for budget monitoring. HC-12 compliance.

    Accepts an ``AsyncSession`` so the caller controls transaction scope.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Record usage
    # ------------------------------------------------------------------

    async def track(
        self,
        model: str,
        agent_type: str,
        task_id: UUID | None,
        tokens_input: int,
        tokens_output: int,
        duration_ms: int,
        success: bool,
        error_type: str | None = None,
    ) -> None:
        """Record a model usage entry in the ``model_usage`` table."""
        cost = _estimate_cost(model, tokens_input, tokens_output)

        record = ModelUsage(
            model=model,
            agent_type=agent_type,
            task_id=task_id,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            estimated_cost=cost,
            duration_ms=duration_ms,
            success=success,
            error_type=error_type,
        )
        self._session.add(record)
        await self._session.flush()

        log.info(
            "usage_tracked",
            model=model,
            agent_type=agent_type,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            estimated_cost=str(cost),
            duration_ms=duration_ms,
            success=success,
        )

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    async def get_daily_summary(
        self, date: dt.date | None = None,
    ) -> dict[str, dict[str, int | str]]:
        """Get usage summary for a given day.

        Returns a dict keyed by model name::

            {
                "claude_code": {"calls": 5, "tokens_input": 1200, "tokens_output": 800, "estimated_cost": "0.00"},
                "gemini_flash": {...},
                ...
            }
        """
        target = date or dt.date.today()
        start = dt.datetime.combine(target, dt.time.min, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=1)

        stmt = (
            select(
                ModelUsage.model,
                func.count().label("calls"),
                func.coalesce(func.sum(ModelUsage.tokens_input), 0).label("tokens_input"),
                func.coalesce(func.sum(ModelUsage.tokens_output), 0).label("tokens_output"),
                func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("estimated_cost"),
            )
            .where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
            .group_by(ModelUsage.model)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        summary: dict[str, dict[str, int | str]] = {}
        for row in rows:
            summary[row.model] = {
                "calls": row.calls,
                "tokens_input": row.tokens_input,
                "tokens_output": row.tokens_output,
                "estimated_cost": str(row.estimated_cost),
            }
        return summary

    async def get_weekly_summary(self) -> dict[str, int | float | str]:
        """Get usage summary for the current week (Monday–Sunday).

        Returns::

            {
                "week_start": "2026-03-09",
                "claude_calls": 12,
                "claude_weekly_limit": 70,
                "claude_limit_pct": 17.1,
                "gemini_calls": 85,
                "total_calls": 97,
                "total_estimated_cost": "0.045000",
            }
        """
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        week_start = dt.datetime.combine(monday, dt.time.min, tzinfo=dt.timezone.utc)
        week_end = week_start + dt.timedelta(days=7)

        stmt = (
            select(
                ModelUsage.model,
                func.count().label("calls"),
                func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("estimated_cost"),
            )
            .where(ModelUsage.created_at >= week_start, ModelUsage.created_at < week_end)
            .group_by(ModelUsage.model)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        claude_calls = 0
        gemini_calls = 0
        total_cost = Decimal("0")

        for row in rows:
            total_cost += Decimal(str(row.estimated_cost))
            if row.model == ModelName.CLAUDE_CODE:
                claude_calls = row.calls
            else:
                gemini_calls += row.calls

        total_calls = claude_calls + gemini_calls
        limit_pct = round((claude_calls / CLAUDE_WEEKLY_LIMIT) * 100, 1) if CLAUDE_WEEKLY_LIMIT else 0

        return {
            "week_start": monday.isoformat(),
            "claude_calls": claude_calls,
            "claude_weekly_limit": CLAUDE_WEEKLY_LIMIT,
            "claude_limit_pct": limit_pct,
            "gemini_calls": gemini_calls,
            "total_calls": total_calls,
            "total_estimated_cost": str(total_cost),
        }

    async def get_top_agents_by_usage(
        self, date: dt.date | None = None, limit: int = 5,
    ) -> list[dict[str, int | str]]:
        """Get top agents by AI call count for a given day.

        Returns a list of dicts::

            [{"agent_type": "briefing", "calls": 12, "total_tokens": 5400, "estimated_cost": "0.002"}, ...]
        """
        target = date or dt.date.today()
        start = dt.datetime.combine(target, dt.time.min, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=1)

        stmt = (
            select(
                ModelUsage.agent_type,
                func.count().label("calls"),
                func.coalesce(
                    func.sum(ModelUsage.tokens_input + ModelUsage.tokens_output), 0,
                ).label("total_tokens"),
                func.coalesce(func.sum(ModelUsage.estimated_cost), 0).label("estimated_cost"),
            )
            .where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
            .group_by(ModelUsage.agent_type)
            .order_by(func.count().desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return [
            {
                "agent_type": row.agent_type,
                "calls": row.calls,
                "total_tokens": int(row.total_tokens),
                "estimated_cost": str(row.estimated_cost),
            }
            for row in result.all()
        ]

    async def generate_report(self) -> dict[str, Any]:
        """Generate a full daily AI usage report with formatted text.

        Returns a dict with keys: daily, weekly, top_agents, budget_alert, formatted.
        """
        daily = await self.get_daily_summary()
        weekly = await self.get_weekly_summary()
        top_agents = await self.get_top_agents_by_usage()
        budget_alert = await self.check_budget_alert()

        # Build formatted text for notification
        lines: list[str] = []

        # Daily summary
        total_daily_calls = sum(m.get("calls", 0) for m in daily.values())  # type: ignore[union-attr]
        total_daily_cost = sum(
            Decimal(str(m.get("estimated_cost", "0"))) for m in daily.values()  # type: ignore[union-attr]
        )
        claude_daily = daily.get(ModelName.CLAUDE_CODE, {})
        claude_daily_calls = claude_daily.get("calls", 0) if claude_daily else 0

        lines.append(f"Today: {total_daily_calls} calls, ${total_daily_cost:.4f}")
        lines.append(f"  Claude: {claude_daily_calls}/{CLAUDE_DAILY_LIMIT} daily")

        # Gemini breakdown
        for model in [ModelName.GEMINI_FLASH, ModelName.GEMINI_PRO, ModelName.GEMINI_VISION]:
            info = daily.get(model)
            if info:
                lines.append(f"  {model}: {info['calls']} calls")

        # Weekly summary
        lines.append("")
        lines.append(
            f"This week: {weekly['total_calls']} calls, "
            f"${weekly['total_estimated_cost']}"
        )
        lines.append(
            f"  Claude: {weekly['claude_calls']}/{weekly['claude_weekly_limit']} "
            f"({weekly['claude_limit_pct']}%)"
        )

        # Top agents
        if top_agents:
            lines.append("")
            lines.append("Top agents today:")
            for agent in top_agents[:5]:
                lines.append(
                    f"  {agent['agent_type']}: {agent['calls']} calls, "
                    f"{agent['total_tokens']} tokens"
                )

        # Budget alert
        if budget_alert:
            lines.append("")
            lines.append(f"ALERT: {budget_alert}")

        return {
            "daily": daily,
            "weekly": weekly,
            "top_agents": top_agents,
            "budget_alert": budget_alert,
            "formatted": "\n".join(lines),
        }

    async def check_budget_alert(self) -> str | None:
        """Check if approaching Claude budget limits.

        Returns an alert message string, or ``None`` if within budget.
        """
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        week_start = dt.datetime.combine(monday, dt.time.min, tzinfo=dt.timezone.utc)

        # Today's Claude calls
        today_start = dt.datetime.combine(today, dt.time.min, tzinfo=dt.timezone.utc)
        today_end = today_start + dt.timedelta(days=1)

        daily_stmt = (
            select(func.count())
            .select_from(ModelUsage)
            .where(
                ModelUsage.model == ModelName.CLAUDE_CODE,
                ModelUsage.created_at >= today_start,
                ModelUsage.created_at < today_end,
            )
        )

        weekly_stmt = (
            select(func.count())
            .select_from(ModelUsage)
            .where(
                ModelUsage.model == ModelName.CLAUDE_CODE,
                ModelUsage.created_at >= week_start,
            )
        )

        daily_result = await self._session.execute(daily_stmt)
        weekly_result = await self._session.execute(weekly_stmt)

        daily_calls = daily_result.scalar() or 0
        weekly_calls = weekly_result.scalar() or 0

        alerts: list[str] = []

        if weekly_calls >= CLAUDE_WEEKLY_LIMIT:
            alerts.append(
                f"Claude weekly limit REACHED: {weekly_calls}/{CLAUDE_WEEKLY_LIMIT} calls."
            )
        elif weekly_calls >= int(CLAUDE_WEEKLY_LIMIT * BUDGET_ALERT_PCT):
            alerts.append(
                f"Claude weekly budget warning: {weekly_calls}/{CLAUDE_WEEKLY_LIMIT} calls "
                f"({round(weekly_calls / CLAUDE_WEEKLY_LIMIT * 100)}% used)."
            )

        if daily_calls >= CLAUDE_DAILY_LIMIT:
            alerts.append(
                f"Claude daily limit REACHED: {daily_calls}/{CLAUDE_DAILY_LIMIT} calls."
            )
        elif daily_calls >= int(CLAUDE_DAILY_LIMIT * BUDGET_ALERT_PCT):
            alerts.append(
                f"Claude daily budget warning: {daily_calls}/{CLAUDE_DAILY_LIMIT} calls "
                f"({round(daily_calls / CLAUDE_DAILY_LIMIT * 100)}% used)."
            )

        if alerts:
            msg = " ".join(alerts)
            log.warning("budget_alert", message=msg, daily=daily_calls, weekly=weekly_calls)
            return msg

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_cost(model: str, tokens_input: int, tokens_output: int) -> Decimal:
    """Estimate USD cost for a model call based on token counts."""
    rates = _COST_PER_1M.get(model)
    if not rates:
        return Decimal("0")

    input_rate, output_rate = rates
    cost = (
        input_rate * Decimal(tokens_input) / Decimal("1000000")
        + output_rate * Decimal(tokens_output) / Decimal("1000000")
    )
    return cost.quantize(Decimal("0.000001"))
