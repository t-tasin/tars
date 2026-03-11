"""OpenWeatherMap API client for T.A.R.S."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from utils.resilience import CircuitBreakerOpenError, get_service_health_registry, retry_with_backoff

log = structlog.get_logger()

_BASE_URL = "https://api.openweathermap.org/data/2.5"
_SERVICE_NAME = "weather"


class WeatherClient:
    """Async OpenWeatherMap client.

    The *default_location* is pulled from ``Settings.default_location``
    (pitfall #8: never hardcode Wooster, Ohio).

    Includes retry with exponential backoff (3 attempts) and circuit
    breaker (3 failures in 5 min → 1 min cooldown).
    """

    def __init__(self, api_key: str, default_location: str) -> None:
        self._api_key = api_key
        self._default_location = default_location
        self._http = httpx.AsyncClient(timeout=10.0)
        self._cb = get_service_health_registry().get_or_create(_SERVICE_NAME)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_current(self, location: str | None = None) -> dict[str, Any]:
        """Current weather conditions.

        Returns::

            {"temp_c": 18, "temp_f": 64.4, "conditions": "partly cloudy",
             "humidity": 65, "wind_mph": 8.0, "icon": "02d",
             "location": "Wooster"}
        """
        loc = location or self._default_location
        data = await self._get("/weather", {"q": loc, "units": "metric"})

        main = data.get("main", {})
        wind = data.get("wind", {})
        weather = data["weather"][0] if data.get("weather") else {}

        temp_c = main.get("temp", 0)
        return {
            "temp_c": round(temp_c, 1),
            "temp_f": round(temp_c * 9 / 5 + 32, 1),
            "conditions": weather.get("description", "unknown"),
            "humidity": main.get("humidity", 0),
            "wind_mph": round((wind.get("speed", 0)) * 2.237, 1),  # m/s → mph
            "icon": weather.get("icon", ""),
            "location": data.get("name", loc),
        }

    async def get_forecast(
        self, location: str | None = None, hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Hourly forecast (3-hour intervals from the free 5-day endpoint).

        Returns a list capped to *hours* worth of data::

            [{"time": "14:00", "temp_c": 20, "temp_f": 68.0,
              "conditions": "sunny", "rain_probability": 10}, ...]
        """
        loc = location or self._default_location
        data = await self._get("/forecast", {"q": loc, "units": "metric"})

        entries = data.get("list", [])
        # Each entry is a 3-hour slot; cap to requested window.
        max_slots = max(1, hours // 3)

        result: list[dict[str, Any]] = []
        for entry in entries[:max_slots]:
            weather = entry["weather"][0] if entry.get("weather") else {}
            temp_c = entry.get("main", {}).get("temp", 0)
            pop = entry.get("pop", 0)  # 0-1 probability of precipitation
            dt_txt: str = entry.get("dt_txt", "")
            time_str = dt_txt.split(" ")[1][:5] if " " in dt_txt else dt_txt

            result.append({
                "time": time_str,
                "temp_c": round(temp_c, 1),
                "temp_f": round(temp_c * 9 / 5 + 32, 1),
                "conditions": weather.get("description", "unknown"),
                "rain_probability": round(pop * 100),
            })

        return result

    async def get_daily_summary(self, location: str | None = None) -> dict[str, Any]:
        """Summary suitable for the morning briefing.

        Combines current conditions with the day's forecast to produce::

            {"summary": "18°C, partly cloudy, no rain expected.",
             "high": 22, "low": 14, "needs_umbrella": False,
             "suggestion": "Great weather — good day for outdoor activities."}
        """
        current = await self.get_current(location)
        forecast = await self.get_forecast(location, hours=24)

        temps = [f["temp_c"] for f in forecast] if forecast else [current["temp_c"]]
        high = round(max(temps), 1)
        low = round(min(temps), 1)

        max_rain = max((f["rain_probability"] for f in forecast), default=0)
        needs_umbrella = max_rain >= 50

        # Build human-readable summary
        rain_note = (
            f"rain likely ({max_rain}% chance)"
            if needs_umbrella
            else "no rain expected"
        )
        summary = (
            f"{current['temp_c']}°C, {current['conditions']}, {rain_note}."
        )

        suggestion = _build_suggestion(current["temp_c"], current["conditions"], needs_umbrella)

        return {
            "summary": summary,
            "high": high,
            "low": low,
            "needs_umbrella": needs_umbrella,
            "suggestion": suggestion,
        }

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @retry_with_backoff(
        max_retries=3,
        backoff_seconds=(1.0, 2.0, 4.0),
        retryable_exceptions=(httpx.TimeoutException, httpx.ConnectError),
        service=_SERVICE_NAME,
    )
    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue a GET request, inject the API key, and return parsed JSON.

        Retries on timeouts and connection errors (3 attempts, exponential
        backoff). Circuit breaker opens after 3 failures in 5 minutes.
        """
        params = {**params, "appid": self._api_key}
        url = f"{_BASE_URL}{path}"

        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            log.debug("weather_api_ok", path=path, status=resp.status_code)
            return data
        except httpx.HTTPStatusError as exc:
            log.error(
                "weather_api_error",
                path=path,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            raise
        except httpx.RequestError as exc:
            log.error("weather_request_error", path=path, error=str(exc))
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_suggestion(temp_c: float, conditions: str, needs_umbrella: bool) -> str:
    """Generate a brief outfit/activity suggestion."""
    parts: list[str] = []

    cond_lower = conditions.lower()
    if "clear" in cond_lower or "sunny" in cond_lower:
        parts.append("Great weather — good day for outdoor activities.")
    elif "cloud" in cond_lower:
        parts.append("Overcast skies — layers recommended.")
    elif "rain" in cond_lower or "drizzle" in cond_lower:
        parts.append("Rainy — bring an umbrella and waterproof layer.")
    elif "snow" in cond_lower:
        parts.append("Snow expected — dress warm and allow extra commute time.")
    else:
        parts.append("Mixed conditions — check before heading out.")

    if needs_umbrella and "umbrella" not in " ".join(parts).lower():
        parts.append("Bring an umbrella.")

    if temp_c <= 0:
        parts.append("Freezing — heavy coat, gloves, and hat.")
    elif temp_c <= 10:
        parts.append("Cold — a warm jacket is recommended.")
    elif temp_c >= 30:
        parts.append("Hot — stay hydrated and wear light clothing.")

    return " ".join(parts)
