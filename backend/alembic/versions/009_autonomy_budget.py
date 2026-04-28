"""009_autonomy_budget

Phase 4 autonomy budget tracking (P4-13 / P4-14). Creates two tables:

- ``autonomy_budget`` — per-day, per-class, per-agent counters incremented
  by the orchestrator before executing a budgeted op (today: ``WRITE_SELF``).
- ``autonomy_limits`` — class -> daily/weekly cap mapping. ``WRITE_SELF`` is
  seeded with 30/day, 180/week per ``docs/autonomy_budget.md``. Other
  classes are uncapped (no row) — pass-through in the tracker. ``READ`` and
  ``WRITE_LOCAL`` never touch this table; ``WRITE_WORLD`` / ``WRITE_INFRA``
  go directly to ``ApprovalManager``.

Revision ID: 009_autonomy_budget
Revises: 008_wiki_tables
Create Date: 2026-04-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "009_autonomy_budget"
down_revision: str = "008_wiki_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. autonomy_budget — daily/weekly counters per (class, agent).
    # ---------------------------------------------------------------
    op.create_table(
        "autonomy_budget",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("day", "class", "agent", name="uq_autonomy_budget_day_class_agent"),
    )
    op.create_index(
        "idx_autonomy_budget_day_class",
        "autonomy_budget",
        ["day", "class"],
    )

    # ---------------------------------------------------------------
    # 2. autonomy_limits — class -> daily / weekly cap. Seed WRITE_SELF.
    # ---------------------------------------------------------------
    op.create_table(
        "autonomy_limits",
        sa.Column("class", sa.Text(), primary_key=True),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("weekly_cap", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        INSERT INTO autonomy_limits (class, daily_cap, weekly_cap)
        VALUES ('write_self', 30, 180)
        """
    )


def downgrade() -> None:
    op.drop_table("autonomy_limits")
    op.drop_index("idx_autonomy_budget_day_class", table_name="autonomy_budget")
    op.drop_table("autonomy_budget")
