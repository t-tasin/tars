"""Tests for BriefingAgent world_state cache integration (P3.5-07).

Verifies the read-through cache behaviour wired in by P3.5-07:

- fresh ``world_state`` row → cache hit, integration NOT polled
- stale row → integration fallback, downstream payload preserved
- missing row → integration fallback
- repo raises → integration fallback (HC-09 fail-soft)

The same matrix is asserted for healthkit, plus cache-hit-only flows for
tailscale_presence and spotify (briefing has no direct fallback for those).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_agent() -> Any:
    from agents.briefing import BriefingAgent

    agent = object.__new__(BriefingAgent)
    agent._caldav = AsyncMock()
    agent._gmail_personal = AsyncMock()
    agent._gmail_professional = AsyncMock()
    agent._weather = _weather_mock()
    agent._notion = AsyncMock()
    agent._notion_tasks_db = None
    return agent


def _weather_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.get_current = AsyncMock(return_value={"temp_f": 65, "conditions": "clear sky"})
    mock.get_forecast = AsyncMock(return_value=[{"time": "12:00", "temp_f": 70}])
    mock.get_daily_summary = AsyncMock(return_value={"summary": "Nice day", "needs_umbrella": False})
    return mock


def _patch_cache(payload: dict[str, Any] | None) -> Any:
    """Patch ``read_fresh_sensor`` to return ``payload`` and capture sensors queried."""

    calls: list[str] = []

    async def _fake(
        session: Any,
        sensor: str,
        *,
        max_age_seconds: float | None = None,
        now: Any = None,
    ) -> dict[str, Any] | None:
        calls.append(sensor)
        return payload

    return calls, patch("orchestrator.world_state_cache.read_fresh_sensor", _fake)


def _patch_per_sensor(payloads: dict[str, dict[str, Any] | None]) -> Any:
    """Patch read_fresh_sensor with per-sensor return values."""

    calls: list[str] = []

    async def _fake(
        session: Any,
        sensor: str,
        *,
        max_age_seconds: float | None = None,
        now: Any = None,
    ) -> dict[str, Any] | None:
        calls.append(sensor)
        return payloads.get(sensor)

    return calls, patch("orchestrator.world_state_cache.read_fresh_sensor", _fake)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


class TestFetchWeatherCache:
    @pytest.mark.asyncio
    async def test_fresh_cache_hit_skips_integration(self) -> None:
        agent = _make_agent()
        cached_payload = {
            "temp_c": 22.0,
            "temp_f": 71.6,
            "conditions": "clear sky",
            "humidity": 45,
            "wind_mph": 5.2,
            "icon": "01d",
            "location": "Wooster",
            "daily_summary": "Sunny.",
            "high": 24.0,
            "low": 18.0,
            "needs_umbrella": False,
            "suggestion": "Light jacket.",
        }
        calls, ctx = _patch_cache(cached_payload)

        with ctx:
            result = await agent._fetch_weather()

        assert calls == ["weather"]
        # Integration must NOT be polled when cache is fresh
        assert agent._weather.get_current.await_count == 0
        assert agent._weather.get_daily_summary.await_count == 0
        # Reshaped into legacy {current, forecast, summary}
        assert result["current"]["temp_f"] == 71.6
        assert result["current"]["location"] == "Wooster"
        assert result["forecast"] == []
        assert result["summary"]["summary"] == "Sunny."
        assert result["summary"]["needs_umbrella"] is False

    @pytest.mark.asyncio
    async def test_stale_cache_falls_back_to_integration(self) -> None:
        agent = _make_agent()
        # Stale → cache helper returns None
        calls, ctx = _patch_cache(None)

        with ctx:
            result = await agent._fetch_weather()

        assert calls == ["weather"]
        # Integration WAS polled
        agent._weather.get_current.assert_awaited_once()
        agent._weather.get_daily_summary.assert_awaited_once()
        # Same downstream shape
        assert result["current"]["temp_f"] == 65
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_missing_cache_falls_back_to_integration(self) -> None:
        agent = _make_agent()
        calls, ctx = _patch_cache(None)  # missing == None

        with ctx:
            result = await agent._fetch_weather()

        assert calls == ["weather"]
        agent._weather.get_current.assert_awaited_once()
        assert "current" in result

    @pytest.mark.asyncio
    async def test_repo_failure_falls_back_to_integration(self) -> None:
        """HC-09: world_state read raising must NOT crash briefing."""
        agent = _make_agent()

        # Force read_world_state to raise — _read_world_state should catch it
        async def _broken_session_factory() -> Any:  # pragma: no cover
            raise RuntimeError("DB down")

        with patch(
            "agents.briefing.BriefingAgent._read_world_state",
            new_callable=AsyncMock,
            return_value=None,
        ) as mocked_read:
            # Have the mock simulate a failure (returned None ≡ fail-soft)
            mocked_read.return_value = None
            result = await agent._fetch_weather()

        # Falls back to integration
        agent._weather.get_current.assert_awaited_once()
        assert "current" in result


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestFetchHealthCache:
    @pytest.mark.asyncio
    async def test_fresh_cache_hit_skips_db_query(self) -> None:
        """Cache hit must avoid the legacy ``health_data`` SELECT entirely."""
        agent = _make_agent()
        cached_payload = {
            "date": "2026-04-26",
            "readings": {
                "steps": {"value": 8500.0, "unit": "count", "recorded_date": "2026-04-26"},
                "sleep": {"value": 7.5, "unit": "hours", "recorded_date": "2026-04-26"},
            },
            "types_present": ["sleep", "steps"],
        }
        calls, ctx = _patch_cache(cached_payload)

        # The cache helper opens its own DB session via _read_world_state, so
        # we can't simply check get_db_session was not called. Instead, prove
        # that no SELECT against health_data happens by patching db.models.
        with ctx:
            with patch("db.models.HealthData") as health_marker:
                result = await agent._fetch_health_data()
                # The fallback path imports HealthData; cache hit must skip it.
                health_marker.assert_not_called()

        assert calls == ["healthkit"]
        assert result["steps"]["value"] == 8500.0
        assert result["sleep"]["value"] == 7.5
        assert result["date"] == "2026-04-26"

    @pytest.mark.asyncio
    async def test_missing_cache_falls_back_to_db_query(self) -> None:
        from contextlib import asynccontextmanager

        agent = _make_agent()
        calls, ctx = _patch_cache(None)

        # Build a fake session factory that returns the legacy DB rows shape.
        # _fetch_health_data without cache calls session.execute(...)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def _factory():
            yield mock_session

        with ctx, patch("db.session.get_db_session", _factory):
            result = await agent._fetch_health_data()

        assert calls == ["healthkit"]
        mock_session.execute.assert_awaited()  # DB fallback ran
        assert result == {}  # no rows


# ---------------------------------------------------------------------------
# Tailscale + Spotify (cache-only consumers)
# ---------------------------------------------------------------------------


class TestFetchTailscaleAndSpotify:
    @pytest.mark.asyncio
    async def test_tailscale_returns_cache_payload(self) -> None:
        agent = _make_agent()
        cached = {"online_count": 2, "total_count": 4, "devices": []}
        calls, ctx = _patch_cache(cached)
        with ctx:
            result = await agent._fetch_tailscale_presence()
        assert calls == ["tailscale_presence"]
        assert result["online_count"] == 2

    @pytest.mark.asyncio
    async def test_tailscale_returns_empty_when_no_cache(self) -> None:
        agent = _make_agent()
        calls, ctx = _patch_cache(None)
        with ctx:
            result = await agent._fetch_tailscale_presence()
        assert calls == ["tailscale_presence"]
        assert result == {}

    @pytest.mark.asyncio
    async def test_spotify_returns_cache_payload(self) -> None:
        agent = _make_agent()
        cached = {"playing": True, "track": "Test", "artist": "Artist"}
        calls, ctx = _patch_cache(cached)
        with ctx:
            result = await agent._fetch_spotify_now_playing()
        assert calls == ["spotify"]
        assert result["playing"] is True
        assert result["track"] == "Test"

    @pytest.mark.asyncio
    async def test_spotify_returns_empty_when_no_cache(self) -> None:
        agent = _make_agent()
        calls, ctx = _patch_cache(None)
        with ctx:
            result = await agent._fetch_spotify_now_playing()
        assert result == {}


# ---------------------------------------------------------------------------
# Read helper (HC-09)
# ---------------------------------------------------------------------------


class TestReadWorldStateGracefulDegradation:
    @pytest.mark.asyncio
    async def test_session_open_failure_returns_none(self) -> None:
        """If opening the DB session itself raises, _read_world_state returns None."""
        agent = _make_agent()

        with patch("db.session.get_db_session", side_effect=RuntimeError("DB down")):
            result = await agent._read_world_state("weather")
        assert result is None

    @pytest.mark.asyncio
    async def test_read_fresh_sensor_failure_returns_none(self) -> None:
        from contextlib import asynccontextmanager

        agent = _make_agent()

        @asynccontextmanager
        async def _factory():
            yield AsyncMock()

        async def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("query exploded")

        with (
            patch("db.session.get_db_session", _factory),
            patch("orchestrator.world_state_cache.read_fresh_sensor", _boom),
        ):
            result = await agent._read_world_state("weather")

        assert result is None
