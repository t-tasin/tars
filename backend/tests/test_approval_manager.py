"""Tests for the approval queue manager."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.constants import ApprovalStatus, RiskTier

from db.models import Approval, AuditLog
from orchestrator.approval_manager import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalManager,
    ApprovalNotFoundError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_approval(
    *,
    approval_id: uuid.UUID | None = None,
    action_type: str = "send_email",
    risk_tier: RiskTier = RiskTier.TIER2_APPROVAL,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    title: str = "Test approval",
    preview_payload: dict[str, Any] | None = None,
    edited_payload: dict[str, Any] | None = None,
    decision_source: str | None = None,
    decided_at: datetime | None = None,
    executed_at: datetime | None = None,
    expires_at: datetime | None = None,
    task_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> Approval:
    """Build a fake Approval ORM instance without touching the DB."""
    now = datetime.now(UTC)
    return Approval(
        id=approval_id or uuid.uuid4(),
        task_id=task_id,
        action_type=action_type,
        risk_tier=risk_tier,
        status=status,
        title=title,
        preview_payload=preview_payload or {},
        edited_payload=edited_payload,
        decision_source=decision_source,
        decided_at=decided_at,
        executed_at=executed_at,
        expires_at=expires_at or (now + timedelta(hours=1)),
        created_at=created_at or now,
        updated_at=now,
    )


def _mock_session() -> AsyncMock:
    """Return an AsyncMock that behaves enough like AsyncSession."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _mock_scalar_result(value):
    """Create a mock result whose .scalar_one_or_none() returns value."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar_one.return_value = value
    return result


def _mock_scalars_result(values):
    """Create a mock result whose .scalars().all() returns values."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager() -> ApprovalManager:
    return ApprovalManager()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreate:
    async def test_creates_pending_approval(self, manager: ApprovalManager) -> None:
        session = _mock_session()

        result = await manager.create(
            session,
            action_type="send_email",
            title="Send email to recruiter",
            preview_payload={"to": "recruiter@example.com"},
        )

        assert result["status"] == "pending"
        assert result["action_type"] == "send_email"
        assert result["risk_tier"] == str(RiskTier.TIER2_APPROVAL)
        assert result["title"] == "Send email to recruiter"
        assert result["preview_payload"] == {"to": "recruiter@example.com"}

        # Should have added Approval + AuditLog
        added_objects = [call.args[0] for call in session.add.call_args_list]
        assert any(isinstance(o, Approval) for o in added_objects)
        assert any(isinstance(o, AuditLog) for o in added_objects)

    async def test_tier2_action_gets_tier2(self, manager: ApprovalManager) -> None:
        session = _mock_session()

        result = await manager.create(
            session,
            action_type="create_pr",
            title="Create PR",
            preview_payload={},
        )

        assert result["risk_tier"] == str(RiskTier.TIER2_APPROVAL)

    async def test_tier3_action_gets_escalation_tier(self, manager: ApprovalManager) -> None:
        session = _mock_session()

        result = await manager.create(
            session,
            action_type="email_professor",
            title="Email Prof. Sadigh",
            preview_payload={"to": "sadigh@cs.stanford.edu"},
        )

        assert result["risk_tier"] == str(RiskTier.TIER3_ESCALATION)

    async def test_unknown_action_defaults_to_tier2(self, manager: ApprovalManager) -> None:
        session = _mock_session()

        result = await manager.create(
            session,
            action_type="unknown_action",
            title="Unknown",
            preview_payload={},
        )

        assert result["risk_tier"] == str(RiskTier.TIER2_APPROVAL)

    async def test_create_writes_audit_log(self, manager: ApprovalManager) -> None:
        session = _mock_session()

        await manager.create(
            session,
            action_type="send_email",
            title="Test",
            preview_payload={},
        )

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert len(audit_adds) == 1
        assert audit_adds[0].action_type == "approval_created"
        assert audit_adds[0].actor == "system"

    async def test_create_with_task_id(self, manager: ApprovalManager) -> None:
        session = _mock_session()
        task_id = uuid.uuid4()

        result = await manager.create(
            session,
            action_type="create_pr",
            title="Create PR",
            preview_payload={},
            task_id=task_id,
        )

        assert result["task_id"] == str(task_id)

    async def test_expires_at_approximately_one_hour(self, manager: ApprovalManager) -> None:
        session = _mock_session()
        before = datetime.now(UTC)

        result = await manager.create(
            session,
            action_type="send_email",
            title="Test",
            preview_payload={},
        )

        expires = datetime.fromisoformat(result["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        expected = before + timedelta(hours=1)
        assert abs((expires - expected).total_seconds()) < 5

    async def test_all_tier_map_actions(self, manager: ApprovalManager) -> None:
        """Every action in TIER_MAP should resolve to the expected tier."""
        from shared.constants import TIER_MAP

        session = _mock_session()
        for action_type, expected_tier in TIER_MAP.items():
            result = await manager.create(
                session,
                action_type=action_type,
                title=f"Test {action_type}",
                preview_payload={},
            )
            assert result["risk_tier"] == str(expected_tier), f"Failed for {action_type}"


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


class TestApprove:
    async def test_approve_pending(self, manager: ApprovalManager) -> None:
        approval = _make_approval()
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        result = await manager.approve(session, approval.id, source="ios")

        assert result["status"] == "approved"
        assert result["decision_source"] == "ios"
        assert result["decided_at"] is not None
        assert approval.status == ApprovalStatus.APPROVED

    async def test_approve_writes_audit_log(self, manager: ApprovalManager) -> None:
        approval = _make_approval()
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.approve(session, approval.id, source="telegram")

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert len(audit_adds) == 1
        assert audit_adds[0].action_type == "approval_approved"
        assert audit_adds[0].actor == "telegram"
        assert audit_adds[0].source == "telegram"


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


class TestReject:
    async def test_reject_pending(self, manager: ApprovalManager) -> None:
        approval = _make_approval()
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        result = await manager.reject(session, approval.id, source="ios", reason="Not now")

        assert result["status"] == "rejected"
        assert approval.status == ApprovalStatus.REJECTED

    async def test_reject_writes_audit_with_reason(self, manager: ApprovalManager) -> None:
        approval = _make_approval()
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.reject(session, approval.id, source="ios", reason="Bad idea")

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert audit_adds[0].details["reason"] == "Bad idea"


# ---------------------------------------------------------------------------
# Edit & approve
# ---------------------------------------------------------------------------


class TestEditAndApprove:
    async def test_edit_and_approve(self, manager: ApprovalManager) -> None:
        approval = _make_approval(preview_payload={"body": "Original"})
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        result = await manager.edit_and_approve(
            session,
            approval.id,
            source="ios",
            edited_payload={"body": "Revised"},
        )

        assert result["status"] == "edited"
        assert result["edited_payload"] == {"body": "Revised"}
        assert result["decision_source"] == "ios"
        assert approval.status == ApprovalStatus.EDITED

    async def test_edit_records_edited_keys_in_audit(self, manager: ApprovalManager) -> None:
        approval = _make_approval()
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.edit_and_approve(
            session,
            approval.id,
            source="watch",
            edited_payload={"subject": "new", "body": "new"},
        )

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert set(audit_adds[0].details["edited_keys"]) == {"subject", "body"}


# ---------------------------------------------------------------------------
# Mark executed
# ---------------------------------------------------------------------------


class TestMarkExecuted:
    async def test_mark_approved_as_executed(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.mark_executed(session, approval.id, result="Email sent")

        assert approval.status == ApprovalStatus.EXECUTED
        assert approval.executed_at is not None

    async def test_mark_edited_as_executed(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.EDITED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.mark_executed(session, approval.id, result="Done")

        assert approval.status == ApprovalStatus.EXECUTED

    async def test_cannot_execute_rejected(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.REJECTED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalAlreadyDecidedError):
            await manager.mark_executed(session, approval.id, result="Nope")

    async def test_cannot_execute_pending(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.PENDING)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalAlreadyDecidedError):
            await manager.mark_executed(session, approval.id, result="Nope")

    async def test_execute_writes_audit(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        await manager.mark_executed(session, approval.id, result="Sent successfully")

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert len(audit_adds) == 1
        assert audit_adds[0].action_type == "approval_executed"

    async def test_execute_truncates_long_result(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        long_result = "x" * 1000
        await manager.mark_executed(session, approval.id, result=long_result)

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert len(audit_adds[0].details["result"]) == 500


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    async def test_approve_nonexistent_raises(self, manager: ApprovalManager) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))

        with pytest.raises(ApprovalNotFoundError):
            await manager.approve(session, uuid.uuid4(), source="ios")

    async def test_reject_nonexistent_raises(self, manager: ApprovalManager) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(None))

        with pytest.raises(ApprovalNotFoundError):
            await manager.reject(session, uuid.uuid4(), source="ios")

    async def test_approve_already_approved_raises(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalAlreadyDecidedError):
            await manager.approve(session, approval.id, source="telegram")

    async def test_reject_already_rejected_raises(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.REJECTED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalAlreadyDecidedError):
            await manager.reject(session, approval.id, source="telegram")

    async def test_edit_already_approved_raises(self, manager: ApprovalManager) -> None:
        approval = _make_approval(status=ApprovalStatus.APPROVED)
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalAlreadyDecidedError):
            await manager.edit_and_approve(session, approval.id, source="ios", edited_payload={})

    async def test_approve_expired_raises(self, manager: ApprovalManager) -> None:
        approval = _make_approval(
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalExpiredError):
            await manager.approve(session, approval.id, source="ios")

        # Should also flip status to expired
        assert approval.status == ApprovalStatus.EXPIRED

    async def test_reject_expired_raises(self, manager: ApprovalManager) -> None:
        approval = _make_approval(
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session = _mock_session()
        session.execute = AsyncMock(return_value=_mock_scalar_result(approval))

        with pytest.raises(ApprovalExpiredError):
            await manager.reject(session, approval.id, source="ios")


# ---------------------------------------------------------------------------
# Get pending
# ---------------------------------------------------------------------------


class TestGetPending:
    async def test_returns_pending_list_and_count(self, manager: ApprovalManager) -> None:
        approvals = [_make_approval(title=f"Item {i}") for i in range(3)]
        session = _mock_session()

        # First execute: count query
        count_result = _mock_scalar_result(3)
        # Second execute: rows query
        rows_result = _mock_scalars_result(approvals)
        session.execute = AsyncMock(side_effect=[count_result, rows_result])

        pending, total = await manager.get_pending(session)

        assert total == 3
        assert len(pending) == 3
        assert all(isinstance(p, dict) for p in pending)

    async def test_empty_when_no_pending(self, manager: ApprovalManager) -> None:
        session = _mock_session()
        count_result = _mock_scalar_result(0)
        rows_result = _mock_scalars_result([])
        session.execute = AsyncMock(side_effect=[count_result, rows_result])

        pending, total = await manager.get_pending(session)

        assert total == 0
        assert pending == []


# ---------------------------------------------------------------------------
# Expire stale
# ---------------------------------------------------------------------------


class TestExpireStale:
    async def test_expires_past_due_returns_count(self, manager: ApprovalManager) -> None:
        expired_ids = [uuid.uuid4(), uuid.uuid4()]
        result = _mock_scalars_result(expired_ids)
        session = _mock_session()
        session.execute = AsyncMock(return_value=result)

        count = await manager.expire_stale(session)

        assert count == 2

    async def test_expire_writes_audit_for_each(self, manager: ApprovalManager) -> None:
        expired_ids = [uuid.uuid4(), uuid.uuid4()]
        result = _mock_scalars_result(expired_ids)
        session = _mock_session()
        session.execute = AsyncMock(return_value=result)

        await manager.expire_stale(session)

        audit_adds = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], AuditLog)]
        assert len(audit_adds) == 2
        assert all(a.action_type == "approval_expired" for a in audit_adds)

    async def test_no_expiry_returns_zero(self, manager: ApprovalManager) -> None:
        result = _mock_scalars_result([])
        session = _mock_session()
        session.execute = AsyncMock(return_value=result)

        count = await manager.expire_stale(session)

        assert count == 0


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class TestToDict:
    def test_serialises_all_fields(self, manager: ApprovalManager) -> None:
        now = datetime.now(UTC)
        aid = uuid.uuid4()
        tid = uuid.uuid4()
        approval = _make_approval(
            approval_id=aid,
            task_id=tid,
            action_type="send_email",
            risk_tier=RiskTier.TIER2_APPROVAL,
            status=ApprovalStatus.APPROVED,
            title="Test",
            preview_payload={"key": "val"},
            edited_payload={"key": "edited"},
            decision_source="ios",
            decided_at=now,
            executed_at=now,
            expires_at=now + timedelta(hours=1),
            created_at=now,
        )

        d = manager._to_dict(approval)

        assert d["id"] == str(aid)
        assert d["task_id"] == str(tid)
        assert d["action_type"] == "send_email"
        assert d["risk_tier"] == str(RiskTier.TIER2_APPROVAL)
        assert d["status"] == "approved"
        assert d["title"] == "Test"
        assert d["preview_payload"] == {"key": "val"}
        assert d["edited_payload"] == {"key": "edited"}
        assert d["decision_source"] == "ios"
        assert d["decided_at"] == now.isoformat()
        assert d["executed_at"] == now.isoformat()

    def test_none_task_id_serialises_as_none(self, manager: ApprovalManager) -> None:
        approval = _make_approval(task_id=None)
        d = manager._to_dict(approval)
        assert d["task_id"] is None

    def test_none_timestamps_serialise_as_none(self, manager: ApprovalManager) -> None:
        approval = _make_approval(decided_at=None, executed_at=None)
        d = manager._to_dict(approval)
        assert d["decided_at"] is None
        assert d["executed_at"] is None
