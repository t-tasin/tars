"""Builds scoped context for each agent invocation."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import AgentContext
from orchestrator.intent_classifier import Intent
from orchestrator.model_router import ModelRoute
from shared.constants import IntentType

log = structlog.get_logger()


class ContextBuilder:
    """Builds scoped context for each agent. Ensures agents only see relevant data."""

    def __init__(self, gemini_client: Any = None) -> None:
        self._gemini_client = gemini_client

    async def build(
        self,
        intent: Intent,
        route: ModelRoute,
        user_message: str,
        source: str,
        conversation_id: UUID | None = None,
        attachments: list[dict[str, Any]] | None = None,
        session: AsyncSession | None = None,
    ) -> AgentContext:
        """Build an AgentContext with agent-specific data.

        Each agent type gets only the DB context and config it needs.
        If ``session`` is provided, repositories are used to fetch real data;
        otherwise methods return safe defaults.
        """
        attachments = attachments or []
        db_context: dict[str, Any] = {}
        config: dict[str, Any] = {}

        # --- Agent-specific context fetching ---

        if intent.agent == IntentType.BRIEFING:
            config = await self._get_config("morning_briefing", session)
            if self._gemini_client:
                config["gemini_client"] = self._gemini_client

        elif intent.agent == IntentType.EMAIL_CLASSIFIER:
            db_context["recent_corrections"] = await self._get_recent_email_corrections(20, session)
            config = await self._get_config("email_contacts", session)

        elif intent.agent == IntentType.COMMUNICATION:
            if conversation_id:
                db_context["recipient_history"] = await self._get_recipient_history(conversation_id, session)
            config = await self._get_config("communication", session)

        elif intent.agent == IntentType.JOB_SEARCH:
            db_context["saved_jobs_count"] = await self._get_saved_jobs_count(session)
            config = await self._get_config("job_search", session)
            if intent.action:
                config["intent_action"] = intent.action
            if self._gemini_client:
                config["gemini_client"] = self._gemini_client

        elif intent.agent == IntentType.FASHION:
            db_context["wardrobe_summary"] = await self._get_active_wardrobe_count(session)

        elif intent.agent == IntentType.FINANCE:
            db_context["recent_transactions"] = await self._get_recent_transactions(7, session)
            config = await self._get_config("finance", session)

        elif intent.agent == IntentType.HEALTH_FITNESS:
            db_context["recent_health_data"] = await self._get_recent_health_data(7, session)

        elif intent.agent == IntentType.HEALTH_MONITOR:
            db_context["system_health"] = await self._get_system_health(session)

        elif intent.agent == IntentType.DAILY_LIFE:
            config = await self._get_config("daily_life", session)

        elif intent.agent == IntentType.PRODUCT_RESEARCH:
            config = await self._get_config("product_research", session)

        elif intent.agent == IntentType.CODING:
            config = await self._get_config("coding", session)

        elif intent.agent == IntentType.RESEARCH:
            config = await self._get_config("research", session)

        elif intent.agent == IntentType.EOD_SUMMARY:
            db_context["todays_activity"] = await self._get_todays_activity(session)

        # --- Conversation continuity (all agents) ---

        if conversation_id:
            db_context["recent_messages"] = await self._get_recent_messages(
                conversation_id, limit=5, session=session,
            )

        # --- Attach model/route metadata ---

        config["_model"] = route.model
        config["_node"] = route.node
        if route.mcp_profile:
            config["_mcp_profile"] = route.mcp_profile

        log.debug(
            "context_built",
            agent=intent.agent,
            db_keys=list(db_context.keys()),
            config_keys=list(config.keys()),
            has_conversation=conversation_id is not None,
        )

        return AgentContext(
            user_message=user_message,
            intent_type=intent.agent,
            conversation_id=conversation_id,
            source=source,
            attachments=attachments,
            db_context=db_context,
            config=config,
        )

    # ------------------------------------------------------------------
    # DB-backed context queries
    # ------------------------------------------------------------------

    async def _get_config(self, namespace: str, session: AsyncSession | None = None) -> dict[str, Any]:
        """Fetch config values for a namespace from the config table."""
        if session is None:
            return {}
        try:
            from db.repositories.config import ConfigRepository
            repo = ConfigRepository(session)
            return await repo.get_namespace(namespace)
        except Exception:
            log.error("repo_query_failed", repo="config", namespace=namespace, exc_info=True)
            return {}

    async def _get_recent_email_corrections(self, limit: int, session: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Fetch recent email classification corrections for learning."""
        if session is None:
            return []
        try:
            from db.repositories.email_classifications import EmailClassificationRepository
            repo = EmailClassificationRepository(session)
            rows = await repo.get_recent_corrections(limit)
            return [
                {
                    "from_address": r.from_address,
                    "from_name": r.from_name,
                    "subject": r.subject,
                    "classified_tier": str(r.classified_tier),
                    "user_correction": str(r.user_correction),
                }
                for r in rows
            ]
        except Exception:
            log.error("repo_query_failed", repo="email_classifications", exc_info=True)
            return []

    async def _get_recipient_history(self, conversation_id: UUID, session: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Fetch past communication with the recipient in this conversation."""
        if session is None:
            return []
        try:
            from db.repositories.messages import MessageRepository
            repo = MessageRepository(session)
            msgs = await repo.get_by_conversation(conversation_id, limit=20)
            return [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in msgs
            ]
        except Exception:
            log.error("repo_query_failed", repo="messages", exc_info=True)
            return []

    async def _get_recent_messages(
        self,
        conversation_id: UUID,
        limit: int,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent messages for conversation continuity."""
        if session is None:
            return []
        try:
            from db.repositories.messages import MessageRepository
            repo = MessageRepository(session)
            msgs = await repo.get_by_conversation(conversation_id, limit=limit)
            return [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in msgs
            ]
        except Exception:
            log.error("repo_query_failed", repo="messages", exc_info=True)
            return []

    async def _get_recent_transactions(self, days: int, session: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Fetch recent financial transactions."""
        if session is None:
            return []
        try:
            from db.repositories.transactions import TransactionRepository
            repo = TransactionRepository(session)
            txns = await repo.get_recent(days=days)
            return [
                {
                    "merchant_name": t.merchant_name,
                    "amount": str(t.amount),
                    "category": t.category,
                    "transaction_date": t.transaction_date.isoformat(),
                    "is_recurring": t.is_recurring,
                }
                for t in txns
            ]
        except Exception:
            log.error("repo_query_failed", repo="transactions", exc_info=True)
            return []

    async def _get_active_wardrobe_count(self, session: AsyncSession | None = None) -> dict[str, Any]:
        """Get summary of active wardrobe items."""
        if session is None:
            return {}
        try:
            from db.repositories.wardrobe_items import WardrobeItemRepository
            repo = WardrobeItemRepository(session)
            return await repo.get_active_count()
        except Exception:
            log.error("repo_query_failed", repo="wardrobe_items", exc_info=True)
            return {}

    async def _get_saved_jobs_count(self, session: AsyncSession | None = None) -> int:
        """Get count of saved job listings."""
        if session is None:
            return 0
        try:
            from db.repositories.job_listings import JobListingRepository
            repo = JobListingRepository(session)
            return await repo.get_saved_count()
        except Exception:
            log.error("repo_query_failed", repo="job_listings", exc_info=True)
            return 0

    async def _get_recent_health_data(self, days: int, session: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Fetch recent health/fitness data points."""
        if session is None:
            return []
        try:
            from db.repositories.health_data import HealthDataRepository
            repo = HealthDataRepository(session)
            rows = await repo.get_recent(days=days)
            return [
                {
                    "data_type": r.data_type,
                    "value": str(r.value),
                    "unit": r.unit,
                    "recorded_date": r.recorded_date.isoformat(),
                }
                for r in rows
            ]
        except Exception:
            log.error("repo_query_failed", repo="health_data", exc_info=True)
            return []

    async def _get_system_health(self, session: AsyncSession | None = None) -> dict[str, Any]:
        """Fetch current system health status."""
        if session is None:
            return {}
        try:
            from db.repositories.system_health_log import SystemHealthLogRepository
            repo = SystemHealthLogRepository(session)
            return await repo.get_latest()
        except Exception:
            log.error("repo_query_failed", repo="system_health_log", exc_info=True)
            return {}

    async def _get_todays_activity(self, session: AsyncSession | None = None) -> dict[str, Any]:
        """Fetch today's agent activity for end-of-day summary."""
        if session is None:
            return {}
        try:
            from db.repositories.agent_tasks import AgentTaskRepository
            from db.repositories.messages import MessageRepository
            from db.models import Message, Conversation

            task_repo = AgentTaskRepository(session)
            tasks_by_agent = await task_repo.get_todays_by_agent()
            total_tasks = await task_repo.get_todays_count()

            # Count today's messages
            today_start = dt.datetime.combine(
                dt.date.today(), dt.time.min, tzinfo=dt.timezone.utc,
            )
            from sqlalchemy import func, select
            msg_result = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.created_at >= today_start)
            )
            total_messages = msg_result.scalar_one()

            return {
                "total_tasks": total_tasks,
                "tasks_by_agent": tasks_by_agent,
                "total_messages": total_messages,
            }
        except Exception:
            log.error("repo_query_failed", repo="todays_activity", exc_info=True)
            return {}
