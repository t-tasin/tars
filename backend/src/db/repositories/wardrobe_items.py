"""Repository for the wardrobe_items table."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WardrobeItem

log = structlog.get_logger()


class WardrobeItemRepository:
    """CRUD operations for the ``wardrobe_items`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> WardrobeItem:
        """Insert a new wardrobe item."""
        item = WardrobeItem(**kwargs)
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_by_id(self, item_id: uuid.UUID) -> WardrobeItem | None:
        """Fetch a wardrobe item by primary key."""
        result = await self._session.execute(
            select(WardrobeItem).where(WardrobeItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, active_only: bool = True) -> list[WardrobeItem]:
        """Fetch all wardrobe items, optionally filtered to active only."""
        stmt = select(WardrobeItem)
        if active_only:
            stmt = stmt.where(WardrobeItem.active.is_(True))
        stmt = stmt.order_by(WardrobeItem.item_type, WardrobeItem.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_filters(
        self,
        season: str | None = None,
        formality: str | None = None,
        color: str | None = None,
        item_type: str | None = None,
        active_only: bool = True,
    ) -> list[WardrobeItem]:
        """Fetch wardrobe items matching optional filters."""
        stmt = select(WardrobeItem)
        if active_only:
            stmt = stmt.where(WardrobeItem.active.is_(True))
        if formality is not None:
            stmt = stmt.where(WardrobeItem.formality == formality)
        if color is not None:
            stmt = stmt.where(WardrobeItem.color == color)
        if item_type is not None:
            stmt = stmt.where(WardrobeItem.item_type == item_type)
        if season is not None:
            # seasons is a JSONB array — use containment operator
            stmt = stmt.where(WardrobeItem.seasons.op("@>")(f'["{season}"]'))
        stmt = stmt.order_by(WardrobeItem.item_type)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_count(self) -> dict[str, int]:
        """Return count of active items grouped by item_type."""
        result = await self._session.execute(
            select(WardrobeItem.item_type, func.count().label("count"))
            .where(WardrobeItem.active.is_(True))
            .group_by(WardrobeItem.item_type)
        )
        return {row.item_type: row.count for row in result.all()}
