"""Repository for the contacts table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Contact

log = structlog.get_logger()


class ContactRepository:
    """CRUD operations for the ``contacts`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        apple_contact_id: str,
        full_name: str,
        **kwargs: Any,
    ) -> Contact:
        """Insert or update a contact by ``apple_contact_id``."""
        result = await self._session.execute(select(Contact).where(Contact.apple_contact_id == apple_contact_id))
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.full_name = full_name
            for key, value in kwargs.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            existing.synced_at = datetime.now(UTC)
            await self._session.flush()
            return existing

        contact = Contact(
            apple_contact_id=apple_contact_id,
            full_name=full_name,
            synced_at=datetime.now(UTC),
            **kwargs,
        )
        self._session.add(contact)
        await self._session.flush()
        return contact

    async def get_by_email(self, email: str) -> list[Contact]:
        """Find contacts whose ``email_addresses`` JSONB array contains the email.

        Uses the PostgreSQL ``@>`` containment operator.
        """

        stmt = select(Contact).where(Contact.email_addresses.op("@>")(f'["{email}"]'))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all(self, limit: int = 500) -> list[Contact]:
        """Fetch all contacts, ordered by name."""
        result = await self._session.execute(select(Contact).order_by(Contact.full_name).limit(limit))
        return list(result.scalars().all())

    async def search_by_name(self, query: str, limit: int = 20) -> list[Contact]:
        """Search contacts by name (case-insensitive ILIKE)."""
        result = await self._session.execute(
            select(Contact).where(Contact.full_name.ilike(f"%{query}%")).order_by(Contact.full_name).limit(limit)
        )
        return list(result.scalars().all())

    async def bulk_upsert(self, contacts: list[dict[str, Any]]) -> int:
        """Upsert multiple contacts. Returns count processed."""
        count = 0
        for c in contacts:
            c = dict(c)  # shallow copy — do not mutate caller's data
            apple_id = c.pop("apple_contact_id")
            full_name = c.pop("full_name")
            await self.upsert(apple_id, full_name, **c)
            count += 1
        return count
