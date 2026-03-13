"""006_add_workout_tables

Add workout tracking tables: workout_splits, workout_exercises,
workout_sessions, workout_logs. Includes workout_session_status ENUM.

Revision ID: 006_add_workout_tables
Revises: 005_migrate_plaid_to_teller
Create Date: 2026-03-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision: str = "006_add_workout_tables"
down_revision: str = "005_migrate_plaid_to_teller"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create ENUM type (raw SQL with duplicate-safe handling)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE workout_session_status AS ENUM ('pending','active','completed','skipped');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    workout_status = sa.Enum(
        "pending", "active", "completed", "skipped",
        name="workout_session_status", create_type=False,
    )

    # 1. workout_splits
    op.create_table(
        "workout_splits",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rotation_days", JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_splits_active", "workout_splits", ["active"],
        postgresql_where=sa.text("active = true"),
    )

    # 2. workout_exercises
    op.create_table(
        "workout_exercises",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("split_id", sa.Uuid(), sa.ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("day_name", sa.String(30), nullable=False),
        sa.Column("exercise_name", sa.String(100), nullable=False),
        sa.Column("target_sets", sa.Integer, nullable=False),
        sa.Column("target_reps", sa.Integer, nullable=False),
        sa.Column("current_weight", sa.Numeric(8, 2), nullable=False),
        sa.Column("weight_unit", sa.String(5), nullable=False, server_default=sa.text("'lbs'")),
        sa.Column("weight_increment", sa.Numeric(8, 2), nullable=False, server_default=sa.text("2.5")),
        sa.Column("order_index", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_exercises_split_day", "workout_exercises", ["split_id", "day_name"])

    # 3. workout_sessions
    op.create_table(
        "workout_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("split_id", sa.Uuid(), sa.ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("day_name", sa.String(30), nullable=False),
        sa.Column("rotation_index", sa.Integer, nullable=False),
        sa.Column("scheduled_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", workout_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("skip_reason", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_sessions_status", "workout_sessions", ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_sessions_date", "workout_sessions", [sa.text("created_at DESC")])
    op.create_index("idx_sessions_split", "workout_sessions", ["split_id", sa.text("created_at DESC")])

    # 4. workout_logs
    op.create_table(
        "workout_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("workout_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("exercise_id", sa.Uuid(), sa.ForeignKey("workout_exercises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("set_number", sa.Integer, nullable=False),
        sa.Column("target_reps", sa.Integer, nullable=False),
        sa.Column("target_weight", sa.Numeric(8, 2), nullable=False),
        sa.Column("actual_reps", sa.Integer, nullable=True),
        sa.Column("actual_weight", sa.Numeric(8, 2), nullable=True),
        sa.Column("logged_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_logs_session", "workout_logs", ["session_id"])
    op.create_index("idx_logs_exercise_date", "workout_logs", ["exercise_id", sa.text("created_at DESC")])

    # Reuse trigger_set_updated_at() from 001_initial_schema
    for table in ("workout_splits", "workout_exercises", "workout_sessions", "workout_logs"):
        op.execute(f"""
            CREATE TRIGGER set_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION trigger_set_updated_at()
        """)


def downgrade() -> None:
    for table in ("workout_logs", "workout_sessions", "workout_exercises", "workout_splits"):
        op.execute(f"DROP TRIGGER IF EXISTS set_updated_at ON {table}")
        op.drop_table(table)

    op.execute("DROP TYPE IF EXISTS workout_session_status")
