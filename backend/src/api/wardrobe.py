"""Wardrobe and outfit API endpoints for T.A.R.S."""

from __future__ import annotations

import base64
import uuid
from datetime import date
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    ErrorDetail,
    ErrorResponse,
    OutfitResponse,
    WardrobeUploadResponse,
)
from src.db.models import WardrobeItem, WardrobeOutfit
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["wardrobe"])

# Maximum upload size: 10 MB
_MAX_IMAGE_SIZE = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas (wardrobe-specific, not in the shared schemas file)
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class WardrobeItemDetail(BaseModel):
    id: uuid.UUID
    item_type: str
    sub_type: str | None
    color: str
    pattern: str | None
    seasons: list[str]
    formality: str
    brand: str | None
    image_path: str
    last_worn: str | None
    wear_count: int
    active: bool
    created_at: str


class WardrobeListResponse(BaseModel):
    items: list[WardrobeItemDetail]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/wardrobe/upload",
    response_model=WardrobeUploadResponse,
    status_code=201,
)
async def upload_wardrobe_item(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> WardrobeUploadResponse:
    """Upload and catalog a new wardrobe item from a photo.

    Accepts JPEG or PNG images up to 10 MB. The image is analyzed by
    Gemini Vision and stored locally on Node 2 (HC-11).
    """
    # Validate content type
    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_content_type",
                    message="Only JPEG and PNG images are accepted",
                ),
            ).model_dump(),
        )

    # Read and validate size
    image_bytes = await file.read()
    if len(image_bytes) > _MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="file_too_large",
                    message=f"Image exceeds {_MAX_IMAGE_SIZE // (1024 * 1024)} MB limit",
                ),
            ).model_dump(),
        )

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                error=ErrorDetail(code="empty_file", message="Uploaded file is empty"),
            ).model_dump(),
        )

    # Run the fashion agent's catalog action
    from src.agents.base import AgentContext
    from src.agents.fashion import FashionAgent

    agent = FashionAgent()
    context = AgentContext(
        user_message="Catalog this clothing item",
        intent_type="fashion",
        config={"action": "catalog"},
        attachments=[{
            "type": "image",
            "data": base64.b64encode(image_bytes).decode(),
            "mime_type": file.content_type or "image/jpeg",
        }],
    )

    result = await agent.execute(context)

    if not result.success:
        raise HTTPException(
            status_code=502,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="catalog_failed",
                    message=result.error or "Failed to catalog item",
                ),
            ).model_dump(),
        )

    log.info(
        "wardrobe_item_uploaded",
        item_id=result.data.get("wardrobe_item_id"),
        item_type=result.data.get("analysis", {}).get("item_type"),
    )

    return WardrobeUploadResponse(
        wardrobe_item_id=uuid.UUID(result.data["wardrobe_item_id"]),
        analysis=result.data.get("analysis", {}),
        message=result.text,
    )


@router.get("/outfit", response_model=OutfitResponse)
async def get_outfit(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date: date | None = None,
    regenerate: bool = False,
) -> OutfitResponse:
    """Get today's outfit suggestion.

    Returns a cached suggestion if one exists for the requested date,
    unless ``regenerate=True`` is passed.
    """
    target_date = date or _today()

    # Check for existing suggestion (unless regenerating)
    if not regenerate:
        result = await db.execute(
            select(WardrobeOutfit)
            .where(WardrobeOutfit.outfit_date == target_date)
            .order_by(WardrobeOutfit.created_at.desc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            log.info("outfit_cached_hit", outfit_id=str(existing.id), date=target_date.isoformat())
            return OutfitResponse(
                outfit_id=existing.id,
                date=target_date.isoformat(),
                suggestion=existing.reasoning or "Here's your outfit for today.",
                reasoning=existing.reasoning or "",
                items=existing.items if isinstance(existing.items, list) else [],
                weather=existing.weather_context or {},
            )

    # Generate a new suggestion
    from src.agents.base import AgentContext
    from src.agents.fashion import FashionAgent

    agent = FashionAgent()
    context = AgentContext(
        user_message="What should I wear today?",
        intent_type="fashion",
        config={"action": "suggest"},
    )

    agent_result = await agent.execute(context)

    if not agent_result.success:
        raise HTTPException(
            status_code=502,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code="suggestion_failed",
                    message=agent_result.error or "Failed to generate outfit suggestion",
                ),
            ).model_dump(),
        )

    suggestion_data = agent_result.data.get("suggestion", {})

    return OutfitResponse(
        outfit_id=uuid.UUID(agent_result.data["outfit_id"]),
        date=target_date.isoformat(),
        suggestion=suggestion_data.get("suggestion", agent_result.text),
        reasoning=suggestion_data.get("reasoning", ""),
        items=suggestion_data.get("items", []),
        weather=agent_result.data.get("weather", {}),
    )


@router.get("/wardrobe", response_model=WardrobeListResponse)
async def list_wardrobe(
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    item_type: str | None = None,
    formality: str | None = None,
    active: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WardrobeListResponse:
    """List wardrobe items with optional filtering."""
    query = select(WardrobeItem).where(WardrobeItem.active == active)

    if item_type:
        query = query.where(WardrobeItem.item_type == item_type)
    if formality:
        query = query.where(WardrobeItem.formality == formality)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Fetch paginated items
    query = query.order_by(WardrobeItem.item_type, WardrobeItem.color)
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all()

    items = [
        WardrobeItemDetail(
            id=row.id,
            item_type=row.item_type,
            sub_type=row.sub_type,
            color=row.color,
            pattern=row.pattern,
            seasons=row.seasons if isinstance(row.seasons, list) else [],
            formality=row.formality,
            brand=row.brand,
            image_path=row.image_path,
            last_worn=row.last_worn.isoformat() if row.last_worn else None,
            wear_count=row.wear_count,
            active=row.active,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]

    log.info("wardrobe_listed", total=total, returned=len(items), filters={"item_type": item_type, "formality": formality})

    return WardrobeListResponse(items=items, total=total)


def _today() -> date:
    """Return today's date. Extracted for testability."""
    return date.today()
