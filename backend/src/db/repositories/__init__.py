"""Database repository classes — one per entity."""

from db.repositories.agent_outputs import AgentOutputRepository
from db.repositories.agent_tasks import AgentTaskRepository
from db.repositories.approvals import ApprovalRepository
from db.repositories.audit_log import AuditLogRepository
from db.repositories.briefings import BriefingRepository
from db.repositories.budgets import BudgetRepository
from db.repositories.config import ConfigRepository
from db.repositories.contacts import ContactRepository
from db.repositories.conversations import ConversationRepository
from db.repositories.email_classifications import EmailClassificationRepository
from db.repositories.feedback_log import FeedbackLogRepository
from db.repositories.finance_summaries import FinanceSummaryRepository
from db.repositories.health_data import HealthDataRepository
from db.repositories.job_applications import JobApplicationRepository
from db.repositories.job_listings import JobListingRepository
from db.repositories.messages import MessageRepository
from db.repositories.model_usage import ModelUsageRepository
from db.repositories.system_health_log import SystemHealthLogRepository
from db.repositories.transactions import TransactionRepository
from db.repositories.wardrobe_items import WardrobeItemRepository
from db.repositories.wardrobe_outfits import WardrobeOutfitRepository
from db.repositories.world_state import WorldStateRepository

__all__ = [
    "AgentOutputRepository",
    "AgentTaskRepository",
    "ApprovalRepository",
    "AuditLogRepository",
    "BriefingRepository",
    "BudgetRepository",
    "ConfigRepository",
    "ContactRepository",
    "ConversationRepository",
    "EmailClassificationRepository",
    "FeedbackLogRepository",
    "FinanceSummaryRepository",
    "HealthDataRepository",
    "JobApplicationRepository",
    "JobListingRepository",
    "MessageRepository",
    "ModelUsageRepository",
    "SystemHealthLogRepository",
    "TransactionRepository",
    "WardrobeItemRepository",
    "WardrobeOutfitRepository",
    "WorldStateRepository",
]
