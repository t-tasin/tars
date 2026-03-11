"""Approval queue manager for T.A.R.S. — enforces HC-01.

Every action with external side effects flows through this module.
The manager handles the full lifecycle: create → decide → execute → audit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Approval, AuditLog
from shared.constants import ApprovalStatus, RiskTier, TIER_MAP

log = structlog.get_logger("approval_manager")


# ---------------------------------------------------------------------------
# Exceptions — re-exported from utils.errors for backward compatibility
# ---------------------------------------------------------------------------

from utils.errors import (  # noqa: E402
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ApprovalManager:
    """Manages the approval lifecycle for Tier 2 and Tier 3 actions.

    All public methods accept an ``AsyncSession`` so the caller controls
    the transaction boundary (consistent with the repository pattern used
    elsewhere in T.A.R.S.).
    """

    DEFAULT_EXPIRY: timedelta = timedelta(hours=1)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        action_type: str,
        title: str,
        preview_payload: dict[str, Any],
        task_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a pending approval record.

        Parameters
        ----------
        session:
            Active database session (caller commits).
        action_type:
            Key into :data:`TIER_MAP` (e.g. ``"send_email"``).
        title:
            Human-readable description shown to the user.
        preview_payload:
            Full preview of the proposed action.
        task_id:
            Optional link to the originating :class:`AgentTask`.

        Returns
        -------
        dict
            Serialised approval suitable for WebSocket / APNs push.
        """
        risk_tier = TIER_MAP.get(action_type, RiskTier.TIER2_APPROVAL)
        now = datetime.now(timezone.utc)

        approval = Approval(
            task_id=task_id,
            action_type=action_type,
            risk_tier=risk_tier,
            status=ApprovalStatus.PENDING,
            title=title,
            preview_payload=preview_payload,
            expires_at=now + self.DEFAULT_EXPIRY,
        )
        session.add(approval)
        await session.flush()  # populate id

        await self._audit(
            session,
            action_type="approval_created",
            actor="system",
            target=str(approval.id),
            details={
                "action_type": action_type,
                "risk_tier": str(risk_tier),
                "task_id": str(task_id) if task_id else None,
                "title": title,
            },
        )

        log.info(
            "approval_created",
            approval_id=str(approval.id),
            action_type=action_type,
            risk_tier=str(risk_tier),
            task_id=str(task_id) if task_id else None,
        )

        return self._to_dict(approval)

    # ------------------------------------------------------------------
    # Decide
    # ------------------------------------------------------------------

    async def approve(
        self,
        session: AsyncSession,
        approval_id: UUID,
        *,
        source: str,
    ) -> dict[str, Any]:
        """Approve a pending action.

        Raises :class:`ApprovalNotFoundError`, :class:`ApprovalExpiredError`,
        or :class:`ApprovalAlreadyDecidedError` on invalid state.
        """
        approval = await self._get_and_validate(session, approval_id)

        approval.status = ApprovalStatus.APPROVED
        approval.decision_source = source
        approval.decided_at = datetime.now(timezone.utc)

        await self._audit(
            session,
            action_type="approval_approved",
            actor=source,
            target=str(approval_id),
            details={"action_type": approval.action_type},
            source=source,
        )

        log.info("approval_approved", approval_id=str(approval_id), source=source)
        return self._to_dict(approval)

    async def reject(
        self,
        session: AsyncSession,
        approval_id: UUID,
        *,
        source: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Reject a pending action."""
        approval = await self._get_and_validate(session, approval_id)

        approval.status = ApprovalStatus.REJECTED
        approval.decision_source = source
        approval.decided_at = datetime.now(timezone.utc)

        await self._audit(
            session,
            action_type="approval_rejected",
            actor=source,
            target=str(approval_id),
            details={"action_type": approval.action_type, "reason": reason},
            source=source,
        )

        log.info("approval_rejected", approval_id=str(approval_id), source=source, reason=reason)
        return self._to_dict(approval)

    async def edit_and_approve(
        self,
        session: AsyncSession,
        approval_id: UUID,
        *,
        source: str,
        edited_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Edit the payload then approve."""
        approval = await self._get_and_validate(session, approval_id)

        approval.status = ApprovalStatus.EDITED
        approval.decision_source = source
        approval.decided_at = datetime.now(timezone.utc)
        approval.edited_payload = edited_payload

        await self._audit(
            session,
            action_type="approval_edited",
            actor=source,
            target=str(approval_id),
            details={
                "action_type": approval.action_type,
                "edited_keys": list(edited_payload.keys()),
            },
            source=source,
        )

        log.info("approval_edited_and_approved", approval_id=str(approval_id), source=source)
        return self._to_dict(approval)

    # ------------------------------------------------------------------
    # Post-execution
    # ------------------------------------------------------------------

    async def mark_executed(
        self,
        session: AsyncSession,
        approval_id: UUID,
        *,
        result: str,
    ) -> None:
        """Mark an approved/edited approval as executed."""
        approval = await self._get_by_id(session, approval_id)

        if approval.status not in (ApprovalStatus.APPROVED, ApprovalStatus.EDITED):
            raise ApprovalAlreadyDecidedError(
                f"Cannot mark as executed: approval {approval_id} has status {approval.status}"
            )

        approval.status = ApprovalStatus.EXECUTED
        approval.executed_at = datetime.now(timezone.utc)

        await self._audit(
            session,
            action_type="approval_executed",
            actor="system",
            target=str(approval_id),
            details={"action_type": approval.action_type, "result": result[:500]},
        )

        log.info("approval_executed", approval_id=str(approval_id))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_pending(
        self,
        session: AsyncSession,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return pending approvals and total count.

        Returns
        -------
        tuple[list[dict], int]
            (list of approval dicts, total pending count)
        """
        base = select(Approval).where(Approval.status == ApprovalStatus.PENDING)

        count_result = await session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar_one()

        rows_result = await session.execute(
            base.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
        )
        approvals = list(rows_result.scalars().all())

        return [self._to_dict(a) for a in approvals], total

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    async def expire_stale(self, session: AsyncSession) -> int:
        """Expire all pending approvals past their ``expires_at``.

        Intended to be called periodically by the scheduler.

        Returns
        -------
        int
            Number of approvals expired.
        """
        now = datetime.now(timezone.utc)

        stmt = (
            update(Approval)
            .where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at <= now,
            )
            .values(status=ApprovalStatus.EXPIRED)
            .returning(Approval.id)
        )
        result = await session.execute(stmt)
        expired_ids = list(result.scalars().all())

        for eid in expired_ids:
            await self._audit(
                session,
                action_type="approval_expired",
                actor="system",
                target=str(eid),
                details={},
            )

        if expired_ids:
            log.info("approvals_expired", count=len(expired_ids))

        return len(expired_ids)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_by_id(self, session: AsyncSession, approval_id: UUID) -> Approval:
        """Fetch an approval by ID or raise :class:`ApprovalNotFoundError`."""
        result = await session.execute(
            select(Approval).where(Approval.id == approval_id)
        )
        approval = result.scalar_one_or_none()
        if approval is None:
            raise ApprovalNotFoundError(f"Approval {approval_id} not found")
        return approval

    async def _get_and_validate(self, session: AsyncSession, approval_id: UUID) -> Approval:
        """Fetch an approval and verify it is still actionable."""
        approval = await self._get_by_id(session, approval_id)

        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalAlreadyDecidedError(
                f"Approval {approval_id} already has status {approval.status}"
            )

        now = datetime.now(timezone.utc)
        if approval.expires_at.tzinfo is None:
            expires_at = approval.expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = approval.expires_at

        if expires_at <= now:
            approval.status = ApprovalStatus.EXPIRED
            raise ApprovalExpiredError(f"Approval {approval_id} expired at {approval.expires_at}")

        return approval

    @staticmethod
    async def _audit(
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

    @staticmethod
    def _to_dict(approval: Approval) -> dict[str, Any]:
        """Serialise an :class:`Approval` to a plain dict for API / WS responses."""
        return {
            "id": str(approval.id),
            "task_id": str(approval.task_id) if approval.task_id else None,
            "action_type": approval.action_type,
            "risk_tier": str(approval.risk_tier),
            "status": str(approval.status),
            "title": approval.title,
            "preview_payload": approval.preview_payload,
            "edited_payload": approval.edited_payload,
            "decision_source": approval.decision_source,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            "executed_at": approval.executed_at.isoformat() if approval.executed_at else None,
            "expires_at": approval.expires_at.isoformat(),
            "created_at": approval.created_at.isoformat() if approval.created_at else None,
        }
