"""TailscaleSensor — 2-minute presence poll via Tailscale API.

Polls ``GET https://api.tailscale.com/api/v2/tailnet/-/devices`` and
determines which devices are "online" (``lastSeen`` within 5 minutes of
UTC now). Publishes to ``world_state`` via the BaseSensor pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from redis.asyncio import Redis

from sensors.base import BaseSensor, SessionFactory

log = structlog.get_logger()

_TAILSCALE_DEVICES_URL = "https://api.tailscale.com/api/v2/tailnet/-/devices"
_ONLINE_WINDOW = timedelta(minutes=5)


class TailscaleSensor(BaseSensor):
    """Polls Tailscale API and reports per-device online/offline status.

    A device is considered online if its ``lastSeen`` timestamp is within
    5 minutes of UTC now.
    """

    sensor_name = "tailscale_presence"
    min_interval_seconds = 120.0

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis: Redis,
        api_key: str,
    ) -> None:
        super().__init__(session_factory=session_factory, redis=redis)
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=10.0)

    async def collect(self) -> dict[str, Any]:
        """Fetch device list from Tailscale and classify each device.

        Returns a dict with ``online_count``, ``total_count``, and a
        ``devices`` list with per-device status entries.
        """
        response = await self._http.get(
            _TAILSCALE_DEVICES_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        data = response.json()

        now = datetime.now(UTC)
        cutoff = now - _ONLINE_WINDOW

        devices_out: list[dict[str, Any]] = []
        online_count = 0

        for device in data.get("devices", []):
            last_seen_str: str = device.get("lastSeen", "")
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                is_online = last_seen_dt >= cutoff
            except (ValueError, AttributeError):
                is_online = False

            ip_addresses: list[str] = device.get("ipAddresses", [])
            ip = ip_addresses[0] if ip_addresses else ""

            devices_out.append(
                {
                    "hostname": device.get("hostname", ""),
                    "ip": ip,
                    "os": device.get("os", ""),
                    "last_seen": last_seen_str,
                    "online": is_online,
                }
            )
            if is_online:
                online_count += 1

        return {
            "online_count": online_count,
            "total_count": len(devices_out),
            "devices": devices_out,
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


__all__ = ["TailscaleSensor"]
