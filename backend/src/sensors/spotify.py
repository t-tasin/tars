"""SpotifySensor — polls Spotify every 30 s for now-playing state.

Uses spotipy (sync) wrapped in ``asyncio.to_thread`` to avoid blocking the
event loop. Fail-soft on missing credentials, no playback, or API error.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from sensors.base import BaseSensor, SessionFactory

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger()


class SpotifySensor(BaseSensor):
    """Sensor that reads the currently-playing Spotify track.

    Constructor args (keyword-only)
    --------------------------------
    client_id, client_secret, refresh_token
        Spotify OAuth credentials.  If any are empty/falsy the sensor
        operates in no-credentials mode and always returns
        ``{"playing": False, "reason": "no_credentials"}``.
    """

    sensor_name = "spotify"
    min_interval_seconds = 30.0

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        redis: Redis,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        super().__init__(session_factory=session_factory, redis=redis)

        if not client_id or not client_secret or not refresh_token:
            self._spotify = None
            log.info("spotify_sensor_no_credentials")
            return

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth

            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri="http://localhost:8888/callback",
                scope="user-read-playback-state",
                open_browser=False,
            )
            # Inject the stored refresh token so the manager can exchange it
            # for an access token without a browser round-trip.
            auth_manager.cache_handler.save_token_to_cache(
                {
                    "access_token": "",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": refresh_token,
                    "expires_at": 0,  # expired → forces immediate refresh
                    "scope": "user-read-playback-state",
                }
            )
            self._spotify: spotipy.Spotify | None = spotipy.Spotify(auth_manager=auth_manager)
        except Exception as exc:  # noqa: BLE001
            log.warning("spotify_sensor_init_failed", error=str(exc))
            self._spotify = None

    # ------------------------------------------------------------------
    # BaseSensor interface
    # ------------------------------------------------------------------

    async def collect(self) -> dict[str, Any]:
        """Return now-playing state from Spotify.

        Returns a dict with ``playing: True`` and track metadata when
        something is playing, or ``playing: False`` with a ``reason``
        key otherwise. Never raises — errors are returned as a dict.
        """
        if self._spotify is None:
            return {"playing": False, "reason": "no_credentials"}

        try:
            result: dict[str, Any] | None = await asyncio.to_thread(self._spotify.current_playback)
        except Exception as exc:  # noqa: BLE001
            log.warning("spotify_collect_error", error=str(exc))
            return {"playing": False, "reason": "error", "error": str(exc)}

        if result is None or not result.get("is_playing"):
            return {"playing": False, "reason": "idle"}

        item = result["item"]
        return {
            "playing": True,
            "track": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "progress_ms": result["progress_ms"],
            "duration_ms": item["duration_ms"],
            "uri": item["uri"],
        }


__all__ = ["SpotifySensor"]
