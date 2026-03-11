"""004_add_job_application_columns

Add apply_method and fit_analysis columns to job_listings for the
job application pipeline.

Revision ID: 004_add_job_application_columns
Revises: 003_add_not_applied_status
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "004_add_job_application_columns"
down_revision: str = "003_add_not_applied_status"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("job_listings", sa.Column("apply_method", sa.String(30), nullable=True))
    op.add_column("job_listings", sa.Column("fit_analysis", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_listings", "fit_analysis")
    op.drop_column("job_listings", "apply_method")
