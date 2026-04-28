"""WikiWatcher — filesystem observer that re-embeds wiki/ on change.

Phase 3 (P3-07). Watches a wiki root directory for *.md changes and:
- create/modify → call wiki_ingest.ingest_file (idempotent re-embed)
- delete       → remove all chunks for that path from Qdrant + wiki_index

Debounces (default 500 ms) to coalesce editor save bursts (vim/vscode write
multiple events per save).

This is NOT a BaseSensor (no cadence). Run as a long-lived asyncio task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from db.repositories.wiki_index import WikiIndexRepository
from integrations import wiki_ingest
from integrations.qdrant_client import QdrantClient
from integrations.wiki_ingest import COLLECTION_NAME

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()

DEFAULT_DEBOUNCE_S: float = 0.5


# ---------------------------------------------------------------------------
# Internal watchdog event handler (runs in the Observer thread)
# ---------------------------------------------------------------------------


class _MDEventHandler(FileSystemEventHandler):
    """Watchdog event handler that marshals .md events to the asyncio loop."""

    def __init__(self, watcher: WikiWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        self._watcher._on_modified(event)

    def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        self._watcher._on_created(event)

    def on_deleted(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        self._watcher._on_deleted(event)


# ---------------------------------------------------------------------------
# WikiWatcher
# ---------------------------------------------------------------------------


class WikiWatcher:
    """Filesystem watcher that re-embeds wiki/*.md files on change.

    Not a BaseSensor — event-driven, not cadence-based.

    Usage::

        watcher = WikiWatcher(wiki_root, qdrant=qdrant, embedder=embedder,
                              session_factory=session_factory)
        watcher.start()
        # … run until shutdown …
        await watcher.stop()
    """

    def __init__(
        self,
        wiki_root: Path,
        *,
        qdrant: QdrantClient,
        embedder: object,  # EmbeddingClient — avoid circular import at type level
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._wiki_root = wiki_root.resolve()
        self._qdrant = qdrant
        self._embedder = embedder
        self._session_factory = session_factory
        self._debounce_s = debounce_s
        self._loop: asyncio.AbstractEventLoop | None = loop

        # Debounce state: maps resolved Path → asyncio.Task
        self._pending: dict[Path, asyncio.Task[None]] = {}

        self._observer: Observer | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mount the watchdog Observer.  Idempotent — safe to call twice."""
        if self._started:
            log.warning("wiki_watcher.already_started")
            return

        # Grab the running event loop if not injected (must be called from an
        # async context, e.g. inside an asyncio.Task).
        if self._loop is None:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()

        handler = _MDEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._wiki_root), recursive=True)
        self._observer.start()
        self._started = True
        log.info("wiki_watcher.started", wiki_root=str(self._wiki_root))

    async def stop(self) -> None:
        """Stop observer and drain any pending debounce tasks."""
        if not self._started:
            return

        # Cancel all pending debounce tasks.
        pending_tasks = list(self._pending.values())
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._pending.clear()

        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

        self._started = False
        log.info("wiki_watcher.stopped")

    # ------------------------------------------------------------------
    # Watchdog thread hooks — marshal to event loop
    # ------------------------------------------------------------------

    def _on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".md":
            return
        self._schedule_upsert(path.resolve())

    def _on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".md":
            return
        self._schedule_upsert(path.resolve())

    def _on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".md":
            return
        self._schedule_delete(path.resolve())

    # ------------------------------------------------------------------
    # Debounce helpers (called from watchdog thread)
    # ------------------------------------------------------------------

    def _schedule_upsert(self, path: Path) -> None:
        """Schedule a debounced upsert on the asyncio loop."""
        loop = self._loop
        if loop is None or not loop.is_running():
            log.warning("wiki_watcher.no_loop_upsert", path=str(path))
            return
        loop.call_soon_threadsafe(self._enqueue_upsert, path)

    def _schedule_delete(self, path: Path) -> None:
        """Schedule a delete on the asyncio loop (no debounce needed for delete)."""
        loop = self._loop
        if loop is None or not loop.is_running():
            log.warning("wiki_watcher.no_loop_delete", path=str(path))
            return
        loop.call_soon_threadsafe(self._enqueue_delete, path)

    # ------------------------------------------------------------------
    # Enqueue helpers (called on the asyncio loop via call_soon_threadsafe)
    # ------------------------------------------------------------------

    def _enqueue_upsert(self, path: Path) -> None:
        """Cancel any existing debounce task for *path*, schedule a new one."""
        existing = self._pending.pop(path, None)
        if existing is not None and not existing.done():
            existing.cancel()
        loop = self._loop
        assert loop is not None  # guaranteed by _schedule_upsert
        task = loop.create_task(self._debounced_upsert(path))
        self._pending[path] = task

    def _enqueue_delete(self, path: Path) -> None:
        """Cancel any pending upsert for *path*, then schedule delete immediately."""
        existing = self._pending.pop(path, None)
        if existing is not None and not existing.done():
            existing.cancel()
        loop = self._loop
        assert loop is not None
        loop.create_task(self._handle_delete(path))

    # ------------------------------------------------------------------
    # Coroutines (run on the asyncio event loop)
    # ------------------------------------------------------------------

    async def _debounced_upsert(self, path: Path) -> None:
        """Wait debounce_s, then run upsert (cancelled if a newer event arrives)."""
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            return
        finally:
            self._pending.pop(path, None)
        await self._handle_upsert(path)

    async def _handle_upsert(self, path: Path) -> None:
        """Ingest (create/modify) a wiki .md file.

        HC-09: logs + swallows any exception so the watcher never crashes.
        """
        rel = self._rel(path)
        log.info("wiki_watcher.upsert_start", path=rel)
        try:
            async with self._session_factory() as session:
                repo = WikiIndexRepository(session)
                n = await wiki_ingest.ingest_file(
                    path,
                    qdrant=self._qdrant,
                    embedder=self._embedder,
                    wiki_index_repo=repo,
                    wiki_root=self._wiki_root,
                )
            log.info("wiki_watcher.upsert_done", path=rel, chunks=n)
        except Exception:  # noqa: BLE001 — HC-09 fail-soft
            log.exception("wiki_watcher.upsert_error", path=rel)

    async def _handle_delete(self, path: Path) -> None:
        """Remove all chunks for a deleted wiki file from Qdrant + wiki_index.

        HC-09: logs + swallows any exception so the watcher never crashes.
        """
        rel = self._rel(path)
        log.info("wiki_watcher.delete_start", path=rel)
        try:
            async with self._session_factory() as session:
                repo = WikiIndexRepository(session)
                existing_rows = await repo.get_by_path(rel)
                if not existing_rows:
                    log.info("wiki_watcher.delete_no_rows", path=rel)
                    return
                point_ids = [row.qdrant_point_id for row in existing_rows]
                await self._qdrant.delete(COLLECTION_NAME, point_ids)
                await repo.delete_by_path(rel)
            log.info("wiki_watcher.delete_done", path=rel, removed=len(point_ids))
        except Exception:  # noqa: BLE001 — HC-09 fail-soft
            log.exception("wiki_watcher.delete_error", path=rel)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        """Return path relative to wiki_root (or just the name if outside)."""
        try:
            return str(path.relative_to(self._wiki_root))
        except ValueError:
            return path.name


__all__ = ["WikiWatcher", "DEFAULT_DEBOUNCE_S"]
