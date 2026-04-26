"""Tests for ``sensors.spotify.SpotifySensor``.

Covers:
- Token present + playing  → returns full track metadata
- Token present + idle     → returns playing=False, reason="idle"
- Token missing            → returns playing=False, reason="no_credentials"
- tick() publishes WorldState row when playing
- tick() publishes WorldState row when idle (playing=False)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from db.models import WorldState
from sensors.spotify import SpotifySensor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_playing_response() -> dict[str, Any]:
    return {
        "is_playing": True,
        "progress_ms": 45000,
        "item": {
            "name": "Bohemian Rhapsody",
            "artists": [{"name": "Queen"}],
            "album": {"name": "A Night at the Opera"},
            "duration_ms": 354000,
            "uri": "spotify:track:abc123",
        },
    }


class _FakeSession:
    """In-memory async session that records every ``add()``."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _session_factory() -> tuple[Any, _FakeSession]:
    session = _FakeSession()

    @asynccontextmanager
    async def factory():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return factory, session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
def session_pair():
    return _session_factory()


def _make_sensor(redis, session_factory, *, client_id="cid", client_secret="csec", refresh_token="rtoken"):
    """Build a SpotifySensor with spotipy init patched out."""
    with patch("spotipy.Spotify"), patch("spotipy.oauth2.SpotifyOAuth"):
        sensor = SpotifySensor(
            session_factory=session_factory,
            redis=redis,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
    # Replace the spotipy client with a plain MagicMock for fine-grained control
    sensor._spotify = MagicMock()
    return sensor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_collect_token_present_playing(redis, session_pair):
    factory, _session = session_pair
    sensor = _make_sensor(redis, factory)

    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_playing_response())):
        result = await sensor.collect()

    assert result["playing"] is True
    assert result["track"] == "Bohemian Rhapsody"
    assert result["artist"] == "Queen"
    assert result["album"] == "A Night at the Opera"
    assert result["progress_ms"] == 45000
    assert result["duration_ms"] == 354000
    assert result["uri"] == "spotify:track:abc123"


async def test_collect_token_present_idle(redis, session_pair):
    factory, _session = session_pair
    sensor = _make_sensor(redis, factory)

    idle_response = {"is_playing": False, "item": None}

    with patch("asyncio.to_thread", new=AsyncMock(return_value=idle_response)):
        result = await sensor.collect()

    assert result == {"playing": False, "reason": "idle"}


async def test_collect_token_missing(redis, session_pair):
    factory, _session = session_pair

    # No patching needed — __init__ sets self._spotify = None when creds are empty
    sensor = SpotifySensor(
        session_factory=factory,
        redis=redis,
        client_id="",
        client_secret="",
        refresh_token="",
    )

    result = await sensor.collect()

    assert result == {"playing": False, "reason": "no_credentials"}
    assert sensor._spotify is None


async def test_tick_publishes_world_state_row_when_playing(redis, session_pair):
    factory, session = session_pair
    sensor = _make_sensor(redis, factory)

    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_playing_response())):
        ok = await sensor.tick()

    assert ok is True
    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    row = world_rows[0]
    assert row.sensor == "spotify"
    assert row.payload["playing"] is True
    assert row.payload["track"] == "Bohemian Rhapsody"


async def test_tick_publishes_idle_state(redis, session_pair):
    factory, session = session_pair
    sensor = _make_sensor(redis, factory)

    with patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
        ok = await sensor.tick()

    assert ok is True
    world_rows = [obj for obj in session.added if isinstance(obj, WorldState)]
    assert len(world_rows) == 1
    row = world_rows[0]
    assert row.sensor == "spotify"
    assert row.payload["playing"] is False
    assert row.payload["reason"] == "idle"
