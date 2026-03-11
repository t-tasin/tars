"""Message endpoint — primary entry point for all T.A.R.S. interactions."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth import verify_api_key
from src.api.schemas import ErrorDetail, ErrorResponse, SendMessageRequest, TARSResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["messages"])


@router.post(
    "/message",
    response_model=TARSResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing credentials"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def send_message(
    body: SendMessageRequest,
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> TARSResponse:
    """Accept a user message and route it through the orchestrator pipeline.

    Returns the full T.A.R.S. response including conversation context,
    agent used, model used, and optional approval payload.
    """
    # Lazy import to avoid circular imports at module load time.
    # The orchestrator depends on config/db which may not be ready
    # when API modules are first imported.
    from src.orchestrator.engine import get_orchestrator

    orchestrator = get_orchestrator()

    # Convert Pydantic attachment models to plain dicts for the orchestrator
    attachments: list[dict[str, Any]] = []
    if body.attachments:
        attachments = [att.model_dump() for att in body.attachments]

    try:
        result = await orchestrator.process_message(
            text=body.text,
            source=body.source,
            conversation_id=body.conversation_id,
            attachments=attachments,
        )
    except Exception:
        log.exception(
            "message_processing_failed",
            source=body.source,
            conversation_id=str(body.conversation_id) if body.conversation_id else None,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="internal_error",
                    message="An unexpected error occurred while processing your message.",
                ),
            ).model_dump(),
        )

    return TARSResponse(**result)
