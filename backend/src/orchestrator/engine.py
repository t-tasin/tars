"""Central orchestrator engine for T.A.R.S.

Routes messages through the full pipeline:
    intent classification → model routing → context building →
    agent execution → usage tracking → approval (if needed) →
    response formatting → audit logging.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from shared.constants import ModelName
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import AgentContext, AgentResult, BaseAgent
from config import get_settings
from db.models import AuditLog, Conversation, Message
from db.session import get_db_session
from models.claude_spawner import ClaudeCodeSpawner
from models.gemini_client import GeminiClient
from models.usage_tracker import UsageTracker
from orchestrator.approval_manager import ApprovalManager
from orchestrator.context_builder import ContextBuilder
from orchestrator.intent_classifier import Intent, IntentClassifier
from orchestrator.model_router import ModelRoute, ModelRouter
from orchestrator.response_formatter import ResponseFormatter

log = structlog.get_logger()

# Gemini model name mapping: internal label → API model ID
_GEMINI_MODEL_IDS: dict[str, str] = {
    ModelName.GEMINI_FLASH: "gemini-2.5-flash",
    ModelName.GEMINI_PRO: "gemini-2.5-pro",
    ModelName.GEMINI_VISION: "gemini-2.5-flash",
}


class Orchestrator:
    """Central nervous system of T.A.R.S. Routes messages through the full pipeline."""

    def __init__(self) -> None:
        settings = get_settings()

        self.intent_classifier = IntentClassifier()
        self.model_router = ModelRouter()
        self.gemini_client = GeminiClient(api_key=settings.gemini_api_key)
        self.context_builder = ContextBuilder(gemini_client=self.gemini_client)
        self.approval_manager = ApprovalManager()
        self.response_formatter = ResponseFormatter()
        self.claude_spawner = ClaudeCodeSpawner()
        self.agents: dict[str, BaseAgent] = {}
        self._notifications: Any = None  # lazy — set after init_notification_service()

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def register_agent(self, agent: BaseAgent) -> None:
        """Register an agent instance for a given agent type."""
        self.agents[agent.agent_type] = agent
        log.info("agent_registered", agent_type=agent.agent_type)

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def process_message(
        self,
        text: str,
        source: str,
        conversation_id: UUID | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Process a user message through the full orchestrator pipeline.

        Steps:
            1. Persist user message
            2. Classify intent (zero tokens)
            3. Route to model
            4. Build scoped context
            5. Execute via agent or model fallback
            6. Track AI usage (HC-12)
            7. Create approval if side effects (HC-01)
            8. Format response
            9. Persist assistant response
            10. Audit log (HC-08)

        Returns a dict matching the ``TARSResponse`` schema.
        """
        attachments = attachments or []
        start_ms = _now_ms()

        async with get_db_session() as session:
            # 1. Save user message
            conv_id, msg_id = await self._save_message(
                session,
                text,
                source,
                conversation_id,
            )

            # 2. Classify intent
            intent = self.intent_classifier.classify(text, source, attachments)
            log.info(
                "intent_classified",
                intent=intent.agent,
                action=intent.action,
                complexity=intent.complexity,
            )

            # 3. Route to model
            route = self.model_router.route(intent)
            log.info(
                "model_routed",
                model=route.model,
                node=route.node,
                mcp_profile=route.mcp_profile,
            )

            # 4. Build scoped context
            context = await self.context_builder.build(
                intent,
                route,
                text,
                source,
                conv_id,
                attachments,
                session=session,
            )

            # 5. Execute
            exec_start = _now_ms()
            result = await self._execute(intent, route, context)
            exec_duration_ms = _now_ms() - exec_start

            # 6. Track usage (HC-12)
            tokens_in, tokens_out = self._extract_token_counts(result, route)
            usage_tracker = UsageTracker(session)
            await usage_tracker.track(
                model=route.model,
                agent_type=intent.agent,
                task_id=None,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                duration_ms=exec_duration_ms,
                success=result.success,
                error_type=result.error,
            )

            # Check budget alerts (HC-12)
            budget_alert = await usage_tracker.check_budget_alert()
            if budget_alert:
                log.warning("budget_alert_triggered", alert=budget_alert)
                notifier = self._get_notifications()
                if notifier:
                    await notifier.notify_budget_alert(budget_alert)

            # 7. Approval flow if side effects (HC-01)
            if result.has_side_effects and result.action_type:
                approval = await self.approval_manager.create(
                    session,
                    action_type=result.action_type,
                    title=result.approval_title or result.action_type,
                    preview_payload=result.preview or {},
                    task_id=None,
                )
                response = self.response_formatter.format_approval(
                    result,
                    approval,
                    conv_id,
                    msg_id,
                    intent.agent,
                    route.model,
                )

                # Push approval request via notification service
                notifier = self._get_notifications()
                if notifier:
                    await notifier.notify_approval(approval)
            else:
                response = self.response_formatter.format(
                    result,
                    conv_id,
                    msg_id,
                    intent.agent,
                    route.model,
                )

            # 8. Save assistant response
            await self._save_response(session, conv_id, response)

            # 9. Audit log (HC-08)
            total_duration_ms = _now_ms() - start_ms
            await self._audit_log(
                session,
                action_type="message_processed",
                actor=source,
                target=str(conv_id),
                details={
                    "message_id": str(msg_id),
                    "intent": intent.agent,
                    "model": route.model,
                    "node": route.node,
                    "duration_ms": total_duration_ms,
                    "success": result.success,
                    "has_side_effects": result.has_side_effects,
                },
                source=source,
            )

            log.info(
                "message_processed",
                conversation_id=str(conv_id),
                message_id=str(msg_id),
                intent=intent.agent,
                model=route.model,
                duration_ms=total_duration_ms,
                success=result.success,
            )

            return response

    # ------------------------------------------------------------------
    # Notification service accessor
    # ------------------------------------------------------------------

    def _get_notifications(self) -> Any:
        """Lazy-load the NotificationService singleton.

        Returns ``None`` if the service has not been initialised yet
        (e.g. during unit tests).
        """
        if self._notifications is None:
            try:
                from integrations.notification_service import get_notification_service

                self._notifications = get_notification_service()
            except RuntimeError:
                log.debug("notification_service_not_available")
        return self._notifications

    # ------------------------------------------------------------------
    # Execution dispatcher
    # ------------------------------------------------------------------

    async def _execute(
        self,
        intent: Intent,
        route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult:
        """Execute via registered agent or fall back to direct model call.

        Implements HC-09 model fallback chain:
            1. Try the routed model (agent or direct)
            2. If Claude fails → try Gemini Pro
            3. If Gemini fails → try Claude
            4. If both fail → return raw data (no AI composition)
        """
        # Prefer registered agent
        if intent.agent in self.agents:
            agent = self.agents[intent.agent]
            try:
                if not await agent.validate_context(context):
                    log.warning("agent_context_invalid", agent=intent.agent)
                    return AgentResult(
                        success=False,
                        text="I couldn't process that request — missing required context.",
                        error="context_validation_failed",
                    )
                return await agent.execute(context)
            except Exception:
                log.exception("agent_execution_failed", agent=intent.agent)
                # Agent failed — fall through to model fallback chain

        # Model fallback chain (HC-09)
        return await self._execute_with_fallback(route, context)

    async def _execute_with_fallback(
        self,
        route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult:
        """Direct model invocation with HC-09 fallback chain.

        Order:
            1. Try the originally routed model
            2. If Claude unavailable → try Gemini Pro
            3. If Gemini unavailable → try Claude
            4. If both unavailable → return raw data
        """
        if route.model == ModelName.LOCAL:
            return AgentResult(
                success=True,
                text="This feature is not yet implemented.",
            )

        # --- Attempt 1: originally routed model ---
        primary_result = await self._try_model(route, context)
        if primary_result.success:
            return primary_result

        # --- Attempt 2: fallback model ---
        fallback_result = await self._try_fallback_model(route, context)
        if fallback_result is not None and fallback_result.success:
            return fallback_result

        # --- Attempt 3: raw data delivery (HC-09 last resort) ---
        log.error(
            "all_models_unavailable",
            primary_model=route.model,
            message_preview=context.user_message[:100],
        )
        return AgentResult(
            success=True,
            text=(
                "I'm currently unable to compose an AI response — both Claude "
                "and Gemini are temporarily unavailable. Here's your raw request "
                "for reference. I'll be back to full capacity shortly."
            ),
            error="all_models_unavailable",
            data={"raw_message": context.user_message, "fallback": True},
        )

    async def _try_model(
        self,
        route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult:
        """Attempt a single model call. Returns a result (may have success=False)."""
        if route.model in _GEMINI_MODEL_IDS:
            return await self._gemini_call(route, context)
        if route.model == ModelName.CLAUDE_CODE:
            return await self._claude_call(route, context)
        return AgentResult(
            success=False,
            text="I'm not sure how to handle that request.",
            error="unknown_route",
        )

    async def _try_fallback_model(
        self,
        original_route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult | None:
        """Try the opposite model family as a fallback. Returns None if not applicable."""
        if original_route.model == ModelName.CLAUDE_CODE:
            # Claude failed → try Gemini Pro
            log.warning(
                "model_fallback",
                from_model=original_route.model,
                to_model=ModelName.GEMINI_PRO,
            )
            fallback_route = ModelRoute(model=ModelName.GEMINI_PRO, node="node1")
            result = await self._gemini_call(fallback_route, context)
            if result.success:
                result.data["fallback_from"] = original_route.model
            return result

        if original_route.model in _GEMINI_MODEL_IDS:
            # Gemini failed → try Claude
            log.warning(
                "model_fallback",
                from_model=original_route.model,
                to_model=ModelName.CLAUDE_CODE,
            )
            fallback_route = ModelRoute(
                model=ModelName.CLAUDE_CODE,
                node="node1",
                mcp_profile="general",
            )
            result = await self._claude_call(fallback_route, context)
            if result.success:
                result.data["fallback_from"] = original_route.model
            return result

        return None

    async def _gemini_call(
        self,
        route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult:
        """Call Gemini directly as a conversational fallback."""
        model_id = _GEMINI_MODEL_IDS.get(route.model, "gemini-2.5-flash")
        try:
            response = await self.gemini_client.generate(
                prompt=context.user_message,
                model=model_id,
                system_instruction=("You are T.A.R.S., a personal AI assistant. Be helpful, concise, and friendly."),
            )
            return AgentResult(
                success=True,
                text=response.text,
                data={
                    "tokens_input": response.tokens_input,
                    "tokens_output": response.tokens_output,
                    "duration_ms": response.duration_ms,
                },
            )
        except Exception as exc:
            log.error("gemini_call_failed", model=route.model, error=str(exc))
            return AgentResult(
                success=False,
                text="Gemini is temporarily unavailable.",
                error="gemini_unavailable",
            )

    async def _claude_call(
        self,
        route: ModelRoute,
        context: AgentContext,
    ) -> AgentResult:
        """Call Claude Code directly as a fallback."""
        try:
            result = await self.claude_spawner.execute(
                prompt=context.user_message,
                mcp_profile=route.mcp_profile,
            )
            return AgentResult(
                success=result.success,
                text=result.text or "No response from Claude.",
                error=result.error,
                data={
                    "duration_ms": result.duration_ms,
                    "cost_usd": result.cost_usd,
                    "num_turns": result.num_turns,
                },
            )
        except Exception as exc:
            log.error("claude_call_failed", error=str(exc))
            return AgentResult(
                success=False,
                text="Claude is temporarily unavailable.",
                error="claude_unavailable",
            )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _save_message(
        self,
        session: AsyncSession,
        text: str,
        source: str,
        conversation_id: UUID | None,
    ) -> tuple[UUID, UUID]:
        """Persist the user message, creating a conversation if needed.

        Returns (conversation_id, message_id).
        """
        if conversation_id:
            conv_id = conversation_id
        else:
            conversation = Conversation(source=source)
            session.add(conversation)
            await session.flush()
            conv_id = conversation.id

        message = Message(
            conversation_id=conv_id,
            role="user",
            content=text,
        )
        session.add(message)
        await session.flush()

        return conv_id, message.id

    async def _save_response(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        response: dict[str, Any],
    ) -> None:
        """Persist the assistant's response as a message."""
        response_text = response.get("response", {}).get("text", "")
        content_type = response.get("response", {}).get("content_type", "text")

        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=response_text,
            content_type=content_type,
            metadata_={
                "agent_used": response.get("agent_used"),
                "model_used": response.get("model_used"),
            },
        )
        session.add(message)
        await session.flush()

    async def _audit_log(
        self,
        session: AsyncSession,
        *,
        action_type: str,
        actor: str,
        target: str,
        details: dict[str, Any],
        source: str | None = None,
    ) -> None:
        """Insert a row into ``audit_log`` (HC-08)."""
        session.add(
            AuditLog(
                action_type=action_type,
                actor=actor,
                target=target,
                details=details,
                source=source,
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_token_counts(
        result: AgentResult,
        route: ModelRoute,
    ) -> tuple[int, int]:
        """Extract token counts from result data if available."""
        tokens_in = result.data.get("tokens_input", 0)
        tokens_out = result.data.get("tokens_output", 0)
        return int(tokens_in), int(tokens_out)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Return a module-level singleton Orchestrator instance.

    The first call creates the instance; subsequent calls return the same one.
    Thread-safe in asyncio (single-threaded event loop).
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
        log.info("orchestrator_initialized")
    return _orchestrator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    """Current monotonic time in milliseconds."""
    import time as _time

    return int(_time.monotonic() * 1000)
