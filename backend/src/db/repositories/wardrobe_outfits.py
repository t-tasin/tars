"""Repository for the wardrobe_outfits table."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WardrobeOutfit

log = structlog.get_logger()


class WardrobeOutfitRepository:
    """CRUD operations for the ``wardrobe_outfits`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> WardrobeOutfit:
        """Insert a new outfit suggestion."""
        outfit = WardrobeOutfit(**kwargs)
        self._session.add(outfit)
        await self._session.flush()
        return outfit

    async def get_latest(self, limit: int = 5) -> list[WardrobeOutfit]:
        """Fetch the most recent outfit suggestions."""
        result = await self._session.execute(
            select(WardrobeOutfit).order_by(WardrobeOutfit.outfit_date.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_date(self, target_date: date) -> list[WardrobeOutfit]:
        """Fetch outfit suggestions for a specific date."""
        result = await self._session.execute(
            select(WardrobeOutfit)
            .where(WardrobeOutfit.outfit_date == target_date)
            .order_by(WardrobeOutfit.created_at.desc())
        )
        return list(result.scalars().all())
