"""002_add_device_tokens

Add device_tokens table for APNs push notification token storage.

Revision ID: 002_add_device_tokens
Revises: 001_initial_schema
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "002_add_device_tokens"
down_revision: str = "001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("token", sa.String(255), nullable=False),
        sa.Column("device_name", sa.String(100), nullable=True),
        sa.Column("platform", sa.String(20), nullable=False, server_default=sa.text("'ios'")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index(
        "idx_device_tokens_active",
        "device_tokens",
        ["active"],
        postgresql_where=sa.text("active = true"),
    )

    # Apply the updated_at trigger (function created in 001_initial_schema)
    op.execute("""
        CREATE TRIGGER set_updated_at
        BEFORE UPDATE ON device_tokens
        FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS set_updated_at ON device_tokens;")
    op.drop_index("idx_device_tokens_active", table_name="device_tokens")
    op.drop_table("device_tokens")
