"""005_migrate_plaid_to_teller

Migrate finance tables from Plaid to Teller.io:
- Rename plaid_* columns to teller_* in transactions
- Add counterparty, transaction_type, description columns to transactions
- Create budgets table for budget tracking
- Add top_merchants, vs_previous_period, subscription_charges to finance_summaries
- Update indexes for new column names and query patterns

Revision ID: 005_migrate_plaid_to_teller
Revises: 004_add_job_application_columns
Create Date: 2026-03-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_migrate_plaid_to_teller"
down_revision: str = "004_add_job_application_columns"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ==================================================================
    # 1. Rename Plaid columns → Teller in transactions
    # ==================================================================
    op.alter_column("transactions", "plaid_transaction_id", new_column_name="teller_transaction_id")
    op.alter_column("transactions", "plaid_account_id", new_column_name="teller_account_id")

    # ==================================================================
    # 2. Add new columns to transactions
    # ==================================================================
    op.add_column("transactions", sa.Column("counterparty_name", sa.String(255), nullable=True))
    op.add_column("transactions", sa.Column("counterparty_type", sa.String(50), nullable=True))
    op.add_column("transactions", sa.Column("transaction_type", sa.String(50), nullable=True))
    op.add_column("transactions", sa.Column("description", sa.Text(), nullable=True))

    # ==================================================================
    # 3. Update indexes on transactions
    # ==================================================================
    # Drop old unique constraint on plaid_transaction_id (auto-generated name)
    op.drop_constraint("transactions_plaid_transaction_id_key", "transactions", type_="unique")
    # Create new unique index on teller_transaction_id
    op.create_index("idx_txns_teller_id", "transactions", ["teller_transaction_id"], unique=True)
    # New indexes for query patterns
    op.create_index("idx_txns_counterparty", "transactions", ["counterparty_name"])
    op.create_index("idx_txns_type", "transactions", ["transaction_type"])
    op.create_index(
        "idx_txns_recurring_date",
        "transactions",
        ["is_recurring", "transaction_date"],
        postgresql_where=sa.text("is_recurring = true"),
    )

    # ==================================================================
    # 4. Create budgets table
    # ==================================================================
    op.create_table(
        "budgets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_budgets_category",
        "budgets",
        ["category"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )

    # Apply updated_at trigger to budgets
    op.execute("""
        CREATE TRIGGER set_updated_at
        BEFORE UPDATE ON budgets
        FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at()
    """)

    # ==================================================================
    # 5. Add columns to finance_summaries
    # ==================================================================
    op.add_column(
        "finance_summaries", sa.Column("top_merchants", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False)
    )
    op.add_column(
        "finance_summaries",
        sa.Column("vs_previous_period", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "finance_summaries",
        sa.Column("subscription_charges", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    # ==================================================================
    # 5. Remove finance_summaries columns
    # ==================================================================
    op.drop_column("finance_summaries", "subscription_charges")
    op.drop_column("finance_summaries", "vs_previous_period")
    op.drop_column("finance_summaries", "top_merchants")

    # ==================================================================
    # 4. Drop budgets table (trigger drops with table)
    # ==================================================================
    op.execute("DROP TRIGGER IF EXISTS set_updated_at ON budgets")
    op.drop_index("idx_budgets_category", table_name="budgets")
    op.drop_table("budgets")

    # ==================================================================
    # 3. Drop new indexes on transactions
    # ==================================================================
    op.drop_index("idx_txns_recurring_date", table_name="transactions")
    op.drop_index("idx_txns_type", table_name="transactions")
    op.drop_index("idx_txns_counterparty", table_name="transactions")
    op.drop_index("idx_txns_teller_id", table_name="transactions")

    # ==================================================================
    # 2. Remove new columns from transactions
    # ==================================================================
    op.drop_column("transactions", "description")
    op.drop_column("transactions", "transaction_type")
    op.drop_column("transactions", "counterparty_type")
    op.drop_column("transactions", "counterparty_name")

    # ==================================================================
    # 1. Rename Teller columns → Plaid in transactions
    # ==================================================================
    op.alter_column("transactions", "teller_transaction_id", new_column_name="plaid_transaction_id")
    op.alter_column("transactions", "teller_account_id", new_column_name="plaid_account_id")
    # Restore original unique constraint (must be after column rename)
    op.create_unique_constraint("transactions_plaid_transaction_id_key", "transactions", ["plaid_transaction_id"])
