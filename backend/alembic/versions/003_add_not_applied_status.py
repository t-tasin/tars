"""003_add_not_applied_status

Add 'not_applied' value to the job_status PostgreSQL enum type.

Revision ID: 003_add_not_applied_status
Revises: 002_add_device_tokens
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_add_not_applied_status"
down_revision: str = "002_add_device_tokens"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'not_applied' AFTER 'applied';")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # A full recreate would be needed, but this is rarely worth doing.
    pass
