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
    WORKOUT_TRACKER = "workout_tracker"


class ModelName(StrEnum):
    CLAUDE_CODE = "claude_code"
    GEMINI_FLASH = "gemini_flash"
    GEMINI_PRO = "gemini_pro"
    GEMINI_VISION = "gemini_vision"
    LOCAL = "local"
    LOCAL_REFLEX = "local_reflex"  # Qwen3-1.7B on tars2:8001 (CPU)
    LOCAL_BRAIN = "local_brain"  # Qwen3-8B on tars2:8002 (CPU)
    LOCAL_EMBED = "local_embed"  # Qwen3-Embedding-0.6B on tars2:8003 (GPU)


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


class AutonomyClass(StrEnum):
    """Autonomy budget classes — see ``docs/autonomy_budget.md``.

    Order is significant: the ``level`` property exposes a 0..4 numeric
    rank from least- to most-privileged so callers can compare instances
    (e.g. ``AutonomyClass.WRITE_INFRA.level > AutonomyClass.READ.level``).
    """

    READ = "read"
    WRITE_LOCAL = "write_local"
    WRITE_SELF = "write_self"
    WRITE_WORLD = "write_world"
    WRITE_INFRA = "write_infra"

    @property
    def level(self) -> int:
        """Numeric rank from 0 (READ) to 4 (WRITE_INFRA)."""
        return _AUTONOMY_LEVELS[self]

    def requires_approval(self) -> bool:
        """HC-01 alignment: WRITE_WORLD and WRITE_INFRA always need approval."""
        return self in {AutonomyClass.WRITE_WORLD, AutonomyClass.WRITE_INFRA}


_AUTONOMY_LEVELS: dict[AutonomyClass, int] = {
    AutonomyClass.READ: 0,
    AutonomyClass.WRITE_LOCAL: 1,
    AutonomyClass.WRITE_SELF: 2,
    AutonomyClass.WRITE_WORLD: 3,
    AutonomyClass.WRITE_INFRA: 4,
}


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


class WorkoutSessionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"


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
    IntentType.WORKOUT_TRACKER: ModelName.GEMINI_FLASH,
}
