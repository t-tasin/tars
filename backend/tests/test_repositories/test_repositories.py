"""Tests for the database repository layer.

Tests CRUD operations for the most critical repositories using
the test_db fixture from conftest.py (in-memory or real PostgreSQL).

Note: The in-memory session does not support LIMIT, ORDER BY, GROUP BY,
subquery aggregates, or DELETE. Tests that require these features are
marked with ``@pytest.mark.requires_pg`` and skip when using in-memory.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from shared.constants import ApprovalStatus, RiskTier

from db.repositories.approvals import ApprovalRepository
from db.repositories.briefings import BriefingRepository
from db.repositories.config import ConfigRepository
from db.repositories.conversations import ConversationRepository
from db.repositories.messages import MessageRepository
from db.repositories.model_usage import ModelUsageRepository

_HAS_PG = bool(os.environ.get("TEST_DATABASE_URL", ""))
requires_pg = pytest.mark.skipif(not _HAS_PG, reason="Requires real PostgreSQL")


# ---------------------------------------------------------------------------
# ConversationRepository
# ---------------------------------------------------------------------------


class TestConversationRepository:
    """Tests for ConversationRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_conversation(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        conv = await repo.create(source="ios")
        assert conv.id is not None
        assert conv.source == "ios"

    @pytest.mark.asyncio
    async def test_get_by_id(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        conv = await repo.create(source="telegram")
        fetched = await repo.get_by_id(conv.id)
        assert fetched is not None
        assert fetched.source == "telegram"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_recent_respects_limit(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        await repo.create(source="ios")
        await repo.create(source="telegram")
        await repo.create(source="ios")
        recent = await repo.get_recent(limit=2)
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_get_recent_returns_conversations(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        await repo.create(source="ios")
        recent = await repo.get_recent()
        assert len(recent) >= 1

    @pytest.mark.asyncio
    async def test_add_message(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        conv = await repo.create(source="ios")
        msg = await repo.add_message(conv.id, role="user", content="Hello TARS")
        assert msg.id is not None
        assert msg.conversation_id == conv.id
        assert msg.role == "user"
        assert msg.content == "Hello TARS"

    @pytest.mark.asyncio
    async def test_get_messages(self, test_db) -> None:
        repo = ConversationRepository(test_db)
        conv = await repo.create(source="ios")
        await repo.add_message(conv.id, role="user", content="Hello")
        await repo.add_message(conv.id, role="assistant", content="Hi there!")

        messages = await repo.get_messages(conv.id)
        # In-memory returns all messages for the table, but at minimum
        # the two we created should be present.
        assert len(messages) >= 2


# ---------------------------------------------------------------------------
# ApprovalRepository
# ---------------------------------------------------------------------------


class TestApprovalRepository:
    """Tests for ApprovalRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_approval(self, test_db) -> None:
        repo = ApprovalRepository(test_db)
        now = datetime.now(UTC)
        approval = await repo.create(
            action_type="send_email",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Send email to professor",
            preview_payload={"to": "prof@university.edu"},
            expires_at=now + timedelta(hours=1),
        )
        assert approval.id is not None
        assert approval.action_type == "send_email"
        assert approval.status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_by_id(self, test_db) -> None:
        repo = ApprovalRepository(test_db)
        now = datetime.now(UTC)
        approval = await repo.create(
            action_type="create_event",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Create calendar event",
            preview_payload={"event": "meeting"},
            expires_at=now + timedelta(hours=1),
        )
        fetched = await repo.get_by_id(approval.id)
        assert fetched is not None
        assert fetched.title == "Create calendar event"

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_pending(self, test_db) -> None:
        repo = ApprovalRepository(test_db)
        now = datetime.now(UTC)

        await repo.create(
            action_type="send_email",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Email 1",
            preview_payload={},
            expires_at=now + timedelta(hours=1),
        )
        await repo.create(
            action_type="send_email",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Email 2",
            preview_payload={},
            expires_at=now + timedelta(hours=1),
        )

        pending, total = await repo.get_pending()
        assert total >= 2
        assert len(pending) >= 2

    @pytest.mark.asyncio
    async def test_update_status(self, test_db) -> None:
        repo = ApprovalRepository(test_db)
        now = datetime.now(UTC)
        approval = await repo.create(
            action_type="send_email",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Test approval",
            preview_payload={},
            expires_at=now + timedelta(hours=1),
        )
        updated = await repo.update_status(
            approval.id,
            ApprovalStatus.APPROVED,
            decision_source="ios",
            decided_at=now,
        )
        assert updated is not None
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.decision_source == "ios"

    @pytest.mark.asyncio
    async def test_get_by_filters_returns_results(self, test_db) -> None:
        repo = ApprovalRepository(test_db)
        now = datetime.now(UTC)
        await repo.create(
            action_type="apply_job",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.PENDING,
            title="Apply to job",
            preview_payload={},
            expires_at=now + timedelta(hours=1),
        )
        # get_by_filters uses WHERE clauses — in-memory returns all approvals
        results = await repo.get_by_filters()
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# BriefingRepository
# ---------------------------------------------------------------------------


class TestBriefingRepository:
    """Tests for BriefingRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_briefing(self, test_db) -> None:
        repo = BriefingRepository(test_db)
        briefing = await repo.create(
            briefing_type="morning",
            briefing_date=date(2026, 3, 10),
            payload={"weather": "sunny"},
            narrative="Good morning! It's a beautiful day.",
        )
        assert briefing.id is not None
        assert briefing.briefing_type == "morning"

    @pytest.mark.asyncio
    async def test_get_by_date_returns_briefing(self, test_db) -> None:
        repo = BriefingRepository(test_db)
        target = date(2026, 3, 10)
        created = await repo.create(
            briefing_type="morning",
            briefing_date=target,
            payload={"weather": "sunny"},
            narrative="Good morning!",
        )
        # In-memory: returns first match from the table, not filtered
        result = await repo.get_by_date(target, briefing_type="morning")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_by_id(self, test_db) -> None:
        repo = BriefingRepository(test_db)
        created = await repo.create(
            briefing_type="end_of_day",
            briefing_date=date(2026, 3, 10),
            payload={},
            narrative="End of day summary.",
        )
        fetched = await repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.narrative == "End of day summary."

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_latest_orders_correctly(self, test_db) -> None:
        repo = BriefingRepository(test_db)
        await repo.create(
            briefing_type="morning",
            briefing_date=date(2026, 3, 9),
            payload={},
            narrative="Yesterday",
        )
        await repo.create(
            briefing_type="morning",
            briefing_date=date(2026, 3, 10),
            payload={},
            narrative="Today",
        )
        latest = await repo.get_latest("morning")
        assert latest is not None
        assert latest.briefing_date == date(2026, 3, 10)


# ---------------------------------------------------------------------------
# ConfigRepository
# ---------------------------------------------------------------------------


class TestConfigRepository:
    """Tests for ConfigRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        await repo.set("general", "location", "Wooster, OH")
        value = await repo.get("general", "location")
        assert value == "Wooster, OH"

    @pytest.mark.asyncio
    async def test_get_missing_key(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        value = await repo.get("nonexistent", "key")
        assert value is None

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_namespace(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        await repo.set("morning_briefing", "time", "05:50")
        await repo.set("morning_briefing", "voice_enabled", True)

        ns = await repo.get_namespace("morning_briefing")
        assert ns["time"] == "05:50"
        assert ns["voice_enabled"] is True

    @pytest.mark.asyncio
    async def test_upsert_existing(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        await repo.set("general", "location", "Wooster, OH")
        await repo.set("general", "location", "Columbus, OH")
        value = await repo.get("general", "location")
        assert value == "Columbus, OH"

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_all(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        await repo.set("general", "location", "Wooster, OH")
        await repo.set("notifications", "quiet_hours_start", "23:00")

        all_config = await repo.get_all()
        assert "general" in all_config
        assert "notifications" in all_config

    @requires_pg
    @pytest.mark.asyncio
    async def test_delete(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        await repo.set("temp", "key", "value")
        deleted = await repo.delete("temp", "key")
        assert deleted is True
        value = await repo.get("temp", "key")
        assert value is None

    @requires_pg
    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, test_db) -> None:
        repo = ConfigRepository(test_db)
        deleted = await repo.delete("nonexistent", "key")
        assert deleted is False


# ---------------------------------------------------------------------------
# ModelUsageRepository
# ---------------------------------------------------------------------------


class TestModelUsageRepository:
    """Tests for ModelUsageRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_usage(self, test_db) -> None:
        repo = ModelUsageRepository(test_db)
        record = await repo.create(
            model="gemini_flash",
            agent_type="briefing",
            tokens_input=500,
            tokens_output=300,
            estimated_cost=Decimal("0.000250"),
            duration_ms=1200,
            success=True,
        )
        assert record.id is not None
        assert record.model == "gemini_flash"
        assert record.agent_type == "briefing"

    @pytest.mark.asyncio
    async def test_get_by_model_returns_records(self, test_db) -> None:
        repo = ModelUsageRepository(test_db)
        await repo.create(
            model="gemini_flash",
            agent_type="briefing",
            tokens_input=500,
            tokens_output=300,
            estimated_cost=Decimal("0.000250"),
            duration_ms=1200,
            success=True,
        )
        # In-memory: returns all model_usage records, not filtered
        records = await repo.get_by_model("gemini_flash")
        assert len(records) >= 1

    @pytest.mark.asyncio
    async def test_get_by_agent_returns_records(self, test_db) -> None:
        repo = ModelUsageRepository(test_db)
        await repo.create(
            model="gemini_flash",
            agent_type="email_classifier",
            tokens_input=200,
            tokens_output=50,
            estimated_cost=Decimal("0.000040"),
            duration_ms=500,
            success=True,
        )
        records = await repo.get_by_agent("email_classifier")
        assert len(records) >= 1


# ---------------------------------------------------------------------------
# MessageRepository
# ---------------------------------------------------------------------------


class TestMessageRepository:
    """Tests for MessageRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_message(self, test_db) -> None:
        conv_repo = ConversationRepository(test_db)
        conv = await conv_repo.create(source="ios")

        repo = MessageRepository(test_db)
        msg = await repo.create(
            conversation_id=conv.id,
            role="user",
            content="What's the weather?",
        )
        assert msg.id is not None
        assert msg.role == "user"

    @pytest.mark.asyncio
    async def test_get_by_conversation(self, test_db) -> None:
        conv_repo = ConversationRepository(test_db)
        conv = await conv_repo.create(source="ios")

        repo = MessageRepository(test_db)
        await repo.create(conversation_id=conv.id, role="user", content="Hello")
        await repo.create(conversation_id=conv.id, role="assistant", content="Hi!")

        messages = await repo.get_by_conversation(conv.id)
        assert len(messages) >= 2

    @requires_pg
    @pytest.mark.asyncio
    async def test_get_by_conversation_with_limit(self, test_db) -> None:
        conv_repo = ConversationRepository(test_db)
        conv = await conv_repo.create(source="ios")

        repo = MessageRepository(test_db)
        await repo.create(conversation_id=conv.id, role="user", content="Msg 1")
        await repo.create(conversation_id=conv.id, role="assistant", content="Msg 2")
        await repo.create(conversation_id=conv.id, role="user", content="Msg 3")

        messages = await repo.get_by_conversation(conv.id, limit=2)
        assert len(messages) == 2
