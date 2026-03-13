"""SQLAlchemy 2.0 ORM models for T.A.R.S. database."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from shared.constants import (
    ApprovalStatus,
    EmailTier,
    HealthStatus,
    JobStatus,
    RiskTier,
    TaskPriority,
    TaskStatus,
    WorkoutSessionStatus,
)

# Reusable SQLAlchemy Enum types with create_type=False so that
# migrations (not ORM metadata) are the sole owner of PG enum DDL.
# values_callable ensures SQLAlchemy sends lowercase VALUES ("pending")
# instead of uppercase member NAMES ("PENDING") to PostgreSQL.
_enum_values = staticmethod(lambda e: [m.value for m in e])
_task_status_enum = Enum(TaskStatus, name="task_status", create_type=False, values_callable=_enum_values)
_task_priority_enum = Enum(TaskPriority, name="task_priority", create_type=False, values_callable=_enum_values)
_approval_status_enum = Enum(ApprovalStatus, name="approval_status", create_type=False, values_callable=_enum_values)
_risk_tier_enum = Enum(RiskTier, name="risk_tier", create_type=False, values_callable=_enum_values)
_email_tier_enum = Enum(EmailTier, name="email_tier", create_type=False, values_callable=_enum_values)
_job_status_enum = Enum(JobStatus, name="job_status", create_type=False, values_callable=_enum_values)
_health_status_enum = Enum(HealthStatus, name="health_status", create_type=False, values_callable=_enum_values)
_workout_session_status_enum = Enum(WorkoutSessionStatus, name="workout_session_status", create_type=False, values_callable=_enum_values)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# Shorthand for the UUID PK column used on every table.
def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        primary_key=True,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


# ---------------------------------------------------------------------------
# 1. Conversation
# ---------------------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # relationships
    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")


# ---------------------------------------------------------------------------
# 2. Message
# ---------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_conversation", "conversation_id", "created_at"),
        Index("idx_messages_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'text'"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()

    # relationships
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# 3. AgentTask
# ---------------------------------------------------------------------------

class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("idx_agent_tasks_status", "status", "priority"),
        Index("idx_agent_tasks_agent_type", "agent_type", "created_at"),
        Index("idx_agent_tasks_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[TaskStatus] = mapped_column(_task_status_enum, default=TaskStatus.PENDING, server_default=text("'pending'"))
    priority: Mapped[TaskPriority] = mapped_column(_task_priority_enum, default=TaskPriority.NORMAL, server_default=text("'normal'"))
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    node: Mapped[str | None] = mapped_column(String(10), server_default=text("'node1'"))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    approvals: Mapped[list[Approval]] = relationship(back_populates="task", cascade="all, delete-orphan")
    outputs: Mapped[list[AgentOutput]] = relationship(back_populates="task", cascade="all, delete-orphan")
    usage_records: Mapped[list[ModelUsage]] = relationship(back_populates="task")


# ---------------------------------------------------------------------------
# 4. Approval
# ---------------------------------------------------------------------------

class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("idx_approvals_status", "status", postgresql_where=text("status = 'pending'")),
        Index("idx_approvals_expires", "expires_at", postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_tier: Mapped[RiskTier] = mapped_column(_risk_tier_enum, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(_approval_status_enum, default=ApprovalStatus.PENDING, server_default=text("'pending'"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    preview_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    edited_payload: Mapped[dict | None] = mapped_column(JSONB)
    decision_source: Mapped[str | None] = mapped_column(String(20))
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    task: Mapped[AgentTask | None] = relationship(back_populates="approvals")
    job_applications: Mapped[list[JobApplication]] = relationship(back_populates="approval")


# ---------------------------------------------------------------------------
# 5. Contact (defined before EmailClassification for FK)
# ---------------------------------------------------------------------------

class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    apple_contact_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    phone_numbers: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    organization: Mapped[str | None] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(255))
    relationship_type: Mapped[str | None] = mapped_column("relationship", String(50))
    priority_hint: Mapped[str | None] = mapped_column(String(20), server_default=text("'normal'"))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'apple_contacts'"))
    synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    email_classifications: Mapped[list[EmailClassification]] = relationship(back_populates="contact")


# ---------------------------------------------------------------------------
# 6. EmailClassification
# ---------------------------------------------------------------------------

class EmailClassification(Base):
    __tablename__ = "email_classifications"
    __table_args__ = (
        Index("idx_email_class_account", "gmail_account", "received_at"),
        Index("idx_email_class_from", "from_address"),
        Index("idx_email_class_corrections", "user_correction", postgresql_where=text("user_correction IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    gmail_account: Mapped[str] = mapped_column(String(10), nullable=False)
    gmail_message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(Text)
    classified_tier: Mapped[EmailTier] = mapped_column(_email_tier_enum, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    model_used: Mapped[str] = mapped_column(String(30), nullable=False)
    user_correction: Mapped[EmailTier | None] = mapped_column(_email_tier_enum)
    correction_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    received_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    # relationships
    contact: Mapped[Contact | None] = relationship(back_populates="email_classifications")


# ---------------------------------------------------------------------------
# 7. Briefing
# ---------------------------------------------------------------------------

class Briefing(Base):
    __tablename__ = "briefings"
    __table_args__ = (
        UniqueConstraint("briefing_date", "briefing_type", name="uq_briefings_date_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    briefing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    delivered_via: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 8. Config
# ---------------------------------------------------------------------------

class Config(Base):
    __tablename__ = "config"
    __table_args__ = (
        UniqueConstraint("namespace", "key", name="uq_config_namespace_key"),
        Index("idx_config_namespace", "namespace"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    namespace: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), server_default=text("'system'"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------------
# 9. AgentOutput
# ---------------------------------------------------------------------------

class AgentOutput(Base):
    __tablename__ = "agent_outputs"
    __table_args__ = (
        Index("idx_agent_outputs_task", "task_id"),
        Index("idx_agent_outputs_type", "output_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False)
    output_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_paths: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()

    # relationships
    task: Mapped[AgentTask] = relationship(back_populates="outputs")


# ---------------------------------------------------------------------------
# 10. SystemHealthLog
# ---------------------------------------------------------------------------

class SystemHealthLog(Base):
    __tablename__ = "system_health_log"
    __table_args__ = (
        Index("idx_health_log_target", "target", "checked_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    target: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[HealthStatus] = mapped_column(_health_status_enum, nullable=False)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


# ---------------------------------------------------------------------------
# 11. FeedbackLog
# ---------------------------------------------------------------------------

class FeedbackLog(Base):
    __tablename__ = "feedback_log"
    __table_args__ = (
        Index("idx_feedback_type", "feedback_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    original_value: Mapped[dict | None] = mapped_column(JSONB)
    corrected_value: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 12. JobListing
# ---------------------------------------------------------------------------

class JobListing(Base):
    __tablename__ = "job_listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_listings_source_external"),
        Index("idx_jobs_status", "status", "match_score"),
        Index("idx_jobs_scraped", "scraped_at"),
        Index("idx_jobs_company", "company"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    salary_range: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(_job_status_enum, default=JobStatus.NEW, server_default=text("'new'"))
    match_score: Mapped[int | None] = mapped_column(Integer)
    match_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    concerns: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    flash_screen: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    pro_evaluated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    claude_reviewed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    apply_method: Mapped[str | None] = mapped_column(String(30))
    fit_analysis: Mapped[str | None] = mapped_column(Text)
    notion_page_id: Mapped[str | None] = mapped_column(String(255))
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    follow_up_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    applications: Mapped[list[JobApplication]] = relationship(back_populates="job_listing", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 13. JobApplication
# ---------------------------------------------------------------------------

class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        Index("idx_job_apps_listing", "job_listing_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_listings.id", ondelete="CASCADE"), nullable=False)
    cover_letter: Mapped[str | None] = mapped_column(Text)
    resume_version: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(30), server_default=text("'draft'"))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    job_listing: Mapped[JobListing] = relationship(back_populates="applications")
    approval: Mapped[Approval | None] = relationship(back_populates="job_applications")


# ---------------------------------------------------------------------------
# 14. WardrobeItem
# ---------------------------------------------------------------------------

class WardrobeItem(Base):
    __tablename__ = "wardrobe_items"
    __table_args__ = (
        Index("idx_wardrobe_type", "item_type", "active", postgresql_where=text("active = true")),
        Index("idx_wardrobe_formality", "formality", postgresql_where=text("active = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sub_type: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(30), server_default=text("'solid'"))
    seasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    formality: Mapped[str] = mapped_column(String(20), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    image_hash: Mapped[str | None] = mapped_column(String(64))
    last_worn: Mapped[date | None] = mapped_column(Date)
    wear_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    gemini_raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------------
# 15. WardrobeOutfit
# ---------------------------------------------------------------------------

class WardrobeOutfit(Base):
    __tablename__ = "wardrobe_outfits"
    __table_args__ = (
        Index("idx_outfits_date", "outfit_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    outfit_date: Mapped[date] = mapped_column(Date, nullable=False)
    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    weather_context: Mapped[dict | None] = mapped_column(JSONB)
    calendar_context: Mapped[dict | None] = mapped_column(JSONB)
    reasoning: Mapped[str | None] = mapped_column(Text)
    was_worn: Mapped[bool | None] = mapped_column(Boolean)
    user_feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 16. ModelUsage
# ---------------------------------------------------------------------------

class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (
        Index("idx_usage_model", "model", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    model: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    error_type: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = _created_at()

    # relationships
    task: Mapped[AgentTask | None] = relationship(back_populates="usage_records")


# ---------------------------------------------------------------------------
# 17. Transaction
# ---------------------------------------------------------------------------

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("idx_txns_date", "transaction_date"),
        Index("idx_txns_merchant", "merchant_name"),
        Index("idx_txns_category", "category", "transaction_date"),
        Index("idx_txns_recurring", "is_recurring", postgresql_where=text("is_recurring = true")),
        Index("idx_txns_teller_id", "teller_transaction_id", unique=True),
        Index("idx_txns_counterparty", "counterparty_name"),
        Index("idx_txns_type", "transaction_type"),
        Index("idx_txns_recurring_date", "is_recurring", "transaction_date", postgresql_where=text("is_recurring = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    teller_transaction_id: Mapped[str] = mapped_column(String(255), nullable=False)
    teller_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(100))
    merchant_name: Mapped[str | None] = mapped_column(String(255))
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    counterparty_type: Mapped[str | None] = mapped_column(String(50))
    transaction_type: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))
    category: Mapped[str | None] = mapped_column(String(100))
    subcategory: Mapped[str | None] = mapped_column(String(100))
    custom_category: Mapped[str | None] = mapped_column(String(100))
    is_recurring: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    authorized_date: Mapped[date | None] = mapped_column(Date)
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 18. FinanceSummary
# ---------------------------------------------------------------------------

class FinanceSummary(Base):
    __tablename__ = "finance_summaries"
    __table_args__ = (
        UniqueConstraint("period_type", "period_start", name="uq_finance_summaries_period"),
        Index("idx_finance_period", "period_type", "period_start"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_income: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    by_category: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    alerts: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    top_merchants: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    vs_previous_period: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    subscription_charges: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 19. Budget
# ---------------------------------------------------------------------------

class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        Index("idx_budgets_category", "category", unique=True, postgresql_where=text("active = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------------
# 20. HealthData
# ---------------------------------------------------------------------------

class HealthData(Base):
    __tablename__ = "health_data"
    __table_args__ = (
        Index("idx_health_type_date", "data_type", "recorded_date"),
        Index("idx_health_date", "recorded_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    data_type: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    recorded_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    source: Mapped[str | None] = mapped_column(String(30), server_default=text("'healthkit'"))
    synced_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# 21. WorkoutSplit
# ---------------------------------------------------------------------------

class WorkoutSplit(Base):
    __tablename__ = "workout_splits"
    __table_args__ = (
        Index("idx_splits_active", "active", postgresql_where=text("active = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rotation_days: Mapped[list] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    exercises: Mapped[list[WorkoutExercise]] = relationship(back_populates="split", cascade="save-update, merge")
    sessions: Mapped[list[WorkoutSession]] = relationship(back_populates="split")


# ---------------------------------------------------------------------------
# 22. WorkoutExercise
# ---------------------------------------------------------------------------

class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    __table_args__ = (
        Index("idx_exercises_split_day", "split_id", "day_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    split_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False)
    day_name: Mapped[str] = mapped_column(String(30), nullable=False)
    exercise_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_sets: Mapped[int] = mapped_column(Integer, nullable=False)
    target_reps: Mapped[int] = mapped_column(Integer, nullable=False)
    current_weight: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    weight_unit: Mapped[str] = mapped_column(String(5), nullable=False, server_default=text("'lbs'"))
    weight_increment: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, server_default=text("2.5"))
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    split: Mapped[WorkoutSplit] = relationship(back_populates="exercises")
    logs: Mapped[list[WorkoutLog]] = relationship(back_populates="exercise")


# ---------------------------------------------------------------------------
# 23. WorkoutSession
# ---------------------------------------------------------------------------

class WorkoutSession(Base):
    __tablename__ = "workout_sessions"
    __table_args__ = (
        Index("idx_sessions_status", "status", postgresql_where=text("status = 'pending'")),
        Index("idx_sessions_date", "created_at"),
        Index("idx_sessions_split", "split_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    split_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_splits.id", ondelete="RESTRICT"), nullable=False)
    day_name: Mapped[str] = mapped_column(String(30), nullable=False)
    rotation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[WorkoutSessionStatus] = mapped_column(
        _workout_session_status_enum,
        default=WorkoutSessionStatus.PENDING,
        server_default=text("'pending'"),
    )
    skip_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    split: Mapped[WorkoutSplit] = relationship(back_populates="sessions")
    logs: Mapped[list[WorkoutLog]] = relationship(back_populates="session", cascade="save-update, merge")


# ---------------------------------------------------------------------------
# 24. WorkoutLog
# ---------------------------------------------------------------------------

class WorkoutLog(Base):
    __tablename__ = "workout_logs"
    __table_args__ = (
        Index("idx_logs_session", "session_id"),
        Index("idx_logs_exercise_date", "exercise_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_sessions.id", ondelete="RESTRICT"), nullable=False)
    exercise_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workout_exercises.id", ondelete="RESTRICT"), nullable=False)
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_reps: Mapped[int] = mapped_column(Integer, nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    actual_reps: Mapped[int | None] = mapped_column(Integer)
    actual_weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    logged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # relationships
    session: Mapped[WorkoutSession] = relationship(back_populates="logs")
    exercise: Mapped[WorkoutExercise] = relationship(back_populates="logs")


# ---------------------------------------------------------------------------
# 20. DeviceToken
# ---------------------------------------------------------------------------

class DeviceToken(Base):
    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
        Index("idx_device_tokens_active", "active", postgresql_where=text("active = true")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ios'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# ---------------------------------------------------------------------------
# 21. AuditLog
# ---------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_action", "action_type", "created_at"),
        Index("idx_audit_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(30), nullable=False)
    target: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    source: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created_at()
