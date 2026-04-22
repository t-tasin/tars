"""WebSocket manager for real-time client updates.

Handles persistent connections from iOS app, Apple Watch, and Telegram
for pushing approval requests, agent status updates, briefings, and
notifications without polling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.auth import verify_ws_auth

log = structlog.get_logger()

router = APIRouter()

# WebSocket message types pushed to clients
WSMessageType = Literal[
    "message",
    "approval_request",
    "agent_status",
    "notification",
    "briefing_ready",
    "deploy_status",
    "pong",
]


class WebSocketManager:
    """Manages WebSocket connections for real-time push to clients."""

    def __init__(self) -> None:
        self.active_connections: dict[WebSocket, str] = {}  # ws -> device

    async def connect(self, websocket: WebSocket, device: str) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections[websocket] = device
        log.info(
            "ws_connected",
            device=device,
            total_connections=len(self.active_connections),
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket."""
        device = self.active_connections.pop(websocket, "unknown")
        log.info(
            "ws_disconnected",
            device=device,
            total_connections=len(self.active_connections),
        )

    async def broadcast(self, message_type: WSMessageType, data: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        payload = _build_payload(message_type, data)
        stale: list[WebSocket] = []

        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)

        for ws in stale:
            await self.disconnect(ws)

        if stale:
            log.info("ws_stale_cleaned", count=len(stale))

    async def send_personal(self, websocket: WebSocket, message_type: WSMessageType, data: dict[str, Any]) -> None:
        """Send a message to a specific client."""
        await websocket.send_json(_build_payload(message_type, data))

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


def _build_payload(message_type: WSMessageType, data: dict[str, Any]) -> dict[str, Any]:
    """Build a standardised WebSocket message envelope."""
    return {
        "type": message_type,
        "data": data,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

ws_manager = WebSocketManager()


def get_ws_manager() -> WebSocketManager:
    """Return the singleton WebSocketManager instance."""
    return ws_manager


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/api/v1/stream")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Persistent WebSocket for real-time push updates.

    Query params:
        token  — API key (same as Authorization Bearer value)
        device — device token identifying the client
    """
    token = websocket.query_params.get("token", "")
    device = websocket.query_params.get("device", "")

    if not verify_ws_auth(token, device):
        await websocket.close(code=1008)
        log.warning("ws_auth_failed", device=device)
        return

    await ws_manager.connect(websocket, device)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await ws_manager.send_personal(websocket, "pong", {})
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        log.exception("ws_unexpected_error", device=device)
        await ws_manager.disconnect(websocket)
