"""007_add_world_state_partitioned

Phase 3.5 sensor pipeline. Creates ``world_state`` as a monthly
RANGE-partitioned table on ``ts``.

Native Postgres declarative partitioning is used here instead of
``pg_partman`` because the prod/dev/CI Postgres image is
``postgres:16-alpine`` and partman is not packaged. Future migration
will introduce partman once a custom DB image lands; until then a cron
job (P4) rotates partitions month-over-month.

Revision ID: 007_add_world_state_partitioned
Revises: 006_add_workout_tables
Create Date: 2026-04-25
"""

from __future__ import annotations

from datetime import date

from alembic import op

revision: str = "007_add_world_state_partitioned"
down_revision: str = "006_add_workout_tables"
branch_labels: str | None = None
depends_on: str | None = None


# Pre-create monthly partitions covering Jan 2026 .. Dec 2027 (24 months)
# plus a default catch-all. Future migrations will extend the range; the
# default partition prevents inserts from failing if a sensor publishes
# with an out-of-range ``ts``.
_RANGE_START = date(2026, 1, 1)
_RANGE_MONTHS = 24


def _next_month(d: date) -> date:
    year = d.year + (1 if d.month == 12 else 0)
    month = 1 if d.month == 12 else d.month + 1
    return date(year, month, 1)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE world_state (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            sensor VARCHAR(50) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
        """
    )
    op.execute("CREATE INDEX idx_world_state_sensor_ts ON world_state (sensor, ts DESC);")

    cur = _RANGE_START
    for _ in range(_RANGE_MONTHS):
        nxt = _next_month(cur)
        partition = f"world_state_{cur.year}_{cur.month:02d}"
        op.execute(
            f"""
            CREATE TABLE {partition} PARTITION OF world_state
            FOR VALUES FROM ('{cur.isoformat()}') TO ('{nxt.isoformat()}');
            """
        )
        cur = nxt

    op.execute("CREATE TABLE world_state_default PARTITION OF world_state DEFAULT;")


def downgrade() -> None:
    cur = _RANGE_START
    for _ in range(_RANGE_MONTHS):
        partition = f"world_state_{cur.year}_{cur.month:02d}"
        op.execute(f"DROP TABLE IF EXISTS {partition}")
        cur = _next_month(cur)
    op.execute("DROP TABLE IF EXISTS world_state_default")
    op.execute("DROP TABLE IF EXISTS world_state")
