"""Tests for WeatherClient with mocked httpx responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from integrations.weather_client import WeatherClient, _build_suggestion


# ---------------------------------------------------------------------------
# Fake OWM API responses
# ---------------------------------------------------------------------------

_CURRENT_RESPONSE = {
    "name": "Wooster",
    "main": {"temp": 18.0, "humidity": 65},
    "wind": {"speed": 3.5},  # m/s
    "weather": [{"description": "partly cloudy", "icon": "02d"}],
}

_FORECAST_RESPONSE = {
    "list": [
        {
            "dt_txt": "2026-03-10 12:00:00",
            "main": {"temp": 20.0},
            "weather": [{"description": "sunny"}],
            "pop": 0.1,
        },
        {
            "dt_txt": "2026-03-10 15:00:00",
            "main": {"temp": 22.0},
            "weather": [{"description": "clear sky"}],
            "pop": 0.05,
        },
        {
            "dt_txt": "2026-03-10 18:00:00",
            "main": {"temp": 19.0},
            "weather": [{"description": "partly cloudy"}],
            "pop": 0.15,
        },
        {
            "dt_txt": "2026-03-10 21:00:00",
            "main": {"temp": 14.0},
            "weather": [{"description": "cloudy"}],
            "pop": 0.2,
        },
        {
            "dt_txt": "2026-03-11 00:00:00",
            "main": {"temp": 12.0},
            "weather": [{"description": "cloudy"}],
            "pop": 0.3,
        },
        {
            "dt_txt": "2026-03-11 03:00:00",
            "main": {"temp": 11.0},
            "weather": [{"description": "cloudy"}],
            "pop": 0.25,
        },
        {
            "dt_txt": "2026-03-11 06:00:00",
            "main": {"temp": 10.0},
            "weather": [{"description": "cloudy"}],
            "pop": 0.2,
        },
        {
            "dt_txt": "2026-03-11 09:00:00",
            "main": {"temp": 13.0},
            "weather": [{"description": "partly cloudy"}],
            "pop": 0.1,
        },
    ],
}

_RAINY_FORECAST = {
    "list": [
        {
            "dt_txt": "2026-03-10 12:00:00",
            "main": {"temp": 15.0},
            "weather": [{"description": "light rain"}],
            "pop": 0.8,
        },
        {
            "dt_txt": "2026-03-10 15:00:00",
            "main": {"temp": 13.0},
            "weather": [{"description": "rain"}],
            "pop": 0.9,
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://fake"),
    )


@pytest.fixture
def client() -> WeatherClient:
    return WeatherClient(api_key="test-key", default_location="Wooster,OH,US")


# ---------------------------------------------------------------------------
# get_current()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_returns_structured_dict(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_CURRENT_RESPONSE))

    result = await client.get_current()

    assert result["temp_c"] == 18.0
    assert result["temp_f"] == pytest.approx(64.4, abs=0.1)
    assert result["conditions"] == "partly cloudy"
    assert result["humidity"] == 65
    assert result["wind_mph"] == pytest.approx(7.8, abs=0.2)
    assert result["icon"] == "02d"
    assert result["location"] == "Wooster"


@pytest.mark.asyncio
async def test_get_current_custom_location(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_CURRENT_RESPONSE))

    await client.get_current(location="Columbus,OH,US")

    call_kwargs = client._http.get.call_args
    assert "Columbus,OH,US" in str(call_kwargs)


@pytest.mark.asyncio
async def test_get_current_uses_default_location(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_CURRENT_RESPONSE))

    await client.get_current()

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["q"] == "Wooster,OH,US"


@pytest.mark.asyncio
async def test_get_current_includes_api_key(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_CURRENT_RESPONSE))

    await client.get_current()

    call_kwargs = client._http.get.call_args
    params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
    assert params["appid"] == "test-key"


# ---------------------------------------------------------------------------
# get_forecast()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_forecast_returns_list(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_FORECAST_RESPONSE))

    result = await client.get_forecast()

    # 24 hours / 3-hour slots = 8 slots, and we have 8 entries
    assert len(result) == 8
    assert result[0]["time"] == "12:00"
    assert result[0]["temp_c"] == 20.0
    assert result[0]["conditions"] == "sunny"
    assert result[0]["rain_probability"] == 10


@pytest.mark.asyncio
async def test_get_forecast_caps_to_requested_hours(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_FORECAST_RESPONSE))

    result = await client.get_forecast(hours=6)

    # 6 hours / 3-hour slots = 2 entries
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_forecast_includes_temp_f(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=_mock_response(_FORECAST_RESPONSE))

    result = await client.get_forecast(hours=3)

    assert result[0]["temp_f"] == pytest.approx(68.0, abs=0.1)


# ---------------------------------------------------------------------------
# get_daily_summary()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_daily_summary_no_rain(client: WeatherClient):
    current_resp = _mock_response(_CURRENT_RESPONSE)
    forecast_resp = _mock_response(_FORECAST_RESPONSE)
    client._http = AsyncMock()
    client._http.get = AsyncMock(side_effect=[current_resp, forecast_resp])

    result = await client.get_daily_summary()

    assert result["high"] == 22.0
    assert result["low"] == 10.0
    assert result["needs_umbrella"] is False
    assert "no rain expected" in result["summary"]
    assert isinstance(result["suggestion"], str)
    assert len(result["suggestion"]) > 0


@pytest.mark.asyncio
async def test_get_daily_summary_needs_umbrella(client: WeatherClient):
    rainy_current = {
        **_CURRENT_RESPONSE,
        "weather": [{"description": "light rain", "icon": "10d"}],
    }
    current_resp = _mock_response(rainy_current)
    forecast_resp = _mock_response(_RAINY_FORECAST)
    client._http = AsyncMock()
    client._http.get = AsyncMock(side_effect=[current_resp, forecast_resp])

    result = await client.get_daily_summary()

    assert result["needs_umbrella"] is True
    assert "rain likely" in result["summary"]


@pytest.mark.asyncio
async def test_get_daily_summary_custom_location(client: WeatherClient):
    current_resp = _mock_response(_CURRENT_RESPONSE)
    forecast_resp = _mock_response(_FORECAST_RESPONSE)
    client._http = AsyncMock()
    client._http.get = AsyncMock(side_effect=[current_resp, forecast_resp])

    await client.get_daily_summary(location="Akron,OH,US")

    # Both internal calls should use the custom location
    calls = client._http.get.call_args_list
    for call in calls:
        params = call.kwargs.get("params", call[1].get("params", {}))
        assert params["q"] == "Akron,OH,US"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_error_raises(client: WeatherClient):
    error_resp = _mock_response({"cod": 401, "message": "Invalid API key"}, status_code=401)
    client._http = AsyncMock()
    client._http.get = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_current()


@pytest.mark.asyncio
async def test_network_error_raises(client: WeatherClient):
    client._http = AsyncMock()
    client._http.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(httpx.RequestError):
        await client.get_current()


# ---------------------------------------------------------------------------
# _build_suggestion() helper
# ---------------------------------------------------------------------------


def test_suggestion_clear_sky():
    s = _build_suggestion(22.0, "clear sky", needs_umbrella=False)
    assert "outdoor" in s.lower()


def test_suggestion_rain():
    s = _build_suggestion(15.0, "light rain", needs_umbrella=True)
    assert "umbrella" in s.lower()


def test_suggestion_snow():
    s = _build_suggestion(-2.0, "snow", needs_umbrella=False)
    assert "snow" in s.lower() or "warm" in s.lower()


def test_suggestion_cold():
    s = _build_suggestion(5.0, "cloudy", needs_umbrella=False)
    assert "jacket" in s.lower() or "cold" in s.lower()


def test_suggestion_hot():
    s = _build_suggestion(35.0, "clear sky", needs_umbrella=False)
    assert "hydrated" in s.lower() or "light" in s.lower()


def test_suggestion_umbrella_added_when_needed():
    s = _build_suggestion(20.0, "cloudy", needs_umbrella=True)
    assert "umbrella" in s.lower()


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_shuts_down_http_client(client: WeatherClient):
    client._http = AsyncMock()
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_awaited_once()
