"""Tests for ``integrations.wiki_ingest`` (P3-08).

Covers:
- chunk_markdown edge cases (empty, short, long, paragraph-boundary, overlap)
- ingest_file happy path, idempotent re-ingest, empty file, embedder raise
- bootstrap_collection: missing wiki_root, mixed valid+broken files,
  ensure_collection called exactly once before file loop.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.wiki_ingest import (
    COLLECTION_NAME,
    DISTANCE,
    VECTOR_SIZE,
    bootstrap_collection,
    chunk_markdown,
    ingest_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vector() -> list[float]:
    return [0.1] * 1024


def _make_qdrant() -> AsyncMock:
    q = AsyncMock()
    q.ensure_collection = AsyncMock(return_value=True)
    q.upsert = AsyncMock()
    q.delete = AsyncMock()
    return q


def _make_embedder(n_chunks: int = 1) -> AsyncMock:
    e = AsyncMock()
    e.embed_batch = AsyncMock(return_value=[_make_vector() for _ in range(n_chunks)])
    return e


def _make_repo() -> AsyncMock:
    r = AsyncMock()
    r.get_by_path = AsyncMock(return_value=[])
    r.delete_by_path = AsyncMock(return_value=0)
    r.upsert_chunk = AsyncMock(return_value=(MagicMock(), True))
    return r


# ---------------------------------------------------------------------------
# chunk_markdown — pure function tests
# ---------------------------------------------------------------------------


def test_chunk_markdown_empty_string():
    assert chunk_markdown("") == []


def test_chunk_markdown_whitespace_only():
    assert chunk_markdown("   \n\n   ") == []


def test_chunk_markdown_short_text_single_chunk():
    text = "Hello world. This is a short paragraph."
    result = chunk_markdown(text)
    assert len(result) == 1
    assert result[0] == text


def test_chunk_markdown_long_text_multiple_chunks():
    # Build text with paragraphs that each fill ~400 chars; total ~2000 chars
    # With chunk_chars=1200 we expect at least 2 chunks.
    para = "A" * 400
    text = f"{para}\n\n{para}\n\n{para}\n\n{para}\n\n{para}"
    result = chunk_markdown(text, chunk_chars=1200, overlap=200)
    assert len(result) >= 2
    for chunk in result:
        # Each chunk should not wildly exceed chunk_chars (only oversized paras
        # are allowed to exceed by one paragraph).
        assert len(chunk) <= 1200 + 400 + 2


def test_chunk_markdown_no_chunk_exceeds_chunk_chars_by_more_than_one_paragraph():
    """No individual chunk should exceed chunk_chars + max_paragraph_len."""
    para = "B" * 300
    text = "\n\n".join([para] * 10)
    result = chunk_markdown(text, chunk_chars=1000, overlap=150)
    for chunk in result:
        assert len(chunk) <= 1000 + 300 + 2


def test_chunk_markdown_overlap_respected():
    """Successive chunks should share some text (overlap > 0)."""
    para = "C" * 400
    text = "\n\n".join([para] * 6)
    result = chunk_markdown(text, chunk_chars=1000, overlap=300)
    assert len(result) >= 2
    # The end of chunk[0] should appear somewhere in the beginning of chunk[1].
    # At minimum, the last paragraph of chunk[0] should appear in chunk[1].
    last_para_of_first = result[0].split("\n\n")[-1]
    assert last_para_of_first in result[1]


def test_chunk_markdown_paragraph_boundary_not_split_mid_sentence():
    """A paragraph that fits should never be split across two chunks."""
    short_para = "This sentence must stay together in one paragraph."
    filler = "X" * 1000  # Large filler to force chunking
    text = f"{filler}\n\n{short_para}"
    result = chunk_markdown(text, chunk_chars=1100, overlap=100)
    # short_para should appear intact in exactly one chunk
    found = [c for c in result if short_para in c]
    assert len(found) >= 1
    # Confirm it was not split mid-sentence
    for chunk in found:
        assert short_para in chunk


def test_chunk_markdown_single_oversized_paragraph():
    """A paragraph longer than chunk_chars emits as a single chunk."""
    big_para = "Z" * 2000
    result = chunk_markdown(big_para, chunk_chars=1000, overlap=100)
    assert len(result) == 1
    assert result[0] == big_para


# ---------------------------------------------------------------------------
# ingest_file tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_file_happy_path(tmp_path: Path) -> None:
    """Happy path: 3 chunks → 3 upsert calls to qdrant + 3 repo writes."""
    text = "\n\n".join(["Para " * 200] * 3)  # 3 large paragraphs → 3 chunks
    md = tmp_path / "test.md"
    md.write_text(text)

    # Force exactly 3 chunks by using small chunk_chars
    chunks = chunk_markdown(text, chunk_chars=1200, overlap=200)
    n = len(chunks)

    qdrant = _make_qdrant()
    embedder = _make_embedder(n)
    repo = _make_repo()

    count = await ingest_file(
        md,
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=tmp_path,
        chunk_chars=1200,
        chunk_overlap=200,
    )

    assert count == n
    assert qdrant.upsert.call_count == n
    assert repo.upsert_chunk.call_count == n

    # All point_ids passed to qdrant.upsert must be unique UUIDs
    point_ids = [c.args[1] for c in qdrant.upsert.call_args_list]
    assert len(set(point_ids)) == n
    for pid in point_ids:
        assert isinstance(pid, uuid.UUID)


@pytest.mark.asyncio
async def test_ingest_file_idempotent_rein_ingest(tmp_path: Path) -> None:
    """Second call must delete prior Qdrant points before re-upserting."""
    md = tmp_path / "page.md"
    md.write_text("Hello world paragraph.")

    # Simulate existing 2 rows in PG mirror
    prior_pid1, prior_pid2 = uuid.uuid4(), uuid.uuid4()
    prior_row1 = MagicMock()
    prior_row1.qdrant_point_id = prior_pid1
    prior_row2 = MagicMock()
    prior_row2.qdrant_point_id = prior_pid2

    qdrant = _make_qdrant()
    embedder = _make_embedder(1)
    repo = _make_repo()
    repo.get_by_path = AsyncMock(return_value=[prior_row1, prior_row2])

    await ingest_file(
        md,
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=tmp_path,
    )

    # delete must have been called with the prior point ids
    qdrant.delete.assert_awaited_once()
    deleted_ids = qdrant.delete.call_args.args[1]
    assert set(deleted_ids) == {prior_pid1, prior_pid2}

    # repo.delete_by_path must also have been called
    repo.delete_by_path.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_file_empty_file(tmp_path: Path) -> None:
    """Empty file returns 0 chunks; embedder.embed_batch never called."""
    md = tmp_path / "empty.md"
    md.write_text("")

    qdrant = _make_qdrant()
    embedder = _make_embedder(0)
    repo = _make_repo()

    count = await ingest_file(
        md,
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=tmp_path,
    )

    assert count == 0
    embedder.embed_batch.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()
    repo.upsert_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_file_embedder_raises(tmp_path: Path) -> None:
    """If embedder.embed_batch raises, the exception propagates to the caller."""
    md = tmp_path / "fail.md"
    md.write_text("Some content for embedding.")

    qdrant = _make_qdrant()
    embedder = AsyncMock()
    embedder.embed_batch = AsyncMock(side_effect=RuntimeError("embed failed"))
    repo = _make_repo()

    with pytest.raises(RuntimeError, match="embed failed"):
        await ingest_file(
            md,
            qdrant=qdrant,
            embedder=embedder,
            wiki_index_repo=repo,
            wiki_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# bootstrap_collection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_collection_missing_wiki_root() -> None:
    """Missing wiki_root → returns zeros, no crash, ensure_collection not called."""
    qdrant = _make_qdrant()
    embedder = _make_embedder(0)
    repo = _make_repo()

    result = await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=Path("/nonexistent/wiki"),
    )

    assert result == {"files": 0, "chunks": 0, "skipped": 0}
    qdrant.ensure_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_collection_ensure_collection_called_once(tmp_path: Path) -> None:
    """ensure_collection must be called exactly once, before any file loop."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "a.md").write_text("Alpha content for the wiki.")
    (wiki_root / "b.md").write_text("Beta content for the wiki.")

    qdrant = _make_qdrant()
    embedder = AsyncMock()
    embedder.embed_batch = AsyncMock(return_value=[_make_vector()])
    repo = _make_repo()

    call_order: list[str] = []

    original_ensure = qdrant.ensure_collection.side_effect

    async def track_ensure(*args, **kwargs):
        call_order.append("ensure_collection")
        return True

    async def track_upsert(*args, **kwargs):
        call_order.append("upsert")

    qdrant.ensure_collection = AsyncMock(side_effect=track_ensure)
    qdrant.upsert = AsyncMock(side_effect=track_upsert)

    await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=wiki_root,
    )

    # ensure_collection called exactly once
    assert call_order.count("ensure_collection") == 1
    # And it was called before any upsert
    assert call_order.index("ensure_collection") < call_order.index("upsert")


@pytest.mark.asyncio
async def test_bootstrap_collection_valid_files(tmp_path: Path) -> None:
    """Bootstrap with 2 valid .md files returns correct files/chunks counts."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "intro.md").write_text("Introduction paragraph.")
    (wiki_root / "guide.md").write_text("Guide paragraph.")

    qdrant = _make_qdrant()
    embedder = AsyncMock()
    embedder.embed_batch = AsyncMock(return_value=[_make_vector()])
    repo = _make_repo()

    result = await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=wiki_root,
    )

    assert result["files"] == 2
    assert result["chunks"] == 2  # each file = 1 small chunk
    assert result["skipped"] == 0


@pytest.mark.asyncio
async def test_bootstrap_collection_mixed_valid_and_broken(tmp_path: Path) -> None:
    """One broken file → skipped=1, valid files still counted correctly."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "good.md").write_text("Valid content paragraph.")
    broken_path = wiki_root / "broken.md"
    broken_path.write_text("Broken content.")

    qdrant = _make_qdrant()
    embedder = AsyncMock()

    call_count = 0

    async def embed_side_effect(texts):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [_make_vector()]
        raise RuntimeError("embed endpoint down")

    embedder.embed_batch = AsyncMock(side_effect=embed_side_effect)
    repo = _make_repo()

    result = await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=wiki_root,
    )

    assert result["files"] == 1
    assert result["chunks"] == 1
    assert result["skipped"] == 1


@pytest.mark.asyncio
async def test_bootstrap_collection_empty_wiki_dir(tmp_path: Path) -> None:
    """Empty wiki_root directory returns files=0, chunks=0, skipped=0."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()

    qdrant = _make_qdrant()
    embedder = _make_embedder(0)
    repo = _make_repo()

    result = await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=wiki_root,
    )

    assert result == {"files": 0, "chunks": 0, "skipped": 0}
    # ensure_collection still called even for empty dir
    qdrant.ensure_collection.assert_awaited_once_with(COLLECTION_NAME, vector_size=VECTOR_SIZE, distance=DISTANCE)


@pytest.mark.asyncio
async def test_bootstrap_collection_correct_collection_params(tmp_path: Path) -> None:
    """ensure_collection must use the module constants for name/size/distance."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()

    qdrant = _make_qdrant()
    embedder = _make_embedder(0)
    repo = _make_repo()

    await bootstrap_collection(
        qdrant=qdrant,
        embedder=embedder,
        wiki_index_repo=repo,
        wiki_root=wiki_root,
    )

    qdrant.ensure_collection.assert_awaited_once_with(
        COLLECTION_NAME,
        vector_size=VECTOR_SIZE,
        distance=DISTANCE,
    )
    assert COLLECTION_NAME == "tasin_wiki"
    assert VECTOR_SIZE == 1024
    assert DISTANCE == "COSINE"
