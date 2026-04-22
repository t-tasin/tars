"""Unified notification fan-out for T.A.R.S.

Uses Apprise for broadcast alerts, custom Telegram code for interactive
notifications (inline keyboards), and custom APNs code for actionable push
notifications (approve/reject buttons).  See CLAUDE.md § Notification Service.

Channel routing by severity:
  - critical → Telegram + Email (Apprise) + APNs push (custom) + WebSocket
  - warning  → Telegram (Apprise) + APNs push (custom, informational) + WebSocket
  - info     → Telegram (Apprise) + WebSocket
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

import structlog
from sqlalchemy import select

if TYPE_CHECKING:
    from api.websocket import WebSocketManager
    from integrations.apns_client import APNsClient
    from integrations.telegram_bot import TelegramGateway

log = structlog.get_logger()

NotificationPriority = Literal["critical", "warning", "info"]


class NotificationService:
    """Unified notification fan-out.

    Uses Apprise for broadcast, custom Telegram code for interactive
    notifications, and APNs for rich push.  All agents send notifications
    through this single interface.
    """

    def __init__(
        self,
        telegram_gateway: TelegramGateway,
        ws_manager: WebSocketManager,
        apns_client: APNsClient | None = None,
    ) -> None:
        self.telegram = telegram_gateway
        self.ws = ws_manager
        self.apns = apns_client

    # ------------------------------------------------------------------
    # General notification
    # ------------------------------------------------------------------

    async def notify(
        self,
        title: str,
        body: str,
        *,
        priority: NotificationPriority = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Fan out a notification to all appropriate channels.

        Channel selection follows severity routing:
          - critical → WebSocket + Telegram + APNs
          - warning  → WebSocket + Telegram + APNs (informational)
          - info     → WebSocket + Telegram
        """
        data = data or {}
        tasks: list[Any] = []

        # 1. WebSocket broadcast (all priorities)
        tasks.append(
            self._ws_send(
                "notification",
                {
                    "priority": priority,
                    "title": title,
                    "body": body,
                    **data,
                },
            )
        )

        # 2. Telegram message (all priorities)
        tasks.append(self._telegram_send(title, body, priority))

        # 3. APNs push — critical and warning only
        if self.apns and priority in ("critical", "warning"):
            tasks.append(self._apns_send(title=title, body=body, priority=priority, data=data))

        await asyncio.gather(*tasks, return_exceptions=True)

        log.info(
            "notification_sent",
            title=title,
            priority=priority,
            ws_clients=self.ws.connection_count,
            apns_available=self.apns is not None,
        )

    # ------------------------------------------------------------------
    # Approval notification (interactive)
    # ------------------------------------------------------------------

    async def notify_approval(self, approval: dict[str, Any]) -> None:
        """Send an approval request with interactive buttons.

        - Telegram: inline keyboard with Approve / Reject buttons
        - WebSocket: approval_request message for iOS / web
        - APNs: actionable notification with APPROVAL category (Phase 3)
        """
        approval_id = approval.get("id", "unknown")
        title = approval.get("title", "Action requires approval")
        action_type = approval.get("action_type", "unknown")
        risk_tier = approval.get("risk_tier", "tier2_approval")
        preview = approval.get("preview_payload", {})

        tasks: list[Any] = []

        # 1. WebSocket — full approval payload for rich UI
        tasks.append(
            self._ws_send(
                "approval_request",
                {
                    "approval_id": approval_id,
                    "action_type": action_type,
                    "risk_tier": risk_tier,
                    "title": title,
                    "preview": preview,
                },
            )
        )

        # 2. Telegram — inline keyboard with approve/reject
        tasks.append(self._telegram_send_approval(approval_id, title, action_type, risk_tier, preview))

        # 3. APNs — actionable push with APPROVAL category
        if self.apns:
            tasks.append(
                self._apns_send(
                    title=f"Approval: {title}",
                    body=f"{action_type} — tap to review",
                    priority="critical",
                    data={"approval_id": approval_id, "category": "APPROVAL"},
                )
            )

        await asyncio.gather(*tasks, return_exceptions=True)

        log.info(
            "approval_notification_sent",
            approval_id=approval_id,
            action_type=action_type,
            risk_tier=risk_tier,
        )

    # ------------------------------------------------------------------
    # System alert (high priority)
    # ------------------------------------------------------------------

    async def notify_alert(
        self,
        title: str,
        body: str,
        *,
        severity: NotificationPriority = "warning",
    ) -> None:
        """Send a system alert.  Maps severity to notification priority."""
        await self.notify(
            title=f"[ALERT] {title}",
            body=body,
            priority=severity,
            data={"type": "system_alert"},
        )

    # ------------------------------------------------------------------
    # Budget alert (HC-12)
    # ------------------------------------------------------------------

    async def notify_budget_alert(self, alert: dict[str, Any]) -> None:
        """Send a budget warning when AI usage approaches limits."""
        model = alert.get("model", "unknown")
        usage = alert.get("current", 0)
        limit = alert.get("limit", 0)
        period = alert.get("period", "unknown")

        await self.notify(
            title="AI Budget Alert",
            body=f"{model} usage: {usage}/{limit} ({period})",
            priority="warning",
            data={"type": "budget_alert", **alert},
        )

    # ------------------------------------------------------------------
    # Channel implementations
    # ------------------------------------------------------------------

    async def _ws_send(self, message_type: str, data: dict[str, Any]) -> None:
        """Broadcast via the existing WebSocketManager from api.websocket."""
        try:
            await self.ws.broadcast(message_type, data)  # type: ignore[arg-type]
        except Exception:
            log.exception("ws_broadcast_failed")

    async def _telegram_send(
        self,
        title: str,
        body: str,
        priority: NotificationPriority,
    ) -> None:
        """Send a formatted Telegram message."""
        try:
            prefix = {
                "critical": "\U0001f6a8",  # 🚨
                "warning": "\u26a0\ufe0f",  # ⚠️
                "info": "\u2139\ufe0f",  # ℹ️
            }.get(priority, "")

            text = f"{prefix} <b>{title}</b>\n\n{body}"
            await self.telegram.send_message(text=text, parse_mode="HTML")
        except Exception:
            log.exception("telegram_notification_failed", title=title)

    async def _telegram_send_approval(
        self,
        approval_id: str,
        title: str,
        action_type: str,
        risk_tier: str,
        preview: dict[str, Any],
    ) -> None:
        """Send a Telegram message with inline approve/reject keyboard."""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            # Format the preview into readable text
            preview_lines = []
            for key, value in preview.items():
                display = str(value)
                if len(display) > 200:
                    display = display[:200] + "..."
                preview_lines.append(f"  <b>{key}</b>: {display}")
            preview_text = "\n".join(preview_lines) if preview_lines else "  (no preview)"

            tier_label = {
                "tier2_approval": "Tier 2 \u2014 Approval Required",
                "tier3_escalation": "Tier 3 \u2014 Escalation",
            }.get(risk_tier, risk_tier)

            text = (
                f"\U0001f7e1 <b>Approval Required</b>\n\n"
                f"<b>{title}</b>\n"
                f"Action: <code>{action_type}</code>\n"
                f"Risk: {tier_label}\n\n"
                f"<b>Preview:</b>\n{preview_text}"
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\u2705 Approve",
                            callback_data=f"approve:{approval_id}",
                        ),
                        InlineKeyboardButton(
                            "\u274c Reject",
                            callback_data=f"reject:{approval_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "\u270f\ufe0f Edit & Approve",
                            callback_data=f"edit_approve:{approval_id}",
                        ),
                    ],
                ]
            )

            await self.telegram.send_message(text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            log.exception("telegram_approval_failed", approval_id=approval_id)

    async def _apns_send(
        self,
        *,
        title: str,
        body: str,
        priority: str,
        data: dict[str, Any],
    ) -> None:
        """Send push notification via APNs to all registered devices."""
        if not self.apns or not self.apns.is_available:
            log.debug("apns_skipped_no_client", title=title)
            return

        try:
            tokens = await self._get_active_device_tokens()
            if not tokens:
                log.debug("apns_skipped_no_tokens", title=title)
                return

            category = data.get("category", "DEFAULT")
            custom_data = {k: v for k, v in data.items() if k != "category"}
            apns_priority = 10 if priority == "critical" else 5

            await self.apns.send_to_all(
                device_tokens=tokens,
                title=title,
                body=body,
                category=category,
                custom_data=custom_data,
                priority=apns_priority,
            )
        except Exception:
            log.exception("apns_send_failed", title=title)

    async def _get_active_device_tokens(self) -> list[str]:
        """Fetch all active APNs device tokens from the database."""
        try:
            from db.models import DeviceToken
            from db.session import async_session_factory

            async with async_session_factory() as session:
                result = await session.execute(select(DeviceToken.token).where(DeviceToken.active.is_(True)))
                return list(result.scalars().all())
        except Exception:
            log.exception("apns_token_lookup_failed")
            return []


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_notification_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Return the singleton NotificationService.

    Requires that ``init_notification_service()`` was called during startup.
    """
    if _notification_service is None:
        raise RuntimeError("NotificationService not initialised \u2014 call init_notification_service() at startup")
    return _notification_service


def init_notification_service(
    telegram_gateway: TelegramGateway,
    apns_client: APNsClient | None = None,
) -> NotificationService:
    """Create and store the singleton NotificationService.

    Called once during application startup (``lifespan``).  Uses the
    existing ``WebSocketManager`` singleton from ``api.websocket``.
    """
    global _notification_service

    from api.websocket import get_ws_manager

    _notification_service = NotificationService(
        telegram_gateway=telegram_gateway,
        ws_manager=get_ws_manager(),
        apns_client=apns_client,
    )
    log.info("notification_service_initialized", apns_available=apns_client is not None)
    return _notification_service
