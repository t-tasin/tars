"""Repository for the messages table."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message

log = structlog.get_logger()


class MessageRepository:
    """CRUD operations for the ``messages`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        content_type: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Create a new message."""
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            content_type=content_type,
            metadata_=metadata or {},
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def get_by_conversation(
        self,
        conversation_id: uuid.UUID,
        limit: int | None = None,
    ) -> list[Message]:
        """Fetch messages for a conversation, newest last."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        if limit is not None:
            # Return the *most recent* N messages while preserving asc order.
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            result = await self._session.execute(stmt)
            return list(reversed(result.scalars().all()))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_by_source(
        self,
        source: str,
        limit: int = 20,
    ) -> list[Message]:
        """Fetch recent messages from conversations with a given source.

        Joins through conversations to filter by source.
        """
        from db.models import Conversation

        stmt = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.source == source)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
