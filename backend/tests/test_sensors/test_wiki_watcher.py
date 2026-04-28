"""Tests for sensors.wiki_watcher.WikiWatcher (P3-07).

Strategy:
- Drive _handle_upsert / _handle_delete coroutines directly (no real Observer).
- Drive _on_modified / _on_created / _on_deleted synchronously and assert
  that tasks are enqueued on the mock loop.
- Observer thread not started in any test (fragile + requires real fs events).

Smoke test for full Observer wiring is marked @pytest.mark.slow and skipped
in normal CI runs.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sensors.wiki_watcher import WikiWatcher

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_event(src_path: str, *, is_directory: bool = False) -> SimpleNamespace:
    return SimpleNamespace(src_path=src_path, is_directory=is_directory)


class _FakeWikiIndex:
    """In-memory stand-in for WikiIndexRepository."""

    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows or []
        self.deleted: list[str] = []

    async def get_by_path(self, path: str) -> list:
        return [r for r in self.rows if r.path == path]

    async def delete_by_path(self, path: str) -> int:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.path != path]
        self.deleted.append(path)
        return before - len(self.rows)


def _make_row(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, qdrant_point_id=uuid.uuid4())


def _make_watcher(
    wiki_root: Path,
    *,
    qdrant: MagicMock | None = None,
    embedder: MagicMock | None = None,
    repo_rows: list | None = None,
    debounce_s: float = 0.0,
) -> tuple[WikiWatcher, MagicMock, _FakeWikiIndex]:
    """Build a WikiWatcher with mocked deps; return (watcher, qdrant, repo)."""
    qdrant = qdrant or MagicMock()
    qdrant.delete = AsyncMock()
    embedder = embedder or MagicMock()
    repo = _FakeWikiIndex(repo_rows)

    @asynccontextmanager
    async def session_factory():
        yield MagicMock()  # session itself unused; repo is injected in tests

    watcher = WikiWatcher(
        wiki_root,
        qdrant=qdrant,
        embedder=embedder,
        session_factory=session_factory,
        debounce_s=debounce_s,
    )
    # Patch WikiIndexRepository construction to return our fake repo
    watcher._fake_repo = repo
    return watcher, qdrant, repo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def wiki_root(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    return root


@pytest.fixture()
def watcher(wiki_root: Path):
    w, qdrant, repo = _make_watcher(wiki_root)
    return w, qdrant, repo


# ---------------------------------------------------------------------------
# 1. _on_modified filters non-.md files (no task scheduled)
# ---------------------------------------------------------------------------


def test_on_modified_ignores_non_md(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    watcher._on_modified(_make_event(str(wiki_root / "readme.txt")))

    loop.call_soon_threadsafe.assert_not_called()


# ---------------------------------------------------------------------------
# 2. _on_modified on .md schedules upsert via call_soon_threadsafe
# ---------------------------------------------------------------------------


def test_on_modified_md_schedules_upsert(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    md = wiki_root / "page.md"
    watcher._on_modified(_make_event(str(md)))

    loop.call_soon_threadsafe.assert_called_once()
    args = loop.call_soon_threadsafe.call_args[0]
    assert args[0] == watcher._enqueue_upsert
    assert args[1] == md.resolve()


# ---------------------------------------------------------------------------
# 3. _handle_upsert calls ingest_file with correct args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_upsert_calls_ingest_file(wiki_root: Path) -> None:
    md = wiki_root / "note.md"
    md.write_text("# Hello\n\nWorld")

    watcher, qdrant, repo = _make_watcher(wiki_root)

    with (
        patch("sensors.wiki_watcher.wiki_ingest.ingest_file", new=AsyncMock(return_value=2)) as mock_ingest,
        patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo),
    ):
        await watcher._handle_upsert(md)

    mock_ingest.assert_awaited_once()
    call_kwargs = mock_ingest.call_args
    assert call_kwargs[0][0] == md  # positional path
    assert call_kwargs[1]["qdrant"] is qdrant
    assert call_kwargs[1]["embedder"] is watcher._embedder
    assert call_kwargs[1]["wiki_root"] == wiki_root.resolve()


# ---------------------------------------------------------------------------
# 4. _handle_upsert swallows ingest exception (HC-09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_upsert_swallows_exception(wiki_root: Path) -> None:
    md = wiki_root / "boom.md"
    md.write_text("# Boom")

    watcher, _, repo = _make_watcher(wiki_root)

    with (
        patch("sensors.wiki_watcher.wiki_ingest.ingest_file", new=AsyncMock(side_effect=RuntimeError("embed fail"))),
        patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo),
    ):
        # Must NOT raise
        await watcher._handle_upsert(md)


# ---------------------------------------------------------------------------
# 5. _handle_delete removes chunks from qdrant + repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_delete_removes_chunks(wiki_root: Path) -> None:
    rel = "page.md"
    rows = [_make_row(rel), _make_row(rel)]
    expected_ids = [r.qdrant_point_id for r in rows]

    watcher, qdrant, repo = _make_watcher(wiki_root, repo_rows=rows)

    with patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo):
        await watcher._handle_delete(wiki_root / rel)

    qdrant.delete.assert_awaited_once()
    call_args = qdrant.delete.call_args
    # collection is first positional, point_ids is second
    assert call_args[0][1] == expected_ids
    assert rel in repo.deleted


# ---------------------------------------------------------------------------
# 6. _handle_delete swallows missing-rows case (empty result = early return)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_delete_no_rows_is_noop(wiki_root: Path) -> None:
    watcher, qdrant, repo = _make_watcher(wiki_root, repo_rows=[])

    with patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo):
        await watcher._handle_delete(wiki_root / "ghost.md")

    qdrant.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Debounce: two rapid events same file → one ingest call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_coalesces_rapid_events(wiki_root: Path) -> None:
    md = wiki_root / "rapid.md"
    md.write_text("# Rapid")

    watcher, qdrant, repo = _make_watcher(wiki_root, debounce_s=0.05)
    loop = asyncio.get_event_loop()
    watcher._loop = loop

    call_count = 0

    async def fake_ingest(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return 1

    with (
        patch("sensors.wiki_watcher.wiki_ingest.ingest_file", new=fake_ingest),
        patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo),
    ):
        # Fire two rapid events via the enqueue path (on the loop directly)
        watcher._enqueue_upsert(md.resolve())
        watcher._enqueue_upsert(md.resolve())  # second cancels first

        # Wait long enough for debounce to fire
        await asyncio.sleep(0.15)

    assert call_count == 1, f"Expected 1 ingest call, got {call_count}"


# ---------------------------------------------------------------------------
# 8. Two different files don't debounce each other
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_different_files_independent(wiki_root: Path) -> None:
    md_a = wiki_root / "a.md"
    md_b = wiki_root / "b.md"
    md_a.write_text("# A")
    md_b.write_text("# B")

    watcher, _, repo = _make_watcher(wiki_root, debounce_s=0.05)
    loop = asyncio.get_event_loop()
    watcher._loop = loop

    ingested: list[Path] = []

    async def fake_ingest(path, **kwargs):
        ingested.append(path)
        return 1

    with (
        patch("sensors.wiki_watcher.wiki_ingest.ingest_file", new=fake_ingest),
        patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo),
    ):
        watcher._enqueue_upsert(md_a.resolve())
        watcher._enqueue_upsert(md_b.resolve())

        await asyncio.sleep(0.15)

    assert len(ingested) == 2
    assert md_a.resolve() in ingested
    assert md_b.resolve() in ingested


# ---------------------------------------------------------------------------
# 9. _on_created triggers upsert
# ---------------------------------------------------------------------------


def test_on_created_md_schedules_upsert(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    md = wiki_root / "new.md"
    watcher._on_created(_make_event(str(md)))

    loop.call_soon_threadsafe.assert_called_once()
    args = loop.call_soon_threadsafe.call_args[0]
    assert args[0] == watcher._enqueue_upsert


# ---------------------------------------------------------------------------
# 10. _on_deleted triggers delete handler
# ---------------------------------------------------------------------------


def test_on_deleted_md_schedules_delete(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    md = wiki_root / "gone.md"
    watcher._on_deleted(_make_event(str(md)))

    loop.call_soon_threadsafe.assert_called_once()
    args = loop.call_soon_threadsafe.call_args[0]
    assert args[0] == watcher._enqueue_delete


# ---------------------------------------------------------------------------
# 11. start() is idempotent (double-call is a no-op / warning)
# ---------------------------------------------------------------------------


def test_start_is_idempotent(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)

    with patch("sensors.wiki_watcher.Observer") as MockObserver:
        mock_obs = MagicMock()
        MockObserver.return_value = mock_obs

        watcher.start()
        watcher.start()  # second call should be a no-op

    # Observer.start() called exactly once
    mock_obs.start.assert_called_once()


# ---------------------------------------------------------------------------
# 12. stop() cancels pending tasks gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_cancels_pending_tasks(wiki_root: Path) -> None:
    md = wiki_root / "pending.md"
    md.write_text("# Pending")

    watcher, _, repo = _make_watcher(wiki_root, debounce_s=60.0)  # very long debounce
    loop = asyncio.get_event_loop()
    watcher._loop = loop

    with patch("sensors.wiki_watcher.Observer") as MockObserver:
        mock_obs = MagicMock()
        MockObserver.return_value = mock_obs

        watcher.start()

        # Enqueue a task that will sit waiting for 60s
        watcher._enqueue_upsert(md.resolve())
        assert len(watcher._pending) == 1

        # Allow the task to start
        await asyncio.sleep(0)

        await watcher.stop()

    # After stop, pending should be cleared
    assert len(watcher._pending) == 0
    mock_obs.stop.assert_called_once()
    mock_obs.join.assert_called_once()


# ---------------------------------------------------------------------------
# 13. _on_modified ignores directory events
# ---------------------------------------------------------------------------


def test_on_modified_ignores_directory(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    watcher._on_modified(_make_event(str(wiki_root / "subdir"), is_directory=True))

    loop.call_soon_threadsafe.assert_not_called()


# ---------------------------------------------------------------------------
# 14. _on_deleted ignores non-.md files
# ---------------------------------------------------------------------------


def test_on_deleted_ignores_non_md(wiki_root: Path) -> None:
    watcher, _, _ = _make_watcher(wiki_root)
    loop = MagicMock()
    watcher._loop = loop

    watcher._on_deleted(_make_event(str(wiki_root / "image.png")))

    loop.call_soon_threadsafe.assert_not_called()


# ---------------------------------------------------------------------------
# Smoke test — real Observer wiring (skipped in normal CI)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_smoke_observer_fires_on_real_write(wiki_root: Path) -> None:
    """End-to-end smoke: write a file and assert ingest_file gets called."""
    import asyncio

    watcher, _, repo = _make_watcher(wiki_root, debounce_s=0.1)
    loop = asyncio.get_event_loop()
    watcher._loop = loop

    ingested: list[Path] = []

    async def fake_ingest(path, **kwargs):
        ingested.append(path)
        return 1

    with (
        patch("sensors.wiki_watcher.wiki_ingest.ingest_file", new=fake_ingest),
        patch("sensors.wiki_watcher.WikiIndexRepository", return_value=repo),
    ):
        watcher.start()
        try:
            md = wiki_root / "smoke.md"
            md.write_text("# Smoke test")
            await asyncio.sleep(0.4)  # let watchdog pick it up + debounce fire
        finally:
            await watcher.stop()

    assert len(ingested) >= 1
    assert md.resolve() in ingested
