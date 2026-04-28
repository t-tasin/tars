"""One-shot: ensure tasin_wiki collection + ingest wiki/ dir.

Usage::

    cd backend && python -m scripts.wiki_bootstrap

Reads env vars (or uses built-in defaults from clients):
    QDRANT_HOST      — default 100.119.114.125
    QDRANT_PORT      — default 6333
    EMBED_BASE_URL   — default http://100.119.114.125:8003
    WIKI_ROOT        — default <repo-root>/wiki   (two levels up from backend/)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog

# ---------------------------------------------------------------------------
# Bootstrap path: backend/src must be on sys.path so the imports below work
# when the script is run with ``python -m scripts.wiki_bootstrap`` from the
# backend/ directory.
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SRC_DIR = _BACKEND_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Shared package lives one level above backend/
_SHARED_DIR = _BACKEND_DIR.parent / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from db.repositories.wiki_index import WikiIndexRepository  # noqa: E402
from db.session import get_db_session  # noqa: E402
from integrations.qdrant_client import QdrantClient  # noqa: E402
from integrations.wiki_ingest import bootstrap_collection  # noqa: E402
from models.embedding_client import EmbeddingClient  # noqa: E402

log = structlog.get_logger()

_DEFAULT_WIKI_ROOT = _BACKEND_DIR.parent / "wiki"


async def main() -> None:
    qdrant_host = os.environ.get("QDRANT_HOST", "100.119.114.125")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    embed_base_url = os.environ.get("EMBED_BASE_URL", "http://100.119.114.125:8003")
    wiki_root = Path(os.environ.get("WIKI_ROOT", str(_DEFAULT_WIKI_ROOT)))

    log.info(
        "wiki_bootstrap.start",
        qdrant_host=qdrant_host,
        qdrant_port=qdrant_port,
        embed_base_url=embed_base_url,
        wiki_root=str(wiki_root),
    )

    qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
    embedder = EmbeddingClient(base_url=embed_base_url)

    async with get_db_session() as session:
        wiki_index_repo = WikiIndexRepository(session)
        result = await bootstrap_collection(
            qdrant=qdrant,
            embedder=embedder,
            wiki_index_repo=wiki_index_repo,
            wiki_root=wiki_root,
        )

    await qdrant.close()
    await embedder.aclose()

    print(f"wiki_bootstrap done — files={result['files']} chunks={result['chunks']} skipped={result['skipped']}")
    log.info("wiki_bootstrap.done", **result)


if __name__ == "__main__":
    asyncio.run(main())
