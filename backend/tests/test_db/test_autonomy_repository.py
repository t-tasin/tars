"""Tests for ``db.repositories.autonomy.AutonomyBudgetRepository``.

Mixes in-memory shim assertions (always run) with PG-only checks gated
behind ``requires_pg`` — UPSERT semantics, sum aggregates, and the
``UNIQUE(day, class, agent)`` constraint can only be exercised against a
real PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from db.models import AutonomyBudget
from db.repositories.autonomy import AutonomyBudgetRepository

_HAS_PG = bool(os.environ.get("TEST_DATABASE_URL", ""))
requires_pg = pytest.mark.skipif(not _HAS_PG, reason="Requires real PostgreSQL")


@pytest.mark.asyncio
async def test_get_count_returns_zero_when_missing(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    assert await repo.get_count(date(2026, 4, 28), "write_self", "briefing") == 0


@pytest.mark.asyncio
async def test_get_limit_returns_none_when_missing(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    assert await repo.get_limit("write_self") is None


@requires_pg
@pytest.mark.asyncio
async def test_increment_inserts_row_with_count_one(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    new_count = await repo.increment(date(2026, 4, 28), "write_self", "briefing")
    assert new_count == 1
    assert await repo.get_count(date(2026, 4, 28), "write_self", "briefing") == 1


@requires_pg
@pytest.mark.asyncio
async def test_increment_existing_row_bumps_count(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    day = date(2026, 4, 28)
    await repo.increment(day, "write_self", "briefing")
    await repo.increment(day, "write_self", "briefing")
    new_count = await repo.increment(day, "write_self", "briefing")
    assert new_count == 3
    assert await repo.get_count(day, "write_self", "briefing") == 3


@requires_pg
@pytest.mark.asyncio
async def test_increment_isolated_per_agent(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    day = date(2026, 4, 28)
    await repo.increment(day, "write_self", "agent_a")
    await repo.increment(day, "write_self", "agent_a")
    await repo.increment(day, "write_self", "agent_b")
    assert await repo.get_count(day, "write_self", "agent_a") == 2
    assert await repo.get_count(day, "write_self", "agent_b") == 1


@requires_pg
@pytest.mark.asyncio
async def test_weekly_count_rolls_seven_days(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    days = [date(2026, 4, d) for d in (22, 23, 24, 25, 26, 27, 28)]
    for d in days:
        await repo.increment(d, "write_self", "briefing")
        await repo.increment(d, "write_self", "briefing")
    # 7 days * 2 increments = 14
    total = await repo.weekly_count("write_self", "briefing", date(2026, 4, 28))
    assert total == 14

    # Day-7 anchor moves the window forward by one — the 04-22 row drops out.
    later = await repo.weekly_count("write_self", "briefing", date(2026, 4, 29))
    assert later == 12


@requires_pg
@pytest.mark.asyncio
async def test_set_and_get_limit(test_db) -> None:
    repo = AutonomyBudgetRepository(test_db)
    await repo.set_limit("write_self", daily_cap=50, weekly_cap=200)
    assert await repo.get_limit("write_self") == (50, 200)
    # Upsert path — second call updates rather than raises.
    await repo.set_limit("write_self", daily_cap=10, weekly_cap=None)
    assert await repo.get_limit("write_self") == (10, None)


@requires_pg
@pytest.mark.asyncio
async def test_unique_constraint_enforced(test_db) -> None:
    """Real PG must enforce UNIQUE (day, class, agent)."""
    from sqlalchemy.exc import IntegrityError

    a = AutonomyBudget(
        day=date(2026, 4, 28),
        class_="write_self",
        agent="briefing",
        count=1,
    )
    b = AutonomyBudget(
        day=date(2026, 4, 28),
        class_="write_self",
        agent="briefing",
        count=99,
    )
    test_db.add(a)
    await test_db.flush()
    test_db.add(b)
    with pytest.raises(IntegrityError):
        await test_db.flush()


@requires_pg
@pytest.mark.asyncio
async def test_increment_independent_per_day(test_db) -> None:
    """Two distinct days must produce independent counter rows."""
    repo = AutonomyBudgetRepository(test_db)
    await repo.increment(date(2026, 4, 27), "write_self", "briefing")
    await repo.increment(date(2026, 4, 28), "write_self", "briefing")
    assert await repo.get_count(date(2026, 4, 27), "write_self", "briefing") == 1
    assert await repo.get_count(date(2026, 4, 28), "write_self", "briefing") == 1
