"""Tests for the Fashion agent — outfit suggestions, wardrobe cataloging, shopping advice."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext, AgentResult
from agents.fashion import (
    FashionAgent,
    _build_wardrobe_catalog,
    _build_wardrobe_summary,
    _current_season,
    _determine_formality,
    _determine_season,
    _format_events,
    _resolve_suggested_items,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_context(
    message: str = "suggest outfit",
    action: str = "suggest",
    **extra_config: Any,
) -> AgentContext:
    return AgentContext(
        user_message=message,
        intent_type="fashion",
        config={"action": action, **extra_config},
    )


_WARDROBE_ITEMS = [
    {
        "id": "item-1",
        "item_type": "top",
        "sub_type": "t-shirt",
        "color": "blue",
        "pattern": "solid",
        "seasons": ["spring", "summer"],
        "formality": "casual",
        "brand": "Uniqlo",
        "image_path": "/data/wardrobe/item-1.jpg",
        "last_worn": "2026-03-05",
        "wear_count": 4,
    },
    {
        "id": "item-2",
        "item_type": "bottom",
        "sub_type": "jeans",
        "color": "dark blue",
        "pattern": "solid",
        "seasons": ["spring", "summer", "fall", "winter"],
        "formality": "casual",
        "brand": None,
        "image_path": "/data/wardrobe/item-2.jpg",
        "last_worn": None,
        "wear_count": 10,
    },
]


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestDetermineFormality:
    def test_no_events_returns_casual(self):
        assert _determine_formality([]) == "casual"

    def test_interview_returns_formal(self):
        events = [{"title": "Job Interview at Google"}]
        assert _determine_formality(events) == "formal"

    def test_meeting_returns_formal(self):
        events = [{"title": "Board Meeting"}]
        assert _determine_formality(events) == "formal"

    def test_class_returns_smart_casual(self):
        events = [{"title": "CS 101 class"}]
        assert _determine_formality(events) == "smart_casual"

    def test_gym_returns_casual(self):
        events = [{"title": "Gym session"}]
        assert _determine_formality(events) == "casual"

    def test_description_checked_too(self):
        events = [{"title": "Event", "description": "formal dinner"}]
        assert _determine_formality(events) == "formal"


class TestDetermineSeason:
    def test_cold_returns_winter(self):
        assert _determine_season({"temp_c": 0}) == "winter"

    def test_cool_returns_fall(self):
        assert _determine_season({"temp_c": 12}) == "fall"

    def test_warm_returns_spring(self):
        assert _determine_season({"temp_c": 22}) == "spring"

    def test_hot_returns_summer(self):
        assert _determine_season({"temp_c": 30}) == "summer"

    def test_missing_temp_falls_back_to_calendar(self):
        result = _determine_season({})
        assert result in ("spring", "summer", "fall", "winter")


class TestCurrentSeason:
    @patch("agents.fashion.date")
    def test_january_is_winter(self, mock_date):
        mock_date.today.return_value = date(2026, 1, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        assert _current_season() == "winter"


class TestBuildWardrobeCatalog:
    def test_empty_wardrobe(self):
        assert _build_wardrobe_catalog([], []) == "(empty wardrobe)"

    def test_marks_recently_worn(self):
        result = _build_wardrobe_catalog(_WARDROBE_ITEMS, ["item-1"])
        assert "*" in result.split("\n")[0]  # item-1 should have asterisk
        assert "*" not in result.split("\n")[1]  # item-2 should not

    def test_includes_brand(self):
        result = _build_wardrobe_catalog(_WARDROBE_ITEMS, [])
        assert "Uniqlo" in result

    def test_includes_all_items(self):
        result = _build_wardrobe_catalog(_WARDROBE_ITEMS, [])
        lines = result.strip().split("\n")
        assert len(lines) == 2


class TestFormatEvents:
    def test_no_events(self):
        assert _format_events([]) == "(no events today)"

    def test_formats_with_location(self):
        events = [{"start": "09:00", "title": "Standup", "location": "Zoom"}]
        result = _format_events(events)
        assert "Standup" in result
        assert "Zoom" in result


class TestResolveSuggestedItems:
    def test_resolves_known_ids(self):
        result = _resolve_suggested_items(["item-1"], _WARDROBE_ITEMS)
        assert len(result) == 1
        assert result[0]["id"] == "item-1"

    def test_ignores_unknown_ids(self):
        result = _resolve_suggested_items(["item-999"], _WARDROBE_ITEMS)
        assert len(result) == 0


class TestBuildWardrobeSummary:
    def test_empty_wardrobe(self):
        result = _build_wardrobe_summary([])
        assert result["by_type"] == {}
        assert result["color_palette"] == []

    def test_groups_by_type(self):
        result = _build_wardrobe_summary(_WARDROBE_ITEMS)
        assert "top" in result["by_type"]
        assert "bottom" in result["by_type"]
        assert result["by_type"]["top"]["count"] == 1

    def test_color_palette(self):
        result = _build_wardrobe_summary(_WARDROBE_ITEMS)
        assert "blue" in result["color_palette"]

    def test_formality_distribution(self):
        result = _build_wardrobe_summary(_WARDROBE_ITEMS)
        assert result["formality_distribution"]["casual"] == 2

    def test_seasons_coverage(self):
        result = _build_wardrobe_summary(_WARDROBE_ITEMS)
        assert result["seasons_coverage"]["spring"] == 2


# ---------------------------------------------------------------------------
# Agent-level tests (mocking external deps)
# ---------------------------------------------------------------------------


@patch("config.get_settings")
@patch("integrations.caldav_client.CalDAVClient")
@patch("integrations.weather_client.WeatherClient")
@patch("models.gemini_client.GeminiClient")
class TestFashionAgentSuggest:
    def _make_agent(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        mock_settings.return_value = MagicMock(
            gemini_api_key="test",
            openweathermap_api_key="test",
            default_location="Wooster,OH,US",
            icloud_caldav_user="test",
            icloud_caldav_password="test",
        )
        agent = FashionAgent()
        agent._gemini = mock_gemini.return_value
        agent._weather = mock_weather.return_value
        agent._caldav = mock_caldav.return_value
        return agent

    async def test_suggest_empty_wardrobe(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        with patch.object(agent, "_fetch_weather", return_value={"temp_c": 22}), \
             patch.object(agent, "_fetch_today_events", return_value=[]), \
             patch.object(agent, "_fetch_active_wardrobe", return_value=[]), \
             patch.object(agent, "_fetch_recently_worn", return_value=[]):
            result = await agent.execute(_make_context())

        assert result.success is True
        assert "empty" in result.text.lower()

    async def test_suggest_with_wardrobe(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        suggestion_json = json.dumps({
            "items": ["item-1", "item-2"],
            "suggestion": "Blue t-shirt with dark jeans",
            "reasoning": "Casual day, good weather",
        })

        mock_response = MagicMock()
        mock_response.text = suggestion_json
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 500
        mock_response.tokens_output = 200
        mock_response.duration_ms = 1200
        agent._gemini.generate = AsyncMock(return_value=mock_response)

        with patch.object(agent, "_fetch_weather", return_value={"temp_c": 22}), \
             patch.object(agent, "_fetch_today_events", return_value=[]), \
             patch.object(agent, "_fetch_active_wardrobe", return_value=_WARDROBE_ITEMS), \
             patch.object(agent, "_fetch_recently_worn", return_value=[]), \
             patch.object(agent, "_store_outfit", return_value=uuid.uuid4()):
            result = await agent.execute(_make_context())

        assert result.success is True
        assert "Blue t-shirt" in result.text
        assert result.content_type == "card"
        assert len(result.cards) == 1

    async def test_suggest_graceful_degradation_weather_fail(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        """HC-09: Weather failure should not crash the agent."""
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        suggestion_json = json.dumps({
            "items": ["item-1"],
            "suggestion": "Go with a t-shirt",
            "reasoning": "Default casual",
        })
        mock_response = MagicMock()
        mock_response.text = suggestion_json
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 500
        mock_response.tokens_output = 200
        mock_response.duration_ms = 1200
        agent._gemini.generate = AsyncMock(return_value=mock_response)

        with patch.object(agent, "_fetch_weather", side_effect=Exception("API down")), \
             patch.object(agent, "_fetch_today_events", return_value=[]), \
             patch.object(agent, "_fetch_active_wardrobe", return_value=_WARDROBE_ITEMS), \
             patch.object(agent, "_fetch_recently_worn", return_value=[]), \
             patch.object(agent, "_store_outfit", return_value=uuid.uuid4()):
            result = await agent.execute(_make_context())

        assert result.success is True


@patch("config.get_settings")
@patch("integrations.caldav_client.CalDAVClient")
@patch("integrations.weather_client.WeatherClient")
@patch("models.gemini_client.GeminiClient")
class TestFashionAgentCatalog:
    def _make_agent(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        mock_settings.return_value = MagicMock(
            gemini_api_key="test",
            openweathermap_api_key="test",
            default_location="Wooster,OH,US",
            icloud_caldav_user="test",
            icloud_caldav_password="test",
        )
        agent = FashionAgent()
        agent._gemini = mock_gemini.return_value
        agent._weather = mock_weather.return_value
        agent._caldav = mock_caldav.return_value
        return agent

    async def test_catalog_no_attachment(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)
        ctx = _make_context(action="catalog")
        result = await agent.execute(ctx)
        assert result.success is False
        assert "attach" in result.text.lower()

    async def test_catalog_empty_attachment(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)
        ctx = _make_context(action="catalog")
        ctx.attachments = [{"data": ""}]
        result = await agent.execute(ctx)
        assert result.success is False
        assert "empty" in result.text.lower()

    async def test_catalog_success(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        analysis_json = json.dumps({
            "item_type": "top",
            "sub_type": "t-shirt",
            "color": "red",
            "pattern": "solid",
            "seasons": ["summer"],
            "formality": "casual",
            "brand": None,
            "description": "Red cotton t-shirt",
        })
        mock_response = MagicMock()
        mock_response.text = analysis_json
        mock_response.model = "gemini-2.0-flash"
        mock_response.tokens_input = 200
        mock_response.tokens_output = 150
        mock_response.duration_ms = 800
        agent._gemini.generate_with_vision = AsyncMock(return_value=mock_response)

        image_data = base64.b64encode(b"fake image bytes").decode()
        ctx = _make_context(action="catalog")
        ctx.attachments = [{"data": image_data}]

        with patch.object(agent, "_store_wardrobe_item", return_value=uuid.uuid4()), \
             patch.object(agent, "_dispatch_image_save", return_value=None), \
             patch.object(agent, "_update_item_image_path", return_value=None):
            result = await agent.execute(ctx)

        assert result.success is True
        assert "Red cotton t-shirt" in result.text
        assert result.content_type == "card"


@patch("config.get_settings")
@patch("integrations.caldav_client.CalDAVClient")
@patch("integrations.weather_client.WeatherClient")
@patch("models.gemini_client.GeminiClient")
class TestFashionAgentShopping:
    def _make_agent(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        mock_settings.return_value = MagicMock(
            gemini_api_key="test",
            openweathermap_api_key="test",
            default_location="Wooster,OH,US",
            icloud_caldav_user="test",
            icloud_caldav_password="test",
        )
        agent = FashionAgent()
        agent._gemini = mock_gemini.return_value
        agent._weather = mock_weather.return_value
        agent._caldav = mock_caldav.return_value
        return agent

    async def test_shopping_empty_wardrobe(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        with patch.object(agent, "_fetch_active_wardrobe", return_value=[]):
            result = await agent.execute(_make_context(action="shopping_advisor"))

        assert result.success is True
        assert "empty" in result.text.lower()

    async def test_shopping_with_wardrobe(self, mock_gemini, mock_weather, mock_caldav, mock_settings):
        agent = self._make_agent(mock_gemini, mock_weather, mock_caldav, mock_settings)

        mock_response = MagicMock()
        mock_response.text = "You need more formal wear for interviews."
        mock_response.model = "gemini-2.0-pro"
        mock_response.tokens_input = 500
        mock_response.tokens_output = 300
        mock_response.duration_ms = 1500
        agent._gemini.generate = AsyncMock(return_value=mock_response)

        with patch.object(agent, "_fetch_active_wardrobe", return_value=_WARDROBE_ITEMS):
            result = await agent.execute(_make_context(action="shopping_advisor"))

        assert result.success is True
        assert "formal" in result.text.lower()
