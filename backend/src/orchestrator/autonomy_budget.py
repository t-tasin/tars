"""Autonomy budget tracker (P4-13) — daily/weekly cap enforcement.

The orchestrator engine calls :meth:`AutonomyBudgetTracker.check_and_increment`
before executing any op declared with ``AutonomyClass.WRITE_SELF``. The
tracker increments the counter and returns ``BudgetCheck(allowed=True)``;
once the daily (or weekly, if configured) cap is hit, subsequent calls
return ``BudgetCheck(allowed=False, reason=...)`` and the engine routes
the op through ``ApprovalManager`` instead.

``READ`` and ``WRITE_LOCAL`` are pass-through (they never touch the DB).
``WRITE_WORLD`` and ``WRITE_INFRA`` must NOT be passed to this tracker —
they always go through ``ApprovalManager`` (HC-01). Calling the tracker
with those classes is a programming error and raises ``RuntimeError`` so
that engine bugs surface loudly in tests.

Engine integration is intentionally deferred to a follow-up PR; this
module only delivers the building blocks.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from shared.constants import AutonomyClass
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.autonomy import AutonomyBudgetRepository

log = structlog.get_logger()


type SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class BudgetCheck:
    """Result of a tracker call.

    ``allowed`` — the engine may execute the op when ``True``. ``count``
    is the post-increment counter (or 0 for pass-through classes).
    ``cap`` is the configured daily cap, or ``None`` when uncapped /
    pass-through. ``reason`` is set only when ``allowed`` is ``False``.
    """

    allowed: bool
    count: int
    cap: int | None
    reason: str | None = None


class AutonomyBudgetTracker:
    """Enforces daily/weekly autonomy caps for ``WRITE_SELF`` ops.

    ``session_factory`` should yield an ``AsyncSession`` as an async
    context manager (e.g. ``db.session.get_db_session``). ``clock`` is
    injected so tests can freeze time without monkey-patching.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def check_and_increment(
        self,
        autonomy_class: AutonomyClass,
        agent_name: str,
    ) -> BudgetCheck:
        """Check the budget for ``autonomy_class``+``agent_name`` and increment if allowed.

        Behavior matrix:

        * ``READ`` / ``WRITE_LOCAL`` — pass-through, no DB hit.
        * ``WRITE_SELF`` — look up the cap; if no row, allow uncapped;
          otherwise check daily count, then weekly count if configured;
          increment and return when both pass; deny otherwise.
        * ``WRITE_WORLD`` / ``WRITE_INFRA`` — defensive ``RuntimeError``;
          those go directly to ApprovalManager.
        """
        if autonomy_class in (AutonomyClass.READ, AutonomyClass.WRITE_LOCAL):
            log.debug(
                "autonomy_budget.passthrough",
                autonomy_class=autonomy_class.value,
                agent=agent_name,
            )
            return BudgetCheck(allowed=True, count=0, cap=None)

        if autonomy_class in (AutonomyClass.WRITE_WORLD, AutonomyClass.WRITE_INFRA):
            raise RuntimeError(
                "AutonomyBudgetTracker should not be called for "
                "WRITE_WORLD or WRITE_INFRA — those go to ApprovalManager",
            )

        if autonomy_class is not AutonomyClass.WRITE_SELF:
            # Future-proof: a new class added to the enum but not handled
            # here should fail closed rather than silently pass through.
            raise RuntimeError(f"Unhandled autonomy_class {autonomy_class!r}")

        return await self._check_write_self(agent_name)

    async def _check_write_self(self, agent_name: str) -> BudgetCheck:
        today = self._clock().date()
        class_value = AutonomyClass.WRITE_SELF.value

        async with self._session_factory() as session:
            repo = AutonomyBudgetRepository(session)
            limit = await repo.get_limit(class_value)

            if limit is None:
                # No row in autonomy_limits → uncapped pass-through. Still
                # increment so admins/observers can see the rate later.
                count = await repo.increment(today, class_value, agent_name)
                log.info(
                    "autonomy_budget.allowed_uncapped",
                    autonomy_class=class_value,
                    agent=agent_name,
                    count=count,
                    cap=None,
                )
                return BudgetCheck(allowed=True, count=count, cap=None)

            daily_cap, weekly_cap = limit

            current_daily = await repo.get_count(today, class_value, agent_name)
            if current_daily >= daily_cap:
                log.warning(
                    "autonomy_budget.denied",
                    autonomy_class=class_value,
                    agent=agent_name,
                    count=current_daily,
                    cap=daily_cap,
                    reason="daily cap exhausted",
                )
                return BudgetCheck(
                    allowed=False,
                    count=current_daily,
                    cap=daily_cap,
                    reason="daily cap exhausted",
                )

            if weekly_cap is not None:
                current_weekly = await repo.weekly_count(class_value, agent_name, today)
                if current_weekly >= weekly_cap:
                    log.warning(
                        "autonomy_budget.denied",
                        autonomy_class=class_value,
                        agent=agent_name,
                        count=current_weekly,
                        cap=weekly_cap,
                        reason="weekly cap exhausted",
                    )
                    return BudgetCheck(
                        allowed=False,
                        count=current_weekly,
                        cap=weekly_cap,
                        reason="weekly cap exhausted",
                    )

            new_count = await repo.increment(today, class_value, agent_name)
            log.info(
                "autonomy_budget.allowed",
                autonomy_class=class_value,
                agent=agent_name,
                count=new_count,
                cap=daily_cap,
            )
            return BudgetCheck(allowed=True, count=new_count, cap=daily_cap)
