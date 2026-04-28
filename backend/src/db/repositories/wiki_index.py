"""Repository for the ``wiki_index`` table (Phase 3 — Qdrant mirror).

Mirrors every chunk we have embedded into Qdrant ``tasin_wiki``. The
Qdrant point id is stored here so we can rebuild the collection
deterministically (e.g. after an embedding-model swap) without having
to re-chunk the source markdown.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WikiIndex

log = structlog.get_logger()


class WikiIndexRepository:
    """CRUD operations for ``wiki_index``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_chunk(
        self,
        *,
        path: str,
        chunk_index: int,
        qdrant_point_id: uuid.UUID,
        content_hash: str,
        embedding_model: str,
        embedding_dim: int,
    ) -> tuple[WikiIndex, bool]:
        """Insert or update the row for ``(path, chunk_index)``.

        Returns ``(record, changed)`` where ``changed`` is ``True`` when the
        row was newly inserted or its ``content_hash`` differed from the
        prior value (i.e. the chunk text actually changed and must be
        re-embedded). When ``changed`` is ``False`` the existing row is
        returned untouched — useful for the indexer to short-circuit a
        no-op embed.
        """
        existing = await self._get_by_path_and_chunk(path, chunk_index)
        if existing is None:
            record = WikiIndex(
                path=path,
                chunk_index=chunk_index,
                qdrant_point_id=qdrant_point_id,
                content_hash=content_hash,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
            )
            self._session.add(record)
            await self._session.flush()
            log.info("wiki_index.inserted", path=path, chunk_index=chunk_index)
            return record, True

        if existing.content_hash == content_hash and existing.embedding_model == embedding_model:
            return existing, False

        existing.qdrant_point_id = qdrant_point_id
        existing.content_hash = content_hash
        existing.embedding_model = embedding_model
        existing.embedding_dim = embedding_dim
        await self._session.flush()
        log.info("wiki_index.updated", path=path, chunk_index=chunk_index)
        return existing, True

    async def get_by_path(self, path: str) -> list[WikiIndex]:
        """Return every chunk currently indexed for ``path`` ordered by chunk_index."""
        result = await self._session.execute(
            select(WikiIndex).where(WikiIndex.path == path).order_by(WikiIndex.chunk_index.asc())
        )
        rows = [r for r in result.scalars().all() if r.path == path]
        rows.sort(key=lambda r: r.chunk_index)
        return rows

    async def get_by_hash(self, content_hash: str) -> list[WikiIndex]:
        """Return every row matching ``content_hash`` (dedup helper)."""
        result = await self._session.execute(select(WikiIndex).where(WikiIndex.content_hash == content_hash))
        return [r for r in result.scalars().all() if r.content_hash == content_hash]

    async def delete_by_path(self, path: str) -> int:
        """Delete every chunk for ``path``. Returns count deleted.

        Used when a wiki file is removed (proposal ``operation='delete'``
        approved) so the Qdrant rebuild knows not to re-emit those points.
        """
        # In-memory shim doesn't honor DELETE statements, so fall back to
        # selecting + removing from the store when running tests.
        existing = await self.get_by_path(path)
        store: list[Any] | None = getattr(self._session, "_store", None)
        if store is not None:
            kept = [obj for obj in store if not (isinstance(obj, WikiIndex) and obj.path == path)]
            store.clear()
            store.extend(kept)
            log.info("wiki_index.deleted", path=path, count=len(existing))
            return len(existing)

        await self._session.execute(delete(WikiIndex).where(WikiIndex.path == path))
        await self._session.flush()
        log.info("wiki_index.deleted", path=path, count=len(existing))
        return len(existing)

    async def _get_by_path_and_chunk(self, path: str, chunk_index: int) -> WikiIndex | None:
        # Use ``.all()`` rather than ``.first()`` so the in-memory test shim
        # (which doesn't implement ``first()``) can serve the same call site.
        # The unique constraint on (path, chunk_index) keeps the result list
        # at zero or one row in real Postgres.
        result = await self._session.execute(
            select(WikiIndex).where(WikiIndex.path == path, WikiIndex.chunk_index == chunk_index)
        )
        rows = list(result.scalars().all())
        for row in rows:
            if row.path == path and row.chunk_index == chunk_index:
                return row
        return None
