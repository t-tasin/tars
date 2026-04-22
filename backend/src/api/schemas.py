"""Pydantic v2 request/response schemas for all T.A.R.S. API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from shared.constants import ApprovalStatus, ContentType, JobStatus, MessageSource, RiskTier

# ---------------------------------------------------------------------------
# Shared / nested models
# ---------------------------------------------------------------------------


class Attachment(BaseModel):
    type: str
    data: str
    mime_type: str


class HealthDataPoint(BaseModel):
    type: str
    value: float
    unit: str
    date: str
    metadata: dict[str, Any] = {}


class ContactSync(BaseModel):
    apple_contact_id: str
    full_name: str
    email_addresses: list[str]
    phone_numbers: list[str] = []
    organization: str | None = None
    job_title: str | None = None
    relationship: str | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    text: str
    source: MessageSource
    conversation_id: UUID | None = None
    attachments: list[Attachment] | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "edited"]
    source: MessageSource
    reason: str | None = None
    edited_payload: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    source: MessageSource


class RejectRequest(BaseModel):
    source: MessageSource
    reason: str | None = None


class EditAndApproveRequest(BaseModel):
    source: MessageSource
    edited_payload: dict[str, Any]


class JobActionRequest(BaseModel):
    action: Literal["save", "skip", "apply", "archive"]


class ConfigUpdateRequest(BaseModel):
    namespace: str
    key: str
    value: Any


class DeployRequest(BaseModel):
    confirm: bool
    sha: str | None = None
    notify_only: bool = False


class HealthDataSyncRequest(BaseModel):
    data: list[HealthDataPoint]


class ContactsSyncRequest(BaseModel):
    contacts: list[ContactSync]
    full_sync: bool = False


# ---------------------------------------------------------------------------
# Response models — nested components
# ---------------------------------------------------------------------------


class ApprovalPreview(BaseModel):
    approval_id: UUID
    action_type: str
    title: str
    preview: dict[str, Any]


class ResponsePayload(BaseModel):
    text: str
    content_type: ContentType
    cards: list[dict[str, Any]] = []
    approval: ApprovalPreview | None = None


class CalendarEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    start: datetime
    end: datetime
    location: str | None = None
    calendar: str
    all_day: bool


class ApprovalDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action_type: str
    risk_tier: RiskTier
    status: ApprovalStatus
    title: str
    preview: dict[str, Any]
    expires_at: datetime
    created_at: datetime


class JobDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    company: str
    location: str | None = None
    salary_range: str | None = None
    match_score: int | None = None
    match_reasons: list[str]
    concerns: list[str]
    url: str
    status: JobStatus
    source: str
    scraped_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Response models — top-level
# ---------------------------------------------------------------------------


class TARSResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    response: ResponsePayload
    agent_used: str
    model_used: str


class BriefingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    briefing_id: UUID
    type: str
    date: str
    payload: dict[str, Any]
    narrative: str
    created_at: datetime
    delivered: bool


class ScheduleResponse(BaseModel):
    date: str
    events: list[CalendarEvent]
    leave_home_by: str | None = None
    commute_note: str | None = None


class ApprovalsListResponse(BaseModel):
    approvals: list[ApprovalDetail]
    total: int
    pending_count: int


class ApprovalActionResponse(BaseModel):
    approval_id: UUID
    status: ApprovalStatus
    executed: bool = False
    execution_result: str | None = None


class HealthResponse(BaseModel):
    tars: dict[str, Any]
    atlasdesk: dict[str, Any]
    ai_budget: dict[str, Any]


class JobsListResponse(BaseModel):
    jobs: list[JobDetail]
    total: int
    new_today: int


class WardrobeUploadResponse(BaseModel):
    wardrobe_item_id: UUID
    analysis: dict[str, Any]
    message: str


class OutfitResponse(BaseModel):
    outfit_id: UUID
    date: str
    suggestion: str
    reasoning: str
    items: list[dict[str, Any]]
    weather: dict[str, Any]


class FinanceSummaryResponse(BaseModel):
    period: str
    date: str
    total_spent: float
    transactions: list[dict[str, Any]]
    month_to_date: dict[str, Any]
    alerts: list[str]
    trends: dict[str, Any] | None = None


class BudgetCreateRequest(BaseModel):
    category: str
    monthly_limit: float = Field(gt=0)


class BudgetStatus(BaseModel):
    category: str
    monthly_limit: float
    spent: float
    remaining: float
    percent_used: float
    days_remaining: int
    on_track: bool


class BudgetListResponse(BaseModel):
    budgets: list[BudgetStatus]


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Workout schemas
# ---------------------------------------------------------------------------


class CreateExerciseSchema(BaseModel):
    day_name: str
    exercise_name: str
    target_sets: int = Field(gt=0)
    target_reps: int = Field(gt=0)
    current_weight: float = Field(ge=0)
    weight_unit: str = "lbs"
    weight_increment: float = 2.5


class CreateSplitRequest(BaseModel):
    name: str = Field(min_length=1)
    rotation_days: list[str] = Field(min_length=1)
    exercises: list[CreateExerciseSchema]


class UpdateSplitRequest(BaseModel):
    name: str | None = None
    rotation_days: list[str] | None = None


class LogSetRequest(BaseModel):
    session_id: UUID
    exercise_id: UUID
    set_number: int = Field(gt=0)
    actual_reps: int = Field(ge=0)
    actual_weight: float = Field(ge=0)


class SkipSessionRequest(BaseModel):
    reason: str = Field(min_length=1)


class ExerciseDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    day_name: str
    exercise_name: str
    target_sets: int
    target_reps: int
    current_weight: float
    weight_unit: str
    weight_increment: float
    order_index: int


class SplitDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    rotation_days: list[str]
    active: bool
    exercises: list[ExerciseDetail] = []


class LogDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exercise_id: UUID
    set_number: int
    target_reps: int
    target_weight: float
    actual_reps: int | None
    actual_weight: float | None
    logged_at: datetime | None


class SessionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    split_id: UUID
    day_name: str
    rotation_index: int
    scheduled_at: datetime | None
    status: str
    skip_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    logs: list[LogDetail] = []


class StreakResponse(BaseModel):
    streak: int
    recent_skips: list[dict[str, Any]]


class WorkoutHistoryResponse(BaseModel):
    logs: list[LogDetail]
    total: int
