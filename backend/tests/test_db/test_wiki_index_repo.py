"""Tests for ``db.repositories.wiki_index.WikiIndexRepository``.

Covers upsert (insert + change-detect by content_hash), get_by_path,
get_by_hash dedup, and delete_by_path. PG-only checks gated behind
``requires_pg``.
"""

from __future__ import annotations

import os
import uuid

import pytest

from db.models import WikiIndex
from db.repositories.wiki_index import WikiIndexRepository

_HAS_PG = bool(os.environ.get("TEST_DATABASE_URL", ""))
requires_pg = pytest.mark.skipif(not _HAS_PG, reason="Requires real PostgreSQL")


def _stored_wiki_index(test_db) -> list[WikiIndex]:
    """Pull only WikiIndex rows out of the in-memory shim store."""
    store = getattr(test_db, "_store", None)
    if store is None:
        return []
    return [obj for obj in store if isinstance(obj, WikiIndex)]


@pytest.mark.asyncio
async def test_upsert_inserts_new_chunk(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    point_id = uuid.uuid4()
    record, changed = await repo.upsert_chunk(
        path="identity/preferences.md",
        chunk_index=0,
        qdrant_point_id=point_id,
        content_hash="hash-a",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    assert changed is True
    assert record.path == "identity/preferences.md"
    assert record.chunk_index == 0
    assert record.qdrant_point_id == point_id
    assert record.content_hash == "hash-a"
    assert record.embedding_model == "qwen3-embed-0.6b"
    assert record.embedding_dim == 1024


@pytest.mark.asyncio
async def test_upsert_same_hash_is_noop(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    point_id = uuid.uuid4()
    await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=point_id,
        content_hash="same-hash",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    # Second call with identical content_hash + model should report no change.
    _, changed = await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),  # would-be new point id
        content_hash="same-hash",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    assert changed is False
    # The stored row should still hold the original point_id (we short-circuited).
    rows = _stored_wiki_index(test_db)
    assert len(rows) == 1
    assert rows[0].qdrant_point_id == point_id


@pytest.mark.asyncio
async def test_upsert_hash_change_updates_row(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="hash-1",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    new_point = uuid.uuid4()
    record, changed = await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=new_point,
        content_hash="hash-2",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    assert changed is True
    assert record.content_hash == "hash-2"
    assert record.qdrant_point_id == new_point
    # Still exactly one row for (path, chunk_index).
    assert len(_stored_wiki_index(test_db)) == 1


@pytest.mark.asyncio
async def test_upsert_model_change_triggers_reindex(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="same-hash",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    _, changed = await repo.upsert_chunk(
        path="a.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="same-hash",
        embedding_model="qwen3-embed-4b",  # model swap
        embedding_dim=2560,
    )
    assert changed is True


@pytest.mark.asyncio
async def test_get_by_path_returns_chunks_in_order(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    for i in (2, 0, 1):
        await repo.upsert_chunk(
            path="multi.md",
            chunk_index=i,
            qdrant_point_id=uuid.uuid4(),
            content_hash=f"h{i}",
            embedding_model="qwen3-embed-0.6b",
            embedding_dim=1024,
        )
    chunks = await repo.get_by_path("multi.md")
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_by_hash_dedup(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    shared = "shared-hash"
    await repo.upsert_chunk(
        path="x.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash=shared,
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    await repo.upsert_chunk(
        path="y.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash=shared,
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    matches = await repo.get_by_hash(shared)
    assert {m.path for m in matches} == {"x.md", "y.md"}


@pytest.mark.asyncio
async def test_delete_by_path_removes_all_chunks(test_db) -> None:
    repo = WikiIndexRepository(test_db)
    for i in range(3):
        await repo.upsert_chunk(
            path="gone.md",
            chunk_index=i,
            qdrant_point_id=uuid.uuid4(),
            content_hash=f"h{i}",
            embedding_model="qwen3-embed-0.6b",
            embedding_dim=1024,
        )
    # Sibling file should survive.
    await repo.upsert_chunk(
        path="kept.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="keep",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )

    deleted = await repo.delete_by_path("gone.md")
    assert deleted == 3
    assert await repo.get_by_path("gone.md") == []
    assert len(await repo.get_by_path("kept.md")) == 1


@requires_pg
@pytest.mark.asyncio
async def test_path_chunk_uniqueness_enforced(test_db) -> None:
    """Real PG must enforce UNIQUE (path, chunk_index)."""
    from sqlalchemy.exc import IntegrityError

    a = WikiIndex(
        path="dup.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="a",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    b = WikiIndex(
        path="dup.md",
        chunk_index=0,
        qdrant_point_id=uuid.uuid4(),
        content_hash="b",
        embedding_model="qwen3-embed-0.6b",
        embedding_dim=1024,
    )
    test_db.add(a)
    await test_db.flush()
    test_db.add(b)
    with pytest.raises(IntegrityError):
        await test_db.flush()
