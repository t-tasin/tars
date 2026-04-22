"""Tests for the WebSocket manager and endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.websocket import WebSocketManager, _build_payload


class TestWebSocketManager:
    """Unit tests for WebSocketManager."""

    def setup_method(self) -> None:
        self.manager = WebSocketManager()

    @pytest.mark.asyncio
    async def test_connect_registers_websocket(self) -> None:
        ws = AsyncMock()
        await self.manager.connect(ws, "ios-device-1")

        assert ws in self.manager.active_connections
        assert self.manager.active_connections[ws] == "ios-device-1"
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_removes_websocket(self) -> None:
        ws = AsyncMock()
        await self.manager.connect(ws, "ios")
        await self.manager.disconnect(ws)

        assert ws not in self.manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_unknown_ws_no_error(self) -> None:
        ws = AsyncMock()
        await self.manager.disconnect(ws)  # should not raise

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self) -> None:
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.manager.connect(ws1, "ios")
        await self.manager.connect(ws2, "watch")

        await self.manager.broadcast("notification", {"title": "Test"})

        assert ws1.send_json.await_count == 1
        assert ws2.send_json.await_count == 1

        payload = ws1.send_json.call_args[0][0]
        assert payload["type"] == "notification"
        assert payload["data"]["title"] == "Test"
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_broadcast_removes_stale_connections(self) -> None:
        ws_good = AsyncMock()
        ws_stale = AsyncMock()
        ws_stale.send_json.side_effect = RuntimeError("connection closed")

        await self.manager.connect(ws_good, "ios")
        await self.manager.connect(ws_stale, "watch")

        await self.manager.broadcast("message", {"text": "hello"})

        assert ws_good in self.manager.active_connections
        assert ws_stale not in self.manager.active_connections

    @pytest.mark.asyncio
    async def test_send_personal(self) -> None:
        ws = AsyncMock()
        await self.manager.connect(ws, "ios")

        await self.manager.send_personal(ws, "pong", {})

        payload = ws.send_json.call_args_list[-1][0][0]
        assert payload["type"] == "pong"

    def test_connection_count(self) -> None:
        assert self.manager.connection_count == 0


class TestBuildPayload:
    """Tests for the message envelope builder."""

    def test_payload_structure(self) -> None:
        payload = _build_payload("agent_status", {"agent": "briefing", "status": "running"})

        assert payload["type"] == "agent_status"
        assert payload["data"]["agent"] == "briefing"
        assert "timestamp" in payload


class TestWebSocketEndpointAuth:
    """Tests for auth verification on the WebSocket endpoint."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_auth(self) -> None:
        from src.api.websocket import websocket_endpoint

        ws = AsyncMock()
        ws.query_params = {"token": "bad-key", "device": "ios"}

        with patch("src.api.websocket.verify_ws_auth", return_value=False):
            await websocket_endpoint(ws)

        ws.close.assert_awaited_once_with(code=1008)

    @pytest.mark.asyncio
    async def test_accepts_valid_auth_and_handles_disconnect(self) -> None:
        from src.api.websocket import websocket_endpoint, ws_manager

        ws = AsyncMock()
        ws.query_params = {"token": "good-key", "device": "ios-1"}
        ws.receive_json.side_effect = [
            {"type": "ping"},
            Exception("WebSocketDisconnect"),
        ]

        # Reset manager state
        ws_manager.active_connections.clear()

        with patch("src.api.websocket.verify_ws_auth", return_value=True):
            await websocket_endpoint(ws)

        # After disconnect, should be cleaned up
        assert ws not in ws_manager.active_connections
