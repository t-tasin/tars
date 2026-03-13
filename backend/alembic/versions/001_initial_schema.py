"""001_initial_schema

Create all 20 T.A.R.S. tables, custom enum types, extensions,
indexes, and the updated_at trigger infrastructure.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# Custom ENUM types (must exist before any table references them)
# ---------------------------------------------------------------------------
task_status = sa.Enum(
    "pending", "running", "completed", "failed", "cancelled",
    name="task_status", create_type=False,
)
task_priority = sa.Enum(
    "critical", "high", "normal", "low",
    name="task_priority", create_type=False,
)
approval_status = sa.Enum(
    "pending", "approved", "rejected", "edited", "expired", "executed",
    name="approval_status", create_type=False,
)
risk_tier = sa.Enum(
    "tier1_autonomous", "tier2_approval", "tier3_escalation",
    name="risk_tier", create_type=False,
)
email_tier = sa.Enum(
    "urgent", "actionable", "informational", "noise",
    name="email_tier", create_type=False,
)
job_status = sa.Enum(
    "new", "saved", "applying", "applied", "interview",
    "offer", "rejected", "skipped", "expired",
    name="job_status", create_type=False,
)
health_status = sa.Enum(
    "green", "yellow", "red", "unknown",
    name="health_status", create_type=False,
)


def upgrade() -> None:
    # -- Extensions ---------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # -- Enum types ---------------------------------------------------------
    task_status.create(op.get_bind(), checkfirst=True)
    task_priority.create(op.get_bind(), checkfirst=True)
    approval_status.create(op.get_bind(), checkfirst=True)
    risk_tier.create(op.get_bind(), checkfirst=True)
    email_tier.create(op.get_bind(), checkfirst=True)
    job_status.create(op.get_bind(), checkfirst=True)
    health_status.create(op.get_bind(), checkfirst=True)

    # ======================================================================
    # 1. conversations
    # ======================================================================
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ======================================================================
    # 2. messages
    # ======================================================================
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content_type", sa.String(20), server_default=sa.text("'text'"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_messages_conversation", "messages", ["conversation_id", "created_at"])
    op.create_index("idx_messages_created", "messages", ["created_at"])

    # ======================================================================
    # 3. agent_tasks
    # ======================================================================
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("model_used", sa.String(30), nullable=True),
        sa.Column("status", task_status, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("priority", task_priority, server_default=sa.text("'normal'"), nullable=False),
        sa.Column("input_payload", JSONB, nullable=False),
        sa.Column("output_payload", JSONB, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("node", sa.String(10), server_default=sa.text("'node1'"), nullable=True),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agent_tasks_status", "agent_tasks", ["status", "priority"])
    op.create_index("idx_agent_tasks_agent_type", "agent_tasks", ["agent_type", "created_at"])
    op.create_index("idx_agent_tasks_created", "agent_tasks", ["created_at"])

    # ======================================================================
    # 4. approvals
    # ======================================================================
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("risk_tier", risk_tier, nullable=False),
        sa.Column("status", approval_status, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("preview_payload", JSONB, nullable=False),
        sa.Column("edited_payload", JSONB, nullable=True),
        sa.Column("decision_source", sa.String(20), nullable=True),
        sa.Column("decided_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
    )
    op.create_index(
        "idx_approvals_status", "approvals", ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_approvals_expires", "approvals", ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ======================================================================
    # 5. contacts
    # ======================================================================
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("apple_contact_id", sa.String(255), unique=True, nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("email_addresses", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("phone_numbers", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("organization", sa.String(255), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column("relationship", sa.String(50), nullable=True),
        sa.Column("priority_hint", sa.String(20), server_default=sa.text("'normal'"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), server_default=sa.text("'apple_contacts'"), nullable=False),
        sa.Column("synced_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "CREATE INDEX idx_contacts_emails ON contacts USING GIN (email_addresses jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX idx_contacts_name ON contacts USING GIN (full_name gin_trgm_ops)"
    )

    # ======================================================================
    # 6. email_classifications
    # ======================================================================
    op.create_table(
        "email_classifications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("gmail_account", sa.String(10), nullable=False),
        sa.Column("gmail_message_id", sa.String(255), unique=True, nullable=False),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("classified_tier", email_tier, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_used", sa.String(30), nullable=False),
        sa.Column("user_correction", email_tier, nullable=True),
        sa.Column("correction_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("received_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
    )
    op.create_index("idx_email_class_account", "email_classifications", ["gmail_account", "received_at"])
    op.create_index("idx_email_class_from", "email_classifications", ["from_address"])
    op.create_index(
        "idx_email_class_corrections", "email_classifications", ["user_correction"],
        postgresql_where=sa.text("user_correction IS NOT NULL"),
    )

    # ======================================================================
    # 7. briefings
    # ======================================================================
    op.create_table(
        "briefings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("briefing_type", sa.String(20), nullable=False),
        sa.Column("briefing_date", sa.Date(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("delivered_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delivered_via", sa.String(20), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("briefing_date", "briefing_type", name="uq_briefings_date_type"),
    )

    # ======================================================================
    # 8. config
    # ======================================================================
    op.create_table(
        "config",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("namespace", sa.String(50), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(50), server_default=sa.text("'system'"), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "key", name="uq_config_namespace_key"),
    )
    op.create_index("idx_config_namespace", "config", ["namespace"])

    # ======================================================================
    # 9. agent_outputs
    # ======================================================================
    op.create_table(
        "agent_outputs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("output_type", sa.String(50), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("file_paths", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_agent_outputs_task", "agent_outputs", ["task_id"])
    op.create_index("idx_agent_outputs_type", "agent_outputs", ["output_type", "created_at"])

    # ======================================================================
    # 10. system_health_log
    # ======================================================================
    op.create_table(
        "system_health_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("target", sa.String(50), nullable=False),
        sa.Column("status", health_status, nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column("checked_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_health_log_target", "system_health_log", ["target", "checked_at"])

    # ======================================================================
    # 11. feedback_log
    # ======================================================================
    op.create_table(
        "feedback_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("feedback_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("original_value", JSONB, nullable=True),
        sa.Column("corrected_value", JSONB, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_feedback_type", "feedback_log", ["feedback_type", "created_at"])

    # ======================================================================
    # 12. job_listings
    # ======================================================================
    op.create_table(
        "job_listings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("salary_range", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", job_status, server_default=sa.text("'new'"), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column("match_reasons", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("concerns", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("flash_screen", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("pro_evaluated", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("claude_reviewed", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("notion_page_id", sa.String(255), nullable=True),
        sa.Column("applied_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("follow_up_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scraped_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_job_listings_source_external"),
    )
    op.create_index("idx_jobs_status", "job_listings", ["status", "match_score"])
    op.create_index("idx_jobs_scraped", "job_listings", ["scraped_at"])
    op.create_index("idx_jobs_company", "job_listings", ["company"])

    # ======================================================================
    # 13. job_applications
    # ======================================================================
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_listing_id", sa.Uuid(), nullable=False),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("resume_version", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), server_default=sa.text("'draft'"), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_listing_id"], ["job_listings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
    )
    op.create_index("idx_job_apps_listing", "job_applications", ["job_listing_id"])

    # ======================================================================
    # 14. wardrobe_items
    # ======================================================================
    op.create_table(
        "wardrobe_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("item_type", sa.String(30), nullable=False),
        sa.Column("sub_type", sa.String(50), nullable=True),
        sa.Column("color", sa.String(50), nullable=False),
        sa.Column("pattern", sa.String(30), server_default=sa.text("'solid'"), nullable=True),
        sa.Column("seasons", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("formality", sa.String(20), nullable=False),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("image_hash", sa.String(64), nullable=True),
        sa.Column("last_worn", sa.Date(), nullable=True),
        sa.Column("wear_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("gemini_raw", JSONB, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_wardrobe_type", "wardrobe_items", ["item_type", "active"],
        postgresql_where=sa.text("active = true"),
    )
    op.create_index(
        "idx_wardrobe_formality", "wardrobe_items", ["formality"],
        postgresql_where=sa.text("active = true"),
    )

    # ======================================================================
    # 15. wardrobe_outfits
    # ======================================================================
    op.create_table(
        "wardrobe_outfits",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("outfit_date", sa.Date(), nullable=False),
        sa.Column("items", JSONB, nullable=False),
        sa.Column("weather_context", JSONB, nullable=True),
        sa.Column("calendar_context", JSONB, nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("was_worn", sa.Boolean(), nullable=True),
        sa.Column("user_feedback", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_outfits_date", "wardrobe_outfits", ["outfit_date"])

    # ======================================================================
    # 16. model_usage
    # ======================================================================
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("model", sa.String(30), nullable=False),
        sa.Column("agent_type", sa.String(50), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_output", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(10, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("error_type", sa.String(50), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
    )
    op.create_index("idx_usage_model", "model_usage", ["model", "created_at"])

    # ======================================================================
    # 17. transactions
    # ======================================================================
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("plaid_transaction_id", sa.String(255), unique=True, nullable=False),
        sa.Column("plaid_account_id", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(100), nullable=True),
        sa.Column("merchant_name", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("custom_category", sa.String(100), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column("pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_txns_date", "transactions", ["transaction_date"])
    op.create_index("idx_txns_merchant", "transactions", ["merchant_name"])
    op.create_index("idx_txns_category", "transactions", ["category", "transaction_date"])
    op.create_index(
        "idx_txns_recurring", "transactions", ["is_recurring"],
        postgresql_where=sa.text("is_recurring = true"),
    )

    # ======================================================================
    # 18. finance_summaries
    # ======================================================================
    op.create_table(
        "finance_summaries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("total_spent", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_income", sa.Numeric(12, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("by_category", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("alerts", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_type", "period_start", name="uq_finance_summaries_period"),
    )
    op.create_index("idx_finance_period", "finance_summaries", ["period_type", "period_start"])

    # ======================================================================
    # 19. health_data
    # ======================================================================
    op.create_table(
        "health_data",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("data_type", sa.String(30), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recorded_date", sa.Date(), nullable=False),
        sa.Column("start_time", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("end_time", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source", sa.String(30), server_default=sa.text("'healthkit'"), nullable=True),
        sa.Column("synced_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_health_type_date", "health_data", ["data_type", "recorded_date"])
    op.create_index("idx_health_date", "health_data", ["recorded_date"])

    # ======================================================================
    # 20. audit_log
    # ======================================================================
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(30), nullable=False),
        sa.Column("target", sa.String(100), nullable=True),
        sa.Column("details", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_action", "audit_log", ["action_type", "created_at"])
    op.create_index("idx_audit_created", "audit_log", ["created_at"])

    # ======================================================================
    # Trigger function: auto-update updated_at on row modification
    # ======================================================================
    op.execute("""
        CREATE OR REPLACE FUNCTION trigger_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    # Apply trigger to every table that has an updated_at column
    for table in (
        "conversations",
        "agent_tasks",
        "approvals",
        "config",
        "contacts",
        "job_listings",
        "job_applications",
        "wardrobe_items",
    ):
        op.execute(f"""
            CREATE TRIGGER set_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION trigger_set_updated_at()
        """)


def downgrade() -> None:
    # Drop triggers first
    for table in (
        "wardrobe_items",
        "job_applications",
        "job_listings",
        "contacts",
        "config",
        "approvals",
        "agent_tasks",
        "conversations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS set_updated_at ON {table}")

    op.execute("DROP FUNCTION IF EXISTS trigger_set_updated_at()")

    # Drop tables in reverse dependency order
    op.drop_table("audit_log")
    op.drop_table("health_data")
    op.drop_table("finance_summaries")
    op.drop_table("transactions")
    op.drop_table("model_usage")
    op.drop_table("wardrobe_outfits")
    op.drop_table("wardrobe_items")
    op.drop_table("job_applications")
    op.drop_table("job_listings")
    op.drop_table("feedback_log")
    op.drop_table("system_health_log")
    op.drop_table("agent_outputs")
    op.drop_table("config")
    op.drop_table("briefings")
    op.drop_table("email_classifications")
    op.drop_table("contacts")
    op.drop_table("approvals")
    op.drop_table("agent_tasks")
    op.drop_table("messages")
    op.drop_table("conversations")

    # Drop enum types
    health_status.drop(op.get_bind(), checkfirst=True)
    job_status.drop(op.get_bind(), checkfirst=True)
    email_tier.drop(op.get_bind(), checkfirst=True)
    risk_tier.drop(op.get_bind(), checkfirst=True)
    approval_status.drop(op.get_bind(), checkfirst=True)
    task_priority.drop(op.get_bind(), checkfirst=True)
    task_status.drop(op.get_bind(), checkfirst=True)

    # Drop extensions
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
