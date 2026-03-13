"""Finance agent — spending summaries, trend analysis, and Claude escalation.

HC-04: This agent is strictly READ-ONLY with respect to external financial
services.  It queries the local transactions table (populated by
TellerClient.sync_daily), persists summaries to the local finance_summaries
table, and uses Gemini Flash or Claude Code for analysis insights.
It NEVER initiates financial transactions.
"""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import AgentContext, AgentResult, BaseAgent
from models.claude_spawner import ClaudeCodeSpawner, ClaudeSpawnError
from models.gemini_client import GeminiClient
from utils.finance import compute_budget_statuses, pct_change, previous_period_start, resolve_period

log = structlog.get_logger()

_FINANCE_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI finance assistant for Tasin. "
    "Analyze spending data and provide concise, actionable insights. "
    "Be direct and helpful. Highlight notable trends and anomalies. "
    "Never suggest making transactions or moving money — read-only analysis only."
)

_ESCALATION_KEYWORDS = frozenset({
    "analyze", "deep dive", "compare", "why", "explain spending",
    "trend", "unusual", "anomaly", "breakdown", "investigate",
})


class FinanceAgent(BaseAgent):
    """Spending summary, trend analysis, and Claude-escalated deep dives.

    HC-04 compliance: This agent NEVER initiates financial transactions,
    makes purchases, moves money, or modifies any financial account.
    It only reads from the local transactions table, persists summaries
    to the local finance_summaries table, and provides analysis.
    """

    agent_type = "finance"

    def __init__(self) -> None:
        self._claude = ClaudeCodeSpawner()

    async def execute(self, context: AgentContext) -> AgentResult:
        """Show spending summary with trend comparison and optional AI analysis.

        Expects ``context.config`` to optionally contain:
        - ``gemini_client``: a :class:`GeminiClient` for trend insights
        - ``period``: ``"day"``, ``"week"``, or ``"month"`` (default: ``"week"``)
        - ``target_date``: date string to anchor the period (default: today)
        """
        period = context.config.get("period", "week")
        target_date_str = context.config.get("target_date")
        target_date = date.fromisoformat(target_date_str) if target_date_str else date.today()

        start_date, end_date = resolve_period(period, target_date)

        # Query transactions from local DB (read-only, HC-04)
        from agents.anomaly_detector import AnomalyDetector
        from agents.subscription_tracker import SubscriptionTracker
        from db.session import get_db_session

        async with get_db_session() as session:
            summary = await self._build_summary(session, start_date, end_date)
            transactions = await self._fetch_transactions(session, start_date, end_date)
            subscriptions = await self._fetch_subscriptions(session, start_date, end_date)
            trends = await self._compute_trends(session, period, start_date, summary)

            # Subscription detection & anomaly scanning
            tracker = SubscriptionTracker()
            subscription_summary = await tracker.get_subscription_summary(session)

            detector = AnomalyDetector()
            anomalies_raw = await detector.scan_recent(session, lookback_days=1)

            await self._persist_summary(
                session, period, start_date, end_date, summary, trends, subscriptions,
            )
            budget_status = await self._build_budget_status(session)

        anomalies = [
            {
                "transaction_id": a.transaction_id,
                "merchant": a.merchant,
                "amount": a.amount,
                "date": a.date.isoformat(),
                "reason": a.reason,
                "details": a.details,
                "severity": a.severity,
            }
            for a in anomalies_raw
        ]

        # AI insights — Claude escalation or Gemini Flash
        gemini: GeminiClient | None = context.config.get("gemini_client")
        use_claude = self._should_escalate_to_claude(context)

        if use_claude:
            insights = await self._generate_claude_analysis(summary, trends, period, gemini)
        else:
            insights = await self._generate_gemini_insights(summary, trends, period, gemini)

        text = _format_summary_text(
            summary, insights, trends, period, start_date, end_date, anomalies_raw,
        )
        if budget_status:
            text += _format_budget_alerts(budget_status)

        log.info(
            "finance_summary_generated",
            period=period,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_spent=str(summary["total_spent"]),
            transaction_count=summary["transaction_count"],
            used_claude=use_claude,
            has_trends=trends is not None,
            budgets_tracked=len(budget_status),
            subscription_count=subscription_summary["subscription_count"],
            anomaly_count=len(anomalies),
        )

        return AgentResult(
            success=True,
            text=text,
            content_type="card",
            data={
                "period": period,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_spent": float(summary["total_spent"]),
                "total_income": float(summary["total_income"]),
                "by_category": summary["by_category"],
                "top_merchants": summary["top_merchants"],
                "transaction_count": summary["transaction_count"],
                "transactions": transactions,
                "trends": trends,
                "insights": insights,
                "budget_status": budget_status,
                "subscriptions": subscription_summary,
                "anomalies": anomalies,
            },
        )

    # ------------------------------------------------------------------
    # Data queries (READ-ONLY, HC-04)
    # ------------------------------------------------------------------

    async def _build_summary(
        self,
        session: AsyncSession,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Query the transactions table for a spending summary."""
        from db.models import Transaction

        # Total spent (positive amounts = spending)
        total_result = await session.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(),
            ).where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount > 0,
            )
        )
        total_row = total_result.one()
        total_spent = Decimal(str(total_row[0]))
        transaction_count = total_row[1]

        # Total income (negative amounts = incoming)
        income_result = await session.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount < 0,
            )
        )
        total_income = Decimal(str(income_result.scalar_one()))

        # By category
        cat_result = await session.execute(
            select(
                func.coalesce(Transaction.category, "Uncategorized"),
                func.sum(Transaction.amount),
                func.count(),
            )
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount > 0,
            )
            .group_by(Transaction.category)
            .order_by(func.sum(Transaction.amount).desc())
        )
        by_category = {
            row[0]: {"amount": float(row[1]), "count": row[2]}
            for row in cat_result.all()
        }

        # Top merchants
        merchant_result = await session.execute(
            select(
                func.coalesce(Transaction.merchant_name, "Unknown"),
                func.sum(Transaction.amount),
                func.count(),
            )
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount > 0,
            )
            .group_by(Transaction.merchant_name)
            .order_by(func.sum(Transaction.amount).desc())
            .limit(5)
        )
        top_merchants = [
            {"name": row[0], "amount": float(row[1]), "count": row[2]}
            for row in merchant_result.all()
        ]

        return {
            "total_spent": total_spent,
            "total_income": total_income,
            "transaction_count": transaction_count,
            "by_category": by_category,
            "top_merchants": top_merchants,
        }

    async def _fetch_transactions(
        self,
        session: AsyncSession,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch individual transactions for the period (up to limit)."""
        from db.models import Transaction

        result = await session.execute(
            select(
                Transaction.teller_transaction_id,
                Transaction.merchant_name,
                Transaction.amount,
                Transaction.category,
                Transaction.transaction_date,
                Transaction.teller_account_id,
                Transaction.account_name,
            )
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.amount.desc())
            .limit(limit)
        )
        return [
            {
                "teller_transaction_id": row.teller_transaction_id,
                "merchant": row.merchant_name or "Unknown",
                "amount": float(row.amount),
                "category": row.category,
                "date": row.transaction_date.isoformat(),
                "account_id": row.teller_account_id,
                "account_name": row.account_name,
            }
            for row in result.all()
        ]

    async def _fetch_subscriptions(
        self,
        session: AsyncSession,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Fetch recurring/subscription transactions for the period."""
        from db.models import Transaction

        result = await session.execute(
            select(
                Transaction.merchant_name,
                Transaction.amount,
                Transaction.category,
                Transaction.transaction_date,
            )
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
                Transaction.pending == False,  # noqa: E712
                Transaction.is_recurring == True,  # noqa: E712
                Transaction.amount > 0,
            )
            .order_by(Transaction.amount.desc())
        )
        return [
            {
                "merchant": row.merchant_name or "Unknown",
                "amount": float(row.amount),
                "category": row.category,
                "date": row.transaction_date.isoformat(),
            }
            for row in result.all()
        ]

    # ------------------------------------------------------------------
    # Trend comparison
    # ------------------------------------------------------------------

    async def _compute_trends(
        self,
        session: AsyncSession,
        period: str,
        current_start: date,
        current_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Compare current period against the previous equivalent period.

        Returns None if no previous summary exists yet.
        """
        from db.repositories.finance_summaries import FinanceSummaryRepository

        repo = FinanceSummaryRepository(session)
        prev_start = previous_period_start(period, current_start)
        previous = await repo.get_by_period(period, prev_start)

        if previous is None:
            return None

        current_total = float(current_summary["total_spent"])
        prev_total = float(previous.total_spent)

        total_change_pct = pct_change(current_total, prev_total)

        # Per-category deltas
        prev_by_cat: dict[str, dict[str, Any]] = previous.by_category or {}
        curr_by_cat = current_summary["by_category"]
        category_changes: dict[str, float] = {}

        all_categories = set(curr_by_cat.keys()) | set(prev_by_cat.keys())
        for cat in all_categories:
            curr_amt = curr_by_cat.get(cat, {}).get("amount", 0.0)
            prev_amt = prev_by_cat.get(cat, {}).get("amount", 0.0)
            category_changes[cat] = pct_change(curr_amt, prev_amt)

        # Find top increase and decrease
        top_increase: dict[str, Any] | None = None
        top_decrease: dict[str, Any] | None = None

        if category_changes:
            max_cat = max(category_changes, key=category_changes.get)  # type: ignore[arg-type]
            if category_changes[max_cat] > 0:
                top_increase = {"category": max_cat, "change_pct": category_changes[max_cat]}

            min_cat = min(category_changes, key=category_changes.get)  # type: ignore[arg-type]
            if category_changes[min_cat] < 0:
                top_decrease = {"category": min_cat, "change_pct": category_changes[min_cat]}

        return {
            "total_change_pct": total_change_pct,
            "previous_total": prev_total,
            "category_changes": category_changes,
            "top_increase": top_increase,
            "top_decrease": top_decrease,
        }

    # ------------------------------------------------------------------
    # Summary persistence
    # ------------------------------------------------------------------

    async def _persist_summary(
        self,
        session: AsyncSession,
        period: str,
        start_date: date,
        end_date: date,
        summary: dict[str, Any],
        trends: dict[str, Any] | None,
        subscriptions: list[dict[str, Any]],
    ) -> None:
        """Upsert the computed summary into finance_summaries."""
        from db.repositories.finance_summaries import FinanceSummaryRepository

        repo = FinanceSummaryRepository(session)
        alerts = _build_alerts(float(summary["total_spent"]), period, trends)

        await repo.upsert(
            period_type=period,
            period_start=start_date,
            period_end=end_date,
            total_spent=summary["total_spent"],
            total_income=summary["total_income"],
            by_category=summary["by_category"],
            top_merchants=summary["top_merchants"],
            vs_previous_period=trends or {},
            alerts=alerts,
            subscription_charges=subscriptions,
        )

    # ------------------------------------------------------------------
    # Budget vs. actual (preserved from existing implementation)
    # ------------------------------------------------------------------

    async def _build_budget_status(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Compute month-to-date budget vs. actual for all active budgets."""
        from db.models import Transaction
        from db.repositories.budgets import BudgetRepository

        repo = BudgetRepository(session)
        budgets = await repo.get_active()
        if not budgets:
            return []

        today = date.today()
        month_start = today.replace(day=1)
        categories = [b.category.lower() for b in budgets]

        result = await session.execute(
            select(
                func.lower(Transaction.category),
                func.coalesce(func.sum(Transaction.amount), 0),
            )
            .where(
                func.lower(Transaction.category).in_(categories),
                Transaction.transaction_date >= month_start,
                Transaction.transaction_date <= today,
                Transaction.pending == False,  # noqa: E712
                Transaction.amount > 0,
            )
            .group_by(func.lower(Transaction.category))
        )
        spending_by_cat = {row[0]: float(row[1]) for row in result.all()}

        return compute_budget_statuses(budgets, spending_by_cat, today)

    # ------------------------------------------------------------------
    # Claude escalation
    # ------------------------------------------------------------------

    def _should_escalate_to_claude(self, context: AgentContext) -> bool:
        """Check if this request warrants Claude Code analysis."""
        if context.config.get("force_claude"):
            return True

        message_lower = context.user_message.lower()
        return any(kw in message_lower for kw in _ESCALATION_KEYWORDS)

    async def _generate_claude_analysis(
        self,
        summary: dict[str, Any],
        trends: dict[str, Any] | None,
        period: str,
        gemini_fallback: GeminiClient | None,
    ) -> str:
        """Use Claude Code for deep spending analysis.

        HC-09 graceful degradation: Claude -> Gemini -> local fallback.
        """
        data_payload = json.dumps(
            {
                "summary": _serialize_summary(summary),
                "trends": trends,
                "period": period,
            },
            indent=2,
        )

        prompt = (
            "Analyze this personal spending data and provide a detailed, actionable "
            "financial analysis. Identify patterns, anomalies, and areas for optimization. "
            "Compare against the previous period trends if available. "
            "Keep the analysis concise (under 300 words) and direct.\n\n"
            "IMPORTANT: This is read-only analysis. Never suggest making transactions, "
            "moving money, or modifying financial accounts.\n\n"
            f"Data:\n{data_payload}"
        )

        try:
            result = await self._claude.execute(
                prompt=prompt,
                mcp_profile=None,
                timeout=90,
                max_turns=1,
            )
            if result.success and result.text.strip():
                log.info(
                    "finance_claude_analysis",
                    duration_ms=result.duration_ms,
                    cost_usd=result.cost_usd,
                )
                return result.text.strip()

            log.warning("finance_claude_empty_response", falling_back_to="gemini")

        except ClaudeSpawnError:
            log.error("finance_claude_unavailable", exc_info=True, falling_back_to="gemini")

        # Fallback to Gemini (HC-09)
        return await self._generate_gemini_insights(summary, trends, period, gemini_fallback)

    # ------------------------------------------------------------------
    # Gemini insights
    # ------------------------------------------------------------------

    async def _generate_gemini_insights(
        self,
        summary: dict[str, Any],
        trends: dict[str, Any] | None,
        period: str,
        gemini: GeminiClient | None,
    ) -> str:
        """Use Gemini Flash for spending trend analysis.

        Returns a short insights string. If Gemini is unavailable,
        returns a basic local summary (HC-09 graceful degradation).
        """
        if gemini is None or summary["transaction_count"] == 0:
            return _fallback_insights(summary, trends, period)

        data_for_prompt = _serialize_summary(summary)
        if trends:
            data_for_prompt["trends"] = trends

        prompt = (
            f"Analyze this {period}ly spending summary and provide 2-3 concise insights.\n"
            f"Focus on notable patterns, category breakdowns, and actionable observations.\n"
        )

        if trends:
            prompt += (
                f"Compare against the previous period: total spending changed by "
                f"{trends['total_change_pct']}%. "
            )
            if trends.get("top_increase"):
                prompt += (
                    f"Biggest increase: {trends['top_increase']['category']} "
                    f"(+{trends['top_increase']['change_pct']}%). "
                )
            if trends.get("top_decrease"):
                prompt += (
                    f"Biggest decrease: {trends['top_decrease']['category']} "
                    f"({trends['top_decrease']['change_pct']}%). "
                )
            prompt += "\n"

        prompt += (
            f"Keep it under 100 words. Be direct.\n\n"
            f"Data:\n{json.dumps(data_for_prompt, indent=2)}"
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_FINANCE_SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=256,
            )
            log.info(
                "finance_insights_generated",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )
            return response.text.strip()

        except Exception:
            log.error("finance_insights_failed", exc_info=True)
            return _fallback_insights(summary, trends, period)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Make the summary JSON-serializable for AI prompts."""
    return {
        "total_spent": float(summary["total_spent"]),
        "total_income": float(summary["total_income"]),
        "transaction_count": summary["transaction_count"],
        "by_category": summary["by_category"],
        "top_merchants": summary["top_merchants"],
    }


def _build_alerts(
    total_spent: float,
    period: str,
    trends: dict[str, Any] | None,
) -> list[str]:
    """Generate spending alerts based on thresholds and trends."""
    alerts: list[str] = []

    if period == "day" and total_spent > 200:
        alerts.append(f"High daily spending: ${total_spent:.2f}")
    elif period == "week" and total_spent > 1000:
        alerts.append(f"High weekly spending: ${total_spent:.2f}")
    elif period == "month" and total_spent > 3000:
        alerts.append(f"High monthly spending: ${total_spent:.2f}")

    if trends and trends.get("total_change_pct", 0) > 25:
        alerts.append(
            f"Spending up {trends['total_change_pct']}% vs previous {period}"
        )

    if trends and trends.get("top_increase"):
        inc = trends["top_increase"]
        if inc["change_pct"] > 50:
            alerts.append(
                f"{inc['category']} spending surged +{inc['change_pct']}% vs previous {period}"
            )

    return alerts


def _fallback_insights(
    summary: dict[str, Any],
    trends: dict[str, Any] | None,
    period: str,
) -> str:
    """Generate basic insights without AI (HC-09 fallback)."""
    total = float(summary["total_spent"])
    count = summary["transaction_count"]

    if count == 0:
        return f"No transactions recorded for this {period}."

    avg = total / count
    top_cat = (
        max(summary["by_category"].items(), key=lambda x: x[1]["amount"])[0]
        if summary["by_category"]
        else "N/A"
    )

    parts = [f"${total:.2f} spent across {count} transactions. Average: ${avg:.2f}. Top category: {top_cat}."]

    if trends:
        pct = trends["total_change_pct"]
        direction = "up" if pct > 0 else "down"
        parts.append(f"Spending is {direction} {abs(pct)}% vs last {period}.")

    return " ".join(parts)


def _format_budget_alerts(budget_status: list[dict[str, Any]]) -> str:
    """Format budget alerts for budgets over 80% used."""
    alerts: list[str] = []
    for b in budget_status:
        if b["percent_used"] >= 80:
            alerts.append(
                f"Budget alert: {b['category'].title()} at {b['percent_used']:.0f}% "
                f"(${b['spent']:.2f}/${b['monthly_limit']:.2f}) "
                f"with {b['days_remaining']} days left"
            )
    if not alerts:
        return ""
    return "\n\n" + "\n".join(alerts)


def _format_summary_text(
    summary: dict[str, Any],
    insights: str,
    trends: dict[str, Any] | None,
    period: str,
    start_date: date,
    end_date: date,
    anomalies: list[Any] | None = None,
) -> str:
    """Format the summary into a human-readable text response."""
    total = float(summary["total_spent"])
    count = summary["transaction_count"]
    income = float(summary["total_income"])

    period_label = {
        "day": f"Today ({start_date.isoformat()})",
        "week": f"This week ({start_date.isoformat()} to {end_date.isoformat()})",
        "month": f"This month ({start_date.isoformat()} to {end_date.isoformat()})",
    }.get(period, period)

    lines: list[str] = []

    # Warning-severity anomalies prominently at the top
    warnings = [a for a in (anomalies or []) if a.severity == "warning"]
    if warnings:
        for a in warnings:
            lines.append(f"[!] {a.details}")
        lines.append("")

    lines.append(f"Spending Summary — {period_label}")
    lines.append(f"Total spent: ${total:.2f} ({count} transactions)")

    if income > 0:
        lines.append(f"Income: ${income:.2f}")

    # Trend comparison
    if trends:
        pct = trends["total_change_pct"]
        direction = "up" if pct > 0 else "down"
        lines.append(f"vs previous {period}: {direction} {abs(pct)}% (was ${trends['previous_total']:.2f})")

    # Top categories
    if summary["by_category"]:
        lines.append("Top categories:")
        for cat, data in list(summary["by_category"].items())[:5]:
            cat_line = f"  {cat}: ${data['amount']:.2f} ({data['count']} txns)"
            if trends and trends.get("category_changes", {}).get(cat):
                cat_pct = trends["category_changes"][cat]
                cat_line += f" ({'+' if cat_pct > 0 else ''}{cat_pct}%)"
            lines.append(cat_line)

    if insights:
        lines.append("")
        lines.append(insights)

    # Info-level anomalies at the end
    infos = [a for a in (anomalies or []) if a.severity == "info"]
    if infos:
        lines.append("")
        for a in infos:
            lines.append(f"Note: {a.details}")

    return "\n".join(lines)
