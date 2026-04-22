"""Tests for the ContextBuilder."""

from __future__ import annotations

from uuid import uuid4

import pytest
from shared.constants import IntentType, ModelName

from orchestrator.context_builder import ContextBuilder
from orchestrator.intent_classifier import Intent
from orchestrator.model_router import ModelRoute


@pytest.fixture
def builder() -> ContextBuilder:
    return ContextBuilder()


def _route(model: str = ModelName.GEMINI_FLASH) -> ModelRoute:
    return ModelRoute(model=model, node="node1")


# ---------------------------------------------------------------------------
# Agent-specific context shapes
# ---------------------------------------------------------------------------


class TestAgentContextShapes:
    @pytest.mark.asyncio
    async def test_briefing_gets_config(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.BRIEFING)
        ctx = await builder.build(intent, _route(ModelName.GEMINI_PRO), "Give me my briefing", "ios")
        assert "recent_corrections" not in ctx.db_context
        assert ctx.intent_type == IntentType.BRIEFING
        assert ctx.config["_model"] == ModelName.GEMINI_PRO

    @pytest.mark.asyncio
    async def test_email_classifier_gets_corrections(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.EMAIL_CLASSIFIER)
        ctx = await builder.build(intent, _route(), "Classify my emails", "system")
        assert "recent_corrections" in ctx.db_context
        assert isinstance(ctx.db_context["recent_corrections"], list)

    @pytest.mark.asyncio
    async def test_communication_gets_recipient_history(self, builder: ContextBuilder) -> None:
        conv_id = uuid4()
        intent = Intent(agent=IntentType.COMMUNICATION)
        ctx = await builder.build(
            intent,
            _route(ModelName.CLAUDE_CODE),
            "Draft email to prof",
            "ios",
            conversation_id=conv_id,
        )
        assert "recipient_history" in ctx.db_context
        assert "recent_messages" in ctx.db_context

    @pytest.mark.asyncio
    async def test_communication_without_conversation(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.COMMUNICATION)
        ctx = await builder.build(intent, _route(ModelName.CLAUDE_CODE), "Draft email", "ios")
        assert "recipient_history" not in ctx.db_context
        assert "recent_messages" not in ctx.db_context

    @pytest.mark.asyncio
    async def test_job_search_gets_config_and_count(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.JOB_SEARCH)
        ctx = await builder.build(intent, _route(), "Find me jobs", "telegram")
        assert "saved_jobs_count" in ctx.db_context

    @pytest.mark.asyncio
    async def test_fashion_gets_wardrobe_summary(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.FASHION)
        ctx = await builder.build(intent, _route(ModelName.GEMINI_VISION), "What should I wear?", "ios")
        assert "wardrobe_summary" in ctx.db_context

    @pytest.mark.asyncio
    async def test_finance_gets_transactions(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.FINANCE)
        ctx = await builder.build(intent, _route(), "How much did I spend?", "ios")
        assert "recent_transactions" in ctx.db_context
        assert isinstance(ctx.db_context["recent_transactions"], list)

    @pytest.mark.asyncio
    async def test_health_fitness_gets_health_data(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.HEALTH_FITNESS)
        ctx = await builder.build(intent, _route(), "How was my sleep?", "ios")
        assert "recent_health_data" in ctx.db_context

    @pytest.mark.asyncio
    async def test_health_monitor_gets_system_health(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.HEALTH_MONITOR)
        ctx = await builder.build(intent, _route(ModelName.LOCAL), "Check server status", "telegram")
        assert "system_health" in ctx.db_context

    @pytest.mark.asyncio
    async def test_eod_summary_gets_activity(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.EOD_SUMMARY)
        ctx = await builder.build(intent, _route(ModelName.GEMINI_PRO), "End of day", "system")
        assert "todays_activity" in ctx.db_context

    @pytest.mark.asyncio
    async def test_general_has_no_extra_context(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Hello", "telegram")
        assert ctx.db_context == {}


# ---------------------------------------------------------------------------
# Conversation continuity
# ---------------------------------------------------------------------------


class TestConversationContinuity:
    @pytest.mark.asyncio
    async def test_recent_messages_included_with_conversation(self, builder: ContextBuilder) -> None:
        conv_id = uuid4()
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Follow up", "ios", conversation_id=conv_id)
        assert "recent_messages" in ctx.db_context
        assert ctx.conversation_id == conv_id

    @pytest.mark.asyncio
    async def test_no_recent_messages_without_conversation(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Hello", "ios")
        assert "recent_messages" not in ctx.db_context


# ---------------------------------------------------------------------------
# Route metadata propagation
# ---------------------------------------------------------------------------


class TestRouteMetadata:
    @pytest.mark.asyncio
    async def test_model_and_node_in_config(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.CODING)
        route = ModelRoute(model=ModelName.CLAUDE_CODE, node="node2", mcp_profile="coding")
        ctx = await builder.build(intent, route, "Fix the bug", "ios")
        assert ctx.config["_model"] == ModelName.CLAUDE_CODE
        assert ctx.config["_node"] == "node2"
        assert ctx.config["_mcp_profile"] == "coding"

    @pytest.mark.asyncio
    async def test_no_mcp_profile_when_absent(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Hi", "ios")
        assert "_mcp_profile" not in ctx.config


# ---------------------------------------------------------------------------
# Basic fields
# ---------------------------------------------------------------------------


class TestBasicFields:
    @pytest.mark.asyncio
    async def test_user_message_preserved(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Hello world", "telegram")
        assert ctx.user_message == "Hello world"
        assert ctx.source == "telegram"

    @pytest.mark.asyncio
    async def test_attachments_passed_through(self, builder: ContextBuilder) -> None:
        attachments = [{"type": "image", "data": "abc", "mime_type": "image/png"}]
        intent = Intent(agent=IntentType.FASHION)
        ctx = await builder.build(intent, _route(), "What is this?", "ios", attachments=attachments)
        assert ctx.attachments == attachments

    @pytest.mark.asyncio
    async def test_none_attachments_default_to_empty(self, builder: ContextBuilder) -> None:
        intent = Intent(agent=IntentType.GENERAL)
        ctx = await builder.build(intent, _route(), "Hi", "ios", attachments=None)
        assert ctx.attachments == []
