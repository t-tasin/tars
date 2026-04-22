"""Tests for FinanceAgent — spending summaries and trend analysis (HC-04)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext
from agents.finance import (
    FinanceAgent,
    _fallback_insights,
    _format_budget_alerts,
)
from models.gemini_client import GeminiResponse
from utils.finance import pct_change, resolve_period

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    period: str = "week",
    target_date: str | None = None,
    gemini: AsyncMock | None = None,
) -> AgentContext:
    config: dict[str, Any] = {"period": period}
    if target_date:
        config["target_date"] = target_date
    if gemini:
        config["gemini_client"] = gemini
    return AgentContext(
        user_message="Show my spending summary",
        intent_type="finance",
        config=config,
    )


def _mock_summary() -> dict[str, Any]:
    return {
        "total_spent": Decimal("245.50"),
        "total_income": Decimal("0"),
        "transaction_count": 8,
        "by_category": {
            "FOOD_AND_DRINK": {"amount": 120.00, "count": 5},
            "TRANSPORTATION": {"amount": 80.50, "count": 2},
            "ENTERTAINMENT": {"amount": 45.00, "count": 1},
        },
        "top_merchants": [
            {"name": "Starbucks", "amount": 55.00, "count": 3},
            {"name": "Uber", "amount": 80.50, "count": 2},
        ],
    }


def _mock_execute_patches(agent: FinanceAgent) -> dict[str, Any]:
    """Return a dict of context managers that mock all execute() dependencies."""
    mock_tracker = AsyncMock()
    mock_tracker.get_subscription_summary = AsyncMock(
        return_value={
            "subscription_count": 0,
            "monthly_total": 0.0,
            "subscriptions": [],
            "price_changes": [],
        }
    )

    mock_detector = AsyncMock()
    mock_detector.scan_recent = AsyncMock(return_value=[])

    return {
        "sub_tracker": patch(
            "agents.subscription_tracker.SubscriptionTracker",
            return_value=mock_tracker,
        ),
        "anomaly_det": patch(
            "agents.anomaly_detector.AnomalyDetector",
            return_value=mock_detector,
        ),
        "build_summary": patch.object(
            agent,
            "_build_summary",
            new_callable=AsyncMock,
        ),
        "fetch_txns": patch.object(
            agent,
            "_fetch_transactions",
            new_callable=AsyncMock,
        ),
        "fetch_subs": patch.object(
            agent,
            "_fetch_subscriptions",
            new_callable=AsyncMock,
        ),
        "compute_trends": patch.object(
            agent,
            "_compute_trends",
            new_callable=AsyncMock,
        ),
        "persist_summary": patch.object(
            agent,
            "_persist_summary",
            new_callable=AsyncMock,
        ),
        "budget_status": patch.object(
            agent,
            "_build_budget_status",
            new_callable=AsyncMock,
        ),
    }


# ---------------------------------------------------------------------------
# Tests — resolve_period
# ---------------------------------------------------------------------------


class TestPctChange:
    def test_normal(self) -> None:
        assert pct_change(115.0, 100.0) == 15.0

    def test_decrease(self) -> None:
        assert pct_change(80.0, 100.0) == -20.0

    def test_zero_previous_nonzero_current(self) -> None:
        assert pct_change(50.0, 0.0) == 100.0

    def test_both_zero(self) -> None:
        assert pct_change(0.0, 0.0) == 0.0


class TestResolvePeriod:
    def test_day(self) -> None:
        start, end = resolve_period("day", date(2026, 3, 10))
        assert start == date(2026, 3, 10)
        assert end == date(2026, 3, 10)

    def test_week(self) -> None:
        start, end = resolve_period("week", date(2026, 3, 10))
        assert start == date(2026, 3, 9)
        assert end == date(2026, 3, 10)

    def test_month(self) -> None:
        start, end = resolve_period("month", date(2026, 3, 10))
        assert start == date(2026, 3, 1)
        assert end == date(2026, 3, 10)


# ---------------------------------------------------------------------------
# Tests — _fallback_insights
# ---------------------------------------------------------------------------


class TestFallbackInsights:
    def test_no_transactions(self) -> None:
        summary = _mock_summary()
        summary["transaction_count"] = 0
        result = _fallback_insights(summary, None, "week")
        assert "No transactions" in result

    def test_with_transactions(self) -> None:
        summary = _mock_summary()
        result = _fallback_insights(summary, None, "week")
        assert "$245.50" in result
        assert "8 transactions" in result
        assert "FOOD_AND_DRINK" in result


# ---------------------------------------------------------------------------
# Tests — _format_budget_alerts
# ---------------------------------------------------------------------------


class TestFormatBudgetAlerts:
    def test_no_alerts_under_threshold(self) -> None:
        statuses = [
            {
                "category": "dining",
                "monthly_limit": 200.0,
                "spent": 100.0,
                "remaining": 100.0,
                "percent_used": 50.0,
                "days_remaining": 15,
                "on_track": True,
            },
        ]
        assert _format_budget_alerts(statuses) == ""

    def test_alert_at_80_percent(self) -> None:
        statuses = [
            {
                "category": "dining",
                "monthly_limit": 200.0,
                "spent": 178.0,
                "remaining": 22.0,
                "percent_used": 89.0,
                "days_remaining": 10,
                "on_track": False,
            },
        ]
        result = _format_budget_alerts(statuses)
        assert "Budget alert: Dining at 89%" in result
        assert "$178.00/$200.00" in result
        assert "10 days left" in result

    def test_empty_list(self) -> None:
        assert _format_budget_alerts([]) == ""


# ---------------------------------------------------------------------------
# Tests — FinanceAgent.execute
# ---------------------------------------------------------------------------


class TestFinanceAgentExecute:
    @pytest.mark.asyncio
    async def test_execute_without_gemini(self) -> None:
        """FinanceAgent works without Gemini (HC-09 graceful degradation)."""
        agent = FinanceAgent()
        context = _make_context(period="week")
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.success is True
        assert result.data["total_spent"] == 245.50
        assert result.data["transaction_count"] == 8
        assert result.data["period"] == "week"
        assert "FOOD_AND_DRINK" in result.data["by_category"]
        assert result.data["budget_status"] == []

    @pytest.mark.asyncio
    async def test_execute_with_gemini_insights(self) -> None:
        """FinanceAgent uses Gemini Flash for insights when available."""
        mock_gemini = AsyncMock()
        mock_gemini.generate = AsyncMock(
            return_value=GeminiResponse(
                text="Dining spending is up 30% vs last week. Consider meal prepping.",
                model="gemini-2.0-flash",
                tokens_input=200,
                tokens_output=50,
                duration_ms=800,
                finish_reason="STOP",
            )
        )

        agent = FinanceAgent()
        context = _make_context(period="week", gemini=mock_gemini)
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.success is True
        assert "30%" in result.data["insights"]
        mock_gemini.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_gemini_failure_falls_back(self) -> None:
        """FinanceAgent degrades gracefully when Gemini fails (HC-09)."""
        mock_gemini = AsyncMock()
        mock_gemini.generate = AsyncMock(side_effect=Exception("API timeout"))

        agent = FinanceAgent()
        context = _make_context(period="week", gemini=mock_gemini)
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.success is True
        assert "$245.50" in result.data["insights"]

    @pytest.mark.asyncio
    async def test_execute_no_side_effects(self) -> None:
        """FinanceAgent never has side effects (HC-04)."""
        agent = FinanceAgent()
        context = _make_context()
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.has_side_effects is False

    @pytest.mark.asyncio
    async def test_execute_with_budget_alerts(self) -> None:
        """FinanceAgent includes budget alerts when budgets are over 80%."""
        agent = FinanceAgent()
        context = _make_context()

        budget_data = [
            {
                "category": "dining",
                "monthly_limit": 200.0,
                "spent": 178.0,
                "remaining": 22.0,
                "percent_used": 89.0,
                "days_remaining": 10,
                "on_track": False,
            },
        ]

        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = budget_data

            result = await agent.execute(context)

        assert result.data["budget_status"] == budget_data
        assert "Budget alert: Dining at 89%" in result.text
        assert "$178.00/$200.00" in result.text
        assert "10 days left" in result.text


class TestFinanceAgentTrends:
    """Test trend comparison logic in FinanceAgent."""

    @pytest.mark.asyncio
    async def test_trends_computed_when_previous_period_exists(self) -> None:
        """When a previous period summary exists, trends should be populated."""
        agent = FinanceAgent()
        context = _make_context(period="week")
        p = _mock_execute_patches(agent)

        mock_trends = {
            "total_change_pct": 15.0,
            "previous_total": 213.50,
            "category_changes": {"FOOD_AND_DRINK": 20.0},
            "top_increase": {"category": "FOOD_AND_DRINK", "change_pct": 20.0},
            "top_decrease": None,
        }

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = mock_trends
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.data["trends"] is not None
        assert result.data["trends"]["total_change_pct"] == 15.0
        assert result.data["trends"]["previous_total"] == 213.50
        assert "up 15.0%" in result.text

    @pytest.mark.asyncio
    async def test_trends_none_when_no_previous_period(self) -> None:
        """When no previous period summary exists, trends should be None."""
        agent = FinanceAgent()
        context = _make_context(period="week")
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            result = await agent.execute(context)

        assert result.data["trends"] is None


class TestFinanceAgentPersistence:
    """Test that FinanceAgent persists summaries."""

    @pytest.mark.asyncio
    async def test_persist_summary_called(self) -> None:
        """Verify _persist_summary is called after execute."""
        agent = FinanceAgent()
        context = _make_context(period="week")
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"] as m_persist,
            p["budget_status"] as m_budget,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []

            await agent.execute(context)

        m_persist.assert_awaited_once()


class TestFinanceAgentClaudeEscalation:
    """Test Claude escalation and fallback (HC-09)."""

    @pytest.mark.asyncio
    async def test_claude_escalation_on_analyze_keyword(self) -> None:
        """When user message contains 'analyze', Claude should be called."""
        agent = FinanceAgent()
        context = AgentContext(
            user_message="analyze my spending",
            intent_type="finance",
            config={"period": "week"},
        )
        p = _mock_execute_patches(agent)

        mock_claude_result = MagicMock()
        mock_claude_result.success = True
        mock_claude_result.text = "Deep analysis: your dining spending is 40% above average."
        mock_claude_result.duration_ms = 3000
        mock_claude_result.cost_usd = 0.02

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
            patch.object(agent._claude, "execute", new_callable=AsyncMock) as m_claude,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []
            m_claude.return_value = mock_claude_result

            result = await agent.execute(context)

        m_claude.assert_awaited_once()
        assert "Deep analysis" in result.data["insights"]

    @pytest.mark.asyncio
    async def test_claude_fallback_to_gemini_on_failure(self) -> None:
        """When Claude fails, Gemini should be used (HC-09)."""
        from models.claude_spawner import ClaudeSpawnError

        mock_gemini = AsyncMock()
        mock_gemini.generate = AsyncMock(
            return_value=GeminiResponse(
                text="Gemini fallback insight: spending is normal.",
                model="gemini-2.0-flash",
                tokens_input=200,
                tokens_output=50,
                duration_ms=800,
                finish_reason="STOP",
            )
        )

        agent = FinanceAgent()
        context = AgentContext(
            user_message="analyze my spending trends",
            intent_type="finance",
            config={"period": "week", "gemini_client": mock_gemini},
        )
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
            patch.object(agent._claude, "execute", new_callable=AsyncMock) as m_claude,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []
            m_claude.side_effect = ClaudeSpawnError("Claude unavailable")

            result = await agent.execute(context)

        assert result.success is True
        mock_gemini.generate.assert_awaited_once()
        assert "Gemini fallback" in result.data["insights"]

    @pytest.mark.asyncio
    async def test_claude_fallback_to_local_when_both_fail(self) -> None:
        """When Claude and Gemini both fail, local fallback used (HC-09)."""
        from models.claude_spawner import ClaudeSpawnError

        mock_gemini = AsyncMock()
        mock_gemini.generate = AsyncMock(side_effect=Exception("Gemini down"))

        agent = FinanceAgent()
        context = AgentContext(
            user_message="analyze spending breakdown",
            intent_type="finance",
            config={"period": "week", "gemini_client": mock_gemini},
        )
        p = _mock_execute_patches(agent)

        with (
            p["sub_tracker"],
            p["anomaly_det"],
            p["build_summary"] as m_build,
            p["fetch_txns"] as m_txns,
            p["fetch_subs"] as m_subs,
            p["compute_trends"] as m_trends,
            p["persist_summary"],
            p["budget_status"] as m_budget,
            patch.object(agent._claude, "execute", new_callable=AsyncMock) as m_claude,
        ):
            m_build.return_value = _mock_summary()
            m_txns.return_value = []
            m_subs.return_value = []
            m_trends.return_value = None
            m_budget.return_value = []
            m_claude.side_effect = ClaudeSpawnError("Claude unavailable")

            result = await agent.execute(context)

        assert result.success is True
        # Fallback insights contain the raw spending amount
        assert "$245.50" in result.data["insights"]

    def test_escalation_keywords(self) -> None:
        """Verify all escalation keywords trigger Claude."""
        agent = FinanceAgent()

        for keyword in ["analyze", "deep dive", "trend", "anomaly", "investigate"]:
            context = AgentContext(
                user_message=f"Please {keyword} my finances",
                intent_type="finance",
                config={"period": "week"},
            )
            assert agent._should_escalate_to_claude(context) is True

    def test_normal_message_does_not_escalate(self) -> None:
        """Normal messages should not trigger Claude escalation."""
        agent = FinanceAgent()
        context = AgentContext(
            user_message="Show my spending summary",
            intent_type="finance",
            config={"period": "week"},
        )
        assert agent._should_escalate_to_claude(context) is False

    def test_force_claude_config(self) -> None:
        """force_claude config overrides keyword check."""
        agent = FinanceAgent()
        context = AgentContext(
            user_message="Show my spending summary",
            intent_type="finance",
            config={"period": "week", "force_claude": True},
        )
        assert agent._should_escalate_to_claude(context) is True


class TestHC04Compliance:
    """Verify HC-04: FinanceAgent is strictly read-only."""

    def test_no_write_operations_in_source(self) -> None:
        import inspect

        from agents import finance

        source = inspect.getsource(finance)

        forbidden = [
            "initiate_transfer",
            "send_payment",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"HC-04 VIOLATION: Found '{pattern}' in finance.py. FinanceAgent must be strictly read-only."
            )
