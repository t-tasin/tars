"""Approval endpoints — manage the HC-01 approval queue."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from shared.constants import ApprovalStatus
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import verify_api_key
from src.api.schemas import (
    ApprovalActionResponse,
    ApprovalDetail,
    ApprovalsListResponse,
    ApproveRequest,
    EditAndApproveRequest,
    ErrorDetail,
    ErrorResponse,
    RejectRequest,
)
from src.dependencies import get_db
from src.orchestrator.approval_manager import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalManager,
    ApprovalNotFoundError,
)

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["approvals"])

# Module-level manager instance (stateless, safe to share).
_approval_manager = ApprovalManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_detail(raw: dict[str, Any]) -> ApprovalDetail:
    """Convert ApprovalManager dict to ApprovalDetail schema."""
    return ApprovalDetail(
        id=raw["id"],
        action_type=raw["action_type"],
        risk_tier=raw["risk_tier"],
        status=raw["status"],
        title=raw["title"],
        preview=raw["preview_payload"],
        expires_at=raw["expires_at"],
        created_at=raw["created_at"],
    )


def _not_found(approval_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error=ErrorDetail(
                code="approval_not_found",
                message=f"Approval {approval_id} not found.",
            ),
        ).model_dump(),
    )


def _already_decided(approval_id: UUID, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=ErrorResponse(
            error=ErrorDetail(
                code="approval_already_decided",
                message=message,
            ),
        ).model_dump(),
    )


def _expired(approval_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=410,
        detail=ErrorResponse(
            error=ErrorDetail(
                code="approval_expired",
                message=f"Approval {approval_id} has expired.",
            ),
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/approvals",
    response_model=ApprovalsListResponse,
    responses={
        401: {"model": ErrorResponse},
    },
)
async def list_approvals(
    status: ApprovalStatus | None = Query(default=None, description="Filter by status (default: pending)"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> ApprovalsListResponse:
    """List approvals. Defaults to pending approvals."""
    # The ApprovalManager currently only supports querying pending approvals.
    # If a non-pending status is requested, return empty results.
    if status is not None and status != ApprovalStatus.PENDING:
        return ApprovalsListResponse(approvals=[], total=0, pending_count=0)

    approvals_raw, total = await _approval_manager.get_pending(
        session,
        limit=limit,
        offset=offset,
    )

    return ApprovalsListResponse(
        approvals=[_to_detail(a) for a in approvals_raw],
        total=total,
        pending_count=total,
    )


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
    },
)
async def approve_action(
    approval_id: UUID,
    body: ApproveRequest,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> ApprovalActionResponse:
    """Approve a pending action."""
    try:
        result = await _approval_manager.approve(
            session,
            approval_id,
            source=body.source,
        )
    except ApprovalNotFoundError:
        raise _not_found(approval_id)
    except ApprovalExpiredError:
        raise _expired(approval_id)
    except ApprovalAlreadyDecidedError as exc:
        raise _already_decided(approval_id, str(exc))

    return ApprovalActionResponse(
        approval_id=approval_id,
        status=result["status"],
    )


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=ApprovalActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
    },
)
async def reject_action(
    approval_id: UUID,
    body: RejectRequest,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> ApprovalActionResponse:
    """Reject a pending action."""
    try:
        result = await _approval_manager.reject(
            session,
            approval_id,
            source=body.source,
            reason=body.reason,
        )
    except ApprovalNotFoundError:
        raise _not_found(approval_id)
    except ApprovalExpiredError:
        raise _expired(approval_id)
    except ApprovalAlreadyDecidedError as exc:
        raise _already_decided(approval_id, str(exc))

    return ApprovalActionResponse(
        approval_id=approval_id,
        status=result["status"],
    )


@router.post(
    "/approvals/{approval_id}/edit",
    response_model=ApprovalActionResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
    },
)
async def edit_and_approve_action(
    approval_id: UUID,
    body: EditAndApproveRequest,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> ApprovalActionResponse:
    """Edit the payload and approve a pending action."""
    try:
        result = await _approval_manager.edit_and_approve(
            session,
            approval_id,
            source=body.source,
            edited_payload=body.edited_payload,
        )
    except ApprovalNotFoundError:
        raise _not_found(approval_id)
    except ApprovalExpiredError:
        raise _expired(approval_id)
    except ApprovalAlreadyDecidedError as exc:
        raise _already_decided(approval_id, str(exc))

    return ApprovalActionResponse(
        approval_id=approval_id,
        status=result["status"],
    )
