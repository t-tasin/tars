"""Shared finance helpers used by both the finance agent and API."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any


def resolve_period(period: str, anchor: date) -> tuple[date, date]:
    """Resolve a period string to a (start_date, end_date) tuple."""
    if period == "day":
        return anchor, anchor
    elif period == "month":
        start = anchor.replace(day=1)
        return start, anchor
    else:
        # Default to week (Monday-based)
        start = anchor - timedelta(days=anchor.weekday())
        return start, anchor


def previous_period_start(period: str, current_start: date) -> date:
    """Compute the start date of the previous equivalent period.

    - day:   previous day
    - week:  7 days before current_start
    - month: first day of the previous calendar month
    """
    if period == "day":
        return current_start - timedelta(days=1)
    elif period == "month":
        # Go to last day of previous month, then to its first day
        last_of_prev = current_start - timedelta(days=1)
        return last_of_prev.replace(day=1)
    else:
        return current_start - timedelta(weeks=1)


def pct_change(current: float, previous: float) -> float:
    """Compute percentage change from *previous* to *current*.

    Returns 0.0 when both values are zero, and 100.0 when *previous* is
    zero but *current* is positive.
    """
    if previous > 0:
        return round(((current - previous) / previous) * 100, 1)
    return 0.0 if current == 0 else 100.0


def compute_budget_statuses(
    budgets: list[Any],
    spending_by_category: dict[str, float],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Compute month-to-date budget vs. actual for a list of budgets.

    Args:
        budgets: ORM Budget objects (must have .category, .monthly_limit).
        spending_by_category: mapping of lowercase category -> total spent (MTD).
        today: override for testing; defaults to date.today().

    Returns:
        List of budget status dicts with category, spent, remaining, percent_used,
        days_remaining, on_track.
    """
    if not budgets:
        return []

    today = today or date.today()
    month_start = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = (today - month_start).days + 1
    days_remaining = days_in_month - days_elapsed

    statuses: list[dict[str, Any]] = []
    for budget in budgets:
        limit = float(budget.monthly_limit)
        spent = spending_by_category.get(budget.category.lower(), 0.0)
        remaining = max(limit - spent, 0.0)
        percent_used = round((spent / limit) * 100, 1) if limit > 0 else 0.0

        # Projection: use min 3 days elapsed to avoid noisy day-1 projections
        projection_days = max(days_elapsed, 3)
        projected = (spent / projection_days) * days_in_month if spent > 0 else 0.0
        on_track = projected <= limit

        statuses.append(
            {
                "category": budget.category,
                "monthly_limit": limit,
                "spent": round(spent, 2),
                "remaining": round(remaining, 2),
                "percent_used": percent_used,
                "days_remaining": days_remaining,
                "on_track": on_track,
            }
        )

    return statuses
