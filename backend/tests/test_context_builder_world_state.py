"""Tests for ContextBuilder weather pre-fetch reading from world_state (P3.5-07)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import IntentType, ModelName

from orchestrator.context_builder import ContextBuilder
from orchestrator.intent_classifier import Intent
from orchestrator.model_router import ModelRoute


def _route() -> ModelRoute:
    return ModelRoute(model=ModelName.LOCAL_BRAIN, node="node2")


def _patch_cache(payload: dict[str, Any] | None) -> Any:
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


@pytest.mark.asyncio
async def test_weather_prefetch_uses_world_state_when_fresh() -> None:
    """Cache hit → WeatherClient must NOT be constructed or polled."""
    builder = ContextBuilder()
    cached = {
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
    calls, ctx = _patch_cache(cached)

    weather_client_cls = MagicMock()

    with (
        ctx,
        patch("integrations.weather_client.WeatherClient", weather_client_cls),
    ):
        intent = Intent(agent=IntentType.GENERAL)
        ctx_built = await builder.build(intent, _route(), "What's the weather?", "telegram")

    assert calls == ["weather"]
    weather_client_cls.assert_not_called()  # integration NOT constructed
    assert ctx_built.system_context["weather"]["temp_f"] == 71.6
    assert ctx_built.system_context["weather"]["location"] == "Wooster"


@pytest.mark.asyncio
async def test_weather_prefetch_falls_back_when_stale() -> None:
    """Cache miss/stale → falls back to WeatherClient.get_current()."""
    builder = ContextBuilder()
    calls, ctx = _patch_cache(None)  # treat None as stale/missing

    mock_client = MagicMock()
    mock_client.get_current = AsyncMock(
        return_value={"temp_f": 65.0, "location": "Wooster"}
    )
    mock_client.close = AsyncMock()

    with (
        ctx,
        patch("integrations.weather_client.WeatherClient", return_value=mock_client),
    ):
        intent = Intent(agent=IntentType.GENERAL)
        ctx_built = await builder.build(intent, _route(), "weather forecast?", "telegram")

    assert calls == ["weather"]
    mock_client.get_current.assert_awaited_once()  # fallback fired
    assert ctx_built.system_context["weather"]["temp_f"] == 65.0


@pytest.mark.asyncio
async def test_weather_prefetch_missing_row_falls_back() -> None:
    """Missing world_state row is the same code path as stale → fallback."""
    builder = ContextBuilder()
    calls, ctx = _patch_cache(None)

    mock_client = MagicMock()
    mock_client.get_current = AsyncMock(return_value={"temp_f": 50.0})
    mock_client.close = AsyncMock()

    with (
        ctx,
        patch("integrations.weather_client.WeatherClient", return_value=mock_client),
    ):
        intent = Intent(agent=IntentType.GENERAL)
        ctx_built = await builder.build(intent, _route(), "is it raining?", "telegram")

    mock_client.get_current.assert_awaited_once()
    assert ctx_built.system_context["weather"]["temp_f"] == 50.0


@pytest.mark.asyncio
async def test_weather_prefetch_world_state_failure_falls_back() -> None:
    """HC-09: world_state read raising must fall back, never propagate."""
    builder = ContextBuilder()

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("DB exploded")

    mock_client = MagicMock()
    mock_client.get_current = AsyncMock(return_value={"temp_f": 60.0})
    mock_client.close = AsyncMock()

    with (
        patch("orchestrator.world_state_cache.read_fresh_sensor", _boom),
        patch("integrations.weather_client.WeatherClient", return_value=mock_client),
    ):
        intent = Intent(agent=IntentType.GENERAL)
        ctx_built = await builder.build(intent, _route(), "temperature?", "telegram")

    # Fell back to integration despite repo failure
    mock_client.get_current.assert_awaited_once()
    assert ctx_built.system_context["weather"]["temp_f"] == 60.0
