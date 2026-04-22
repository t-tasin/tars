"""Apple Push Notification Service client for T.A.R.S.

Wraps the ``aioapns`` library to send push notifications to iOS devices
and Apple Watch.  Supports actionable notification categories (APPROVAL)
with approve/reject buttons.

``aioapns`` is async-native (uses HTTP/2 via ``h2``), so all send
operations integrate naturally with the asyncio event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from aioapns import APNs, NotificationRequest

log = structlog.get_logger()

# APNs category identifiers — must match the iOS UNNotificationCategory ids.
CATEGORY_APPROVAL = "APPROVAL"
CATEGORY_DEFAULT = "DEFAULT"
CATEGORY_WORKOUT_REMINDER = "WORKOUT_REMINDER"


class APNsClient:
    """Apple Push Notification Service client.

    Uses token-based authentication (.p8 key) and sends via HTTP/2 to APNs.
    All public methods are async-safe.
    """

    def __init__(
        self,
        key_path: str,
        key_id: str,
        team_id: str,
        bundle_id: str,
        *,
        use_sandbox: bool = False,
    ) -> None:
        self._bundle_id = bundle_id
        self._use_sandbox = use_sandbox
        self._apns: APNs | None = None

        key_file = Path(key_path)
        if not key_file.exists():
            log.warning("apns_key_not_found", path=key_path)
            return

        self._apns = APNs(
            key=key_path,
            key_id=key_id,
            team_id=team_id,
            topic=bundle_id,
            use_sandbox=use_sandbox,
        )
        log.info(
            "apns_client_initialized",
            bundle_id=bundle_id,
            sandbox=use_sandbox,
        )

    @property
    def is_available(self) -> bool:
        return self._apns is not None

    async def send(
        self,
        device_token: str,
        title: str,
        body: str,
        category: str = CATEGORY_DEFAULT,
        custom_data: dict[str, Any] | None = None,
        priority: int = 10,
    ) -> bool:
        """Send a push notification to a single device.

        Args:
            device_token: Hex-encoded APNs device token.
            title: Notification title.
            body: Notification body text.
            category: UNNotificationCategory identifier (e.g. ``"APPROVAL"``).
            custom_data: Extra key-value pairs added to the notification payload
                         (visible in ``userInfo`` on the iOS side).
            priority: APNs priority — 10 (immediate) or 5 (power-saving).

        Returns:
            ``True`` if APNs accepted the notification, ``False`` otherwise.
        """
        if not self._apns:
            log.debug("apns_send_skipped_no_client")
            return False

        custom = custom_data or {}

        # Build the aps payload dict
        message: dict[str, Any] = {
            "aps": {
                "alert": {"title": title, "body": body},
                "badge": 1,
                "sound": "default",
                "category": category,
                "mutable-content": 1,
            },
            **custom,
        }

        request = NotificationRequest(
            device_token=device_token,
            message=message,
            priority=priority,
        )

        try:
            result = await self._apns.send_notification(request)

            if result.is_successful:
                log.info(
                    "apns_sent",
                    device_token=device_token[:8] + "...",
                    category=category,
                )
                return True

            log.warning(
                "apns_rejected",
                device_token=device_token[:8] + "...",
                reason=result.description,
                status=result.status,
            )
            return False

        except Exception:
            log.exception(
                "apns_send_error",
                device_token=device_token[:8] + "...",
            )
            return False

    async def send_to_all(
        self,
        device_tokens: list[str],
        title: str,
        body: str,
        category: str = CATEGORY_DEFAULT,
        custom_data: dict[str, Any] | None = None,
        priority: int = 10,
    ) -> int:
        """Send a push notification to all registered devices.

        Returns the number of successful deliveries.
        """
        if not device_tokens:
            return 0

        results = await asyncio.gather(
            *(
                self.send(
                    device_token=token,
                    title=title,
                    body=body,
                    category=category,
                    custom_data=custom_data,
                    priority=priority,
                )
                for token in device_tokens
            ),
            return_exceptions=True,
        )

        successes = sum(1 for r in results if r is True)
        log.info(
            "apns_broadcast_complete",
            total=len(device_tokens),
            successes=successes,
            category=category,
        )
        return successes

    async def send_workout_reminder(
        self,
        session_id: str,
        day_name: str,
    ) -> int:
        """Send a workout reminder push to all registered devices.

        Uses the WORKOUT_REMINDER category which provides START_WORKOUT
        and SKIP_WORKOUT action buttons on iOS.

        Returns the number of successful deliveries.
        """
        from sqlalchemy import select

        from db.models import DeviceToken
        from db.session import get_db_session

        async with get_db_session() as session:
            result = await session.execute(
                select(DeviceToken.token).where(DeviceToken.active == True)  # noqa: E712
            )
            tokens = [row[0] for row in result.all()]

        if not tokens:
            log.debug("workout_reminder_no_devices")
            return 0

        return await self.send_to_all(
            device_tokens=tokens,
            title=f"{day_name.title()} Day — Time to Work Out",
            body="Your workout is scheduled now. Ready to start?",
            category=CATEGORY_WORKOUT_REMINDER,
            custom_data={"session_id": session_id, "day_name": day_name},
        )
