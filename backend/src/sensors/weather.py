"""WeatherSensor — 15-minute cadence weather readings via WeatherClient.

Wraps :class:`integrations.weather_client.WeatherClient` to produce a merged
payload from ``get_current()`` and ``get_daily_summary()``, then publishes it
to ``world_state`` + the Redis pub/sub channel ``tars:world:weather``.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from integrations.weather_client import WeatherClient
from sensors.base import BaseSensor, SessionFactory


class WeatherSensor(BaseSensor):
    """Sensor that polls OpenWeatherMap every 15 minutes.

    Merges the current-conditions response with the daily summary to produce
    a single flat payload suitable for BriefingAgent consumption.
    """

    sensor_name = "weather"
    min_interval_seconds = 900.0

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis: Redis,
        weather_client: WeatherClient,
    ) -> None:
        super().__init__(session_factory=session_factory, redis=redis)
        self._weather_client = weather_client

    async def collect(self) -> dict[str, Any]:
        """Fetch current conditions + daily summary and merge into one payload."""
        current = await self._weather_client.get_current()
        daily = await self._weather_client.get_daily_summary()

        return {
            "temp_c": current["temp_c"],
            "temp_f": current["temp_f"],
            "conditions": current["conditions"],
            "humidity": current["humidity"],
            "wind_mph": current["wind_mph"],
            "icon": current["icon"],
            "location": current["location"],
            "daily_summary": daily["summary"],
            "high": daily["high"],
            "low": daily["low"],
            "needs_umbrella": daily["needs_umbrella"],
            "suggestion": daily["suggestion"],
        }


__all__ = ["WeatherSensor"]
