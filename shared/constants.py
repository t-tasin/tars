from __future__ import annotations

from enum import StrEnum


class IntentType(StrEnum):
    BRIEFING = "briefing"
    DAILY_LIFE = "daily_life"
    COMMUNICATION = "communication"
    JOB_SEARCH = "job_search"
    FASHION = "fashion"
    PRODUCT_RESEARCH = "product_research"
    CODING = "coding"
    RESEARCH = "research"
    HEALTH_MONITOR = "health_monitor"
    FINANCE = "finance"
    HEALTH_FITNESS = "health_fitness"
    EMAIL_CLASSIFIER = "email_classifier"
    EOD_SUMMARY = "eod_summary"
    CONFIG = "config"
    SYSTEM = "system"
    GENERAL = "general"


class ModelName(StrEnum):
    CLAUDE_CODE = "claude_code"
    GEMINI_FLASH = "gemini_flash"
    GEMINI_PRO = "gemini_pro"
    GEMINI_VISION = "gemini_vision"
    LOCAL = "local"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    EXPIRED = "expired"
    EXECUTED = "executed"


class RiskTier(StrEnum):
    TIER1_AUTONOMOUS = "tier1_autonomous"
    TIER2_APPROVAL = "tier2_approval"
    TIER3_ESCALATION = "tier3_escalation"


class EmailTier(StrEnum):
    URGENT = "urgent"
    ACTIONABLE = "actionable"
    INFORMATIONAL = "informational"
    NOISE = "noise"


class JobStatus(StrEnum):
    NEW = "new"
    SAVED = "saved"
    APPLYING = "applying"
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    EXPIRED = "expired"


class HealthStatus(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


class MessageSource(StrEnum):
    IOS = "ios"
    TELEGRAM = "telegram"
    WATCH = "watch"
    SIRI = "siri"
    WAKE_WORD = "wake_word"
    SYSTEM = "system"


class ContentType(StrEnum):
    TEXT = "text"
    CARD = "card"
    IMAGE = "image"
    ACTION = "action"
    APPROVAL = "approval"
    BRIEFING = "briefing"


# Action type → risk tier mapping for the approval system
TIER_MAP: dict[str, RiskTier] = {
    "send_email": RiskTier.TIER2_APPROVAL,
    "create_event": RiskTier.TIER2_APPROVAL,
    "archive_emails": RiskTier.TIER2_APPROVAL,
    "create_notion": RiskTier.TIER2_APPROVAL,
    "create_pr": RiskTier.TIER2_APPROVAL,
    "apply_job": RiskTier.TIER2_APPROVAL,
    "email_professor": RiskTier.TIER3_ESCALATION,
    "push_production": RiskTier.TIER3_ESCALATION,
    "delete_data": RiskTier.TIER3_ESCALATION,
    "modify_infra": RiskTier.TIER3_ESCALATION,
}

# Default model routing per agent/intent type
AGENT_MODEL_MAP: dict[IntentType, ModelName] = {
    IntentType.BRIEFING: ModelName.GEMINI_PRO,
    IntentType.DAILY_LIFE: ModelName.GEMINI_FLASH,
    IntentType.COMMUNICATION: ModelName.CLAUDE_CODE,
    IntentType.JOB_SEARCH: ModelName.GEMINI_PRO,
    IntentType.FASHION: ModelName.GEMINI_VISION,
    IntentType.PRODUCT_RESEARCH: ModelName.GEMINI_PRO,
    IntentType.CODING: ModelName.CLAUDE_CODE,
    IntentType.RESEARCH: ModelName.CLAUDE_CODE,
    IntentType.HEALTH_MONITOR: ModelName.LOCAL,
    IntentType.FINANCE: ModelName.GEMINI_FLASH,
    IntentType.HEALTH_FITNESS: ModelName.GEMINI_FLASH,
    IntentType.EMAIL_CLASSIFIER: ModelName.GEMINI_FLASH,
    IntentType.EOD_SUMMARY: ModelName.GEMINI_PRO,
    IntentType.CONFIG: ModelName.LOCAL,
    IntentType.SYSTEM: ModelName.LOCAL,
    IntentType.GENERAL: ModelName.GEMINI_FLASH,
}
