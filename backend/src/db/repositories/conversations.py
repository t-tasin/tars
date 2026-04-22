"""Repository for conversations and their messages."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Message

log = structlog.get_logger()


class ConversationRepository:
    """CRUD operations for the ``conversations`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, source: str, metadata: dict[str, Any] | None = None) -> Conversation:
        """Create a new conversation."""
        conv = Conversation(source=source, metadata_=metadata or {})
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Fetch a conversation by primary key."""
        result = await self._session.execute(select(Conversation).where(Conversation.id == conversation_id))
        return result.scalar_one_or_none()

    async def get_recent(self, limit: int = 20) -> list[Conversation]:
        """Fetch the most recent conversations."""
        result = await self._session.execute(select(Conversation).order_by(Conversation.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def add_message(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        content_type: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Append a message to an existing conversation."""
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

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int | None = None,
    ) -> list[Message]:
        """Fetch messages for a conversation, ordered by creation time."""
        stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
