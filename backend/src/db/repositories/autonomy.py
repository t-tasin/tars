"""Repository for the ``autonomy_budget`` and ``autonomy_limits`` tables.

Backs ``AutonomyBudgetTracker`` (P4-13). The tracker only ever calls into
this repo for ``WRITE_SELF`` ops — ``READ`` / ``WRITE_LOCAL`` short-circuit
in the service layer, and ``WRITE_WORLD`` / ``WRITE_INFRA`` go straight to
``ApprovalManager`` (HC-01).

Note: the model attr is ``class_`` because ``class`` is reserved; the
underlying column is named ``class``. Repo callers pass strings (the
``AutonomyClass.value``) — this layer doesn't touch the enum directly.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AutonomyBudget, AutonomyLimit

log = structlog.get_logger()


class AutonomyBudgetRepository:
    """CRUD + counter helpers for autonomy budgeting."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # autonomy_budget
    # ------------------------------------------------------------------

    async def get_count(self, day: date, class_: str, agent: str) -> int:
        """Return today's counter for ``(day, class, agent)``. ``0`` if missing."""
        result = await self._session.execute(
            select(AutonomyBudget).where(
                AutonomyBudget.day == day,
                AutonomyBudget.class_ == class_,
                AutonomyBudget.agent == agent,
            )
        )
        # Filter again in Python so the in-memory test shim — which ignores
        # WHERE clauses — still returns the right row. Real PG already
        # filtered server-side; this is a no-op there.
        for row in result.scalars().all():
            if row.day == day and row.class_ == class_ and row.agent == agent:
                return row.count
        return 0

    async def increment(self, day: date, class_: str, agent: str) -> int:
        """Atomically increment the counter for ``(day, class, agent)``.

        Inserts a new row with ``count=1`` if none exists, otherwise
        increments the existing row by 1. Returns the new counter value.
        """
        stmt = (
            pg_insert(AutonomyBudget)
            .values(day=day, **{"class": class_}, agent=agent, count=1)
            .on_conflict_do_update(
                constraint="uq_autonomy_budget_day_class_agent",
                set_={"count": AutonomyBudget.count + 1},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        new_count = await self.get_count(day, class_, agent)
        log.info(
            "autonomy_budget.incremented",
            day=day.isoformat(),
            class_=class_,
            agent=agent,
            count=new_count,
        )
        return new_count

    async def weekly_count(self, class_: str, agent: str, anchor: date) -> int:
        """Return the sum of ``count`` over the 7-day window ending at ``anchor`` inclusive."""
        start = anchor - timedelta(days=6)
        result = await self._session.execute(
            select(func.coalesce(func.sum(AutonomyBudget.count), 0)).where(
                AutonomyBudget.class_ == class_,
                AutonomyBudget.agent == agent,
                AutonomyBudget.day >= start,
                AutonomyBudget.day <= anchor,
            )
        )
        scalar = result.scalar()
        if scalar:
            return int(scalar)

        # In-memory shim returns 0 for aggregates; sum manually.
        rows_result = await self._session.execute(select(AutonomyBudget))
        total = 0
        for row in rows_result.scalars().all():
            if row.class_ == class_ and row.agent == agent and start <= row.day <= anchor:
                total += row.count
        return total

    # ------------------------------------------------------------------
    # autonomy_limits
    # ------------------------------------------------------------------

    async def get_limit(self, class_: str) -> tuple[int, int | None] | None:
        """Return ``(daily_cap, weekly_cap)`` for ``class_`` or ``None`` if uncapped."""
        result = await self._session.execute(select(AutonomyLimit).where(AutonomyLimit.class_ == class_))
        for row in result.scalars().all():
            if row.class_ == class_:
                return (row.daily_cap, row.weekly_cap)
        return None

    async def set_limit(self, class_: str, daily_cap: int, weekly_cap: int | None) -> None:
        """Upsert the cap for ``class_``. Admin convenience — not on hot path."""
        stmt = (
            pg_insert(AutonomyLimit)
            .values(**{"class": class_}, daily_cap=daily_cap, weekly_cap=weekly_cap)
            .on_conflict_do_update(
                index_elements=["class"],
                set_={"daily_cap": daily_cap, "weekly_cap": weekly_cap},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        log.info(
            "autonomy_limit.set",
            class_=class_,
            daily_cap=daily_cap,
            weekly_cap=weekly_cap,
        )
