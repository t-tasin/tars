"""Wiki ingestion helpers for T.A.R.S. (P3-08).

Provides:
- ``chunk_markdown`` — pure function to split markdown into overlapping windows.
- ``ingest_file``    — embed one ``.md`` file and upsert to Qdrant + wiki_index.
- ``bootstrap_collection`` — idempotent setup: ensure collection + walk wiki/.

P3-07 watcher will call ``ingest_file`` directly once it lands.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import structlog

from db.repositories.wiki_index import WikiIndexRepository
from integrations.qdrant_client import QdrantClient
from models.embedding_client import EmbeddingClient

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_NAME = "tasin_wiki"
VECTOR_SIZE = 1024
DISTANCE = "COSINE"
DEFAULT_CHUNK_CHARS = 1200  # ~300 tokens
DEFAULT_CHUNK_OVERLAP = 200  # ~50 tokens overlap

_EMBED_MODEL = "qwen3-embed-0.6b"
_EMBED_DIM = 1024


# ---------------------------------------------------------------------------
# chunk_markdown — pure function
# ---------------------------------------------------------------------------


def chunk_markdown(
    text: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping windows aligned to paragraph boundaries.

    Algorithm:
    1. Split on blank lines (double-newline) to get paragraphs.
    2. Pack paragraphs greedily into a window up to ``chunk_chars``.
    3. When a paragraph would overflow, flush the current window as a chunk
       and start a new one, seeding it with the trailing ``overlap`` chars
       of the previous chunk (taken from complete paragraphs).
    4. A single paragraph that exceeds ``chunk_chars`` is emitted as-is
       (we never split mid-paragraph).

    Returns ``[]`` for empty input.
    """
    if not text or not text.strip():
        return []

    # Split into paragraphs (one or more blank lines as delimiter).
    raw_paras = [p.strip() for p in text.split("\n\n")]
    paragraphs = [p for p in raw_paras if p]

    if not paragraphs:
        return []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len: int = 0

    def _flush() -> None:
        """Emit the current window as a chunk."""
        if not current_parts:
            return
        chunk_text = "\n\n".join(current_parts)
        chunks.append(chunk_text)

    def _seed_overlap(prev_chunk: str) -> tuple[list[str], int]:
        """Return (parts, len) seeded from the trailing overlap of prev_chunk."""
        # Walk prev chunk's paragraphs from the end until we fill overlap chars.
        prev_paras = [p.strip() for p in prev_chunk.split("\n\n") if p.strip()]
        seed_parts: list[str] = []
        seed_len = 0
        for para in reversed(prev_paras):
            candidate_len = len(para) + (2 if seed_parts else 0)  # "\n\n" separator
            if seed_len + candidate_len > overlap and seed_parts:
                # Already have enough overlap; stop.
                break
            seed_parts.insert(0, para)
            seed_len += candidate_len
        return seed_parts, seed_len

    for para in paragraphs:
        para_len = len(para)
        # Cost of adding this paragraph to the current window.
        sep_cost = 2 if current_parts else 0  # "\n\n" between paragraphs
        cost = para_len + sep_cost

        if current_len + cost <= chunk_chars:
            # Fits: append.
            current_parts.append(para)
            current_len += cost
        else:
            # Doesn't fit: flush current, then seed overlap for next window.
            if current_parts:
                _flush()
                prev_chunk = "\n\n".join(current_parts)
                current_parts, current_len = _seed_overlap(prev_chunk)

            # Now try to add the paragraph to the seeded window.
            sep_cost2 = 2 if current_parts else 0
            if current_len + para_len + sep_cost2 <= chunk_chars:
                current_parts.append(para)
                current_len += para_len + sep_cost2
            else:
                # Paragraph alone exceeds chunk_chars — emit as standalone chunk.
                # But first flush whatever seed we have if any.
                if current_parts:
                    _flush()
                    current_parts, current_len = [], 0
                # Emit the oversized paragraph directly.
                chunks.append(para)

    # Flush remaining.
    _flush()

    return chunks


# ---------------------------------------------------------------------------
# ingest_file
# ---------------------------------------------------------------------------


async def ingest_file(
    path: Path,
    *,
    qdrant: QdrantClient,
    embedder: EmbeddingClient,
    wiki_index_repo: WikiIndexRepository,
    wiki_root: Path | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """Ingest one wiki Markdown file.  Returns number of chunks ingested.

    Steps:
    1. Read file (UTF-8).
    2. Compute sha256 content hash for dedup.
    3. Delete existing Qdrant points + PG rows for this path (idempotent).
    4. Chunk via ``chunk_markdown``.
    5. Batch-embed chunks.
    6. Upsert each (chunk, vector) to Qdrant + PG ``wiki_index``.
    """
    rel_path = str(path.relative_to(wiki_root)) if wiki_root and wiki_root in path.parents else path.name

    log.info("wiki_ingest.file_start", path=rel_path)

    text = path.read_text(encoding="utf-8")

    file_hash = hashlib.sha256(text.encode()).hexdigest()

    # --- Step 3: delete prior chunks (idempotent re-ingest) ---
    existing_rows = await wiki_index_repo.get_by_path(rel_path)
    if existing_rows:
        prior_point_ids = [row.qdrant_point_id for row in existing_rows]
        await qdrant.delete(COLLECTION_NAME, prior_point_ids)
        await wiki_index_repo.delete_by_path(rel_path)
        log.info("wiki_ingest.deleted_prior", path=rel_path, count=len(prior_point_ids))

    # --- Step 4: chunk ---
    chunks = chunk_markdown(text, chunk_chars=chunk_chars, overlap=chunk_overlap)
    if not chunks:
        log.info("wiki_ingest.empty_file", path=rel_path)
        return 0

    # --- Step 5: embed batch ---
    vectors = await embedder.embed_batch(chunks)

    # --- Step 6: upsert ---
    for idx, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
        point_id = uuid.uuid4()
        await qdrant.upsert(
            COLLECTION_NAME,
            point_id,
            vector,
            payload={
                "path": rel_path,
                "chunk_index": idx,
                "text": chunk_text,
                "content_hash": file_hash,
            },
        )
        await wiki_index_repo.upsert_chunk(
            path=rel_path,
            chunk_index=idx,
            qdrant_point_id=point_id,
            content_hash=file_hash,
            embedding_model=_EMBED_MODEL,
            embedding_dim=_EMBED_DIM,
        )
        log.debug("wiki_ingest.chunk_upserted", path=rel_path, chunk_index=idx, point_id=str(point_id))

    log.info("wiki_ingest.file_done", path=rel_path, chunks=len(chunks))
    return len(chunks)


# ---------------------------------------------------------------------------
# bootstrap_collection
# ---------------------------------------------------------------------------


async def bootstrap_collection(
    *,
    qdrant: QdrantClient,
    embedder: EmbeddingClient,
    wiki_index_repo: WikiIndexRepository,
    wiki_root: Path,
) -> dict[str, int]:
    """Ensure the ``tasin_wiki`` collection exists, then ingest all ``*.md`` files.

    Returns ``{"files": N, "chunks": M, "skipped": K}``.

    If ``wiki_root`` is absent, logs a warning and returns all-zeros — no crash
    (HC-09 fail-soft).
    """
    if not wiki_root.exists():
        log.warning("wiki_ingest.bootstrap_wiki_root_missing", wiki_root=str(wiki_root))
        return {"files": 0, "chunks": 0, "skipped": 0}

    # Ensure collection (idempotent).
    await qdrant.ensure_collection(COLLECTION_NAME, vector_size=VECTOR_SIZE, distance=DISTANCE)
    log.info("wiki_ingest.collection_ensured", collection=COLLECTION_NAME)

    md_files = sorted(wiki_root.rglob("*.md"))
    log.info("wiki_ingest.bootstrap_start", wiki_root=str(wiki_root), file_count=len(md_files))

    total_files = 0
    total_chunks = 0
    total_skipped = 0

    for md_path in md_files:
        try:
            n = await ingest_file(
                md_path,
                qdrant=qdrant,
                embedder=embedder,
                wiki_index_repo=wiki_index_repo,
                wiki_root=wiki_root,
            )
            total_files += 1
            total_chunks += n
        except Exception as exc:  # noqa: BLE001 — HC-09: log + skip per-file errors
            log.error(
                "wiki_ingest.file_error",
                path=str(md_path),
                error=str(exc),
                exc_info=True,
            )
            total_skipped += 1

    result = {"files": total_files, "chunks": total_chunks, "skipped": total_skipped}
    log.info("wiki_ingest.bootstrap_done", **result)
    return result
