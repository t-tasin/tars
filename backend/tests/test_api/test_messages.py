"""Tests for the /api/v1/message endpoint."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.constants import ContentType

from src.api.messages import router

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


_HEADERS = {
    "Authorization": "Bearer test-api-key",
}


# ---------------------------------------------------------------------------
# Mock orchestrator response
# ---------------------------------------------------------------------------


def _mock_orchestrator_response(
    text: str = "Hello, Tasin!",
    agent: str = "general",
    model: str = "gemini_flash",
) -> dict[str, Any]:
    return {
        "conversation_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "response": {
            "text": text,
            "content_type": ContentType.TEXT,
            "cards": [],
            "approval": None,
        },
        "agent_used": agent,
        "model_used": model,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendMessage:
    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_successful_message(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_orchestrator_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert "conversation_id" in data
        assert "message_id" in data
        assert data["response"]["text"] == "Hello, Tasin!"
        assert data["agent_used"] == "general"
        assert data["model_used"] == "gemini_flash"

    @patch("src.api.auth.get_settings")
    def test_missing_auth_returns_401(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
        )

        assert response.status_code == 401

    @patch("src.api.auth.get_settings")
    def test_invalid_api_key_returns_401(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="real-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert response.status_code == 401

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_orchestrator_error_returns_500(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(side_effect=RuntimeError("boom"))
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error"]["code"] == "internal_error"

    @patch("src.api.auth.get_settings")
    def test_invalid_source_returns_422(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "invalid_source"},
            headers=_HEADERS,
        )

        assert response.status_code == 422

    @patch("src.api.auth.get_settings")
    def test_missing_text_returns_422(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 422

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_with_conversation_id(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        conv_id = uuid.uuid4()
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_orchestrator_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={
                "text": "Follow up",
                "source": "telegram",
                "conversation_id": str(conv_id),
            },
            headers=_HEADERS,
        )

        assert response.status_code == 200
        mock_orch.process_message.assert_called_once()
        call_kwargs = mock_orch.process_message.call_args
        assert call_kwargs.kwargs.get("conversation_id") == conv_id or call_kwargs[1].get("conversation_id") == conv_id

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_with_attachments(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_orchestrator_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={
                "text": "Catalog this",
                "source": "ios",
                "attachments": [
                    {"type": "image", "data": "base64data", "mime_type": "image/jpeg"},
                ],
            },
            headers=_HEADERS,
        )

        assert response.status_code == 200
        mock_orch.process_message.assert_called_once()


class TestDeviceTokenAuth:
    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_device_token_required_when_configured(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="device-1,device-2",
        )

        app = _build_app()
        client = TestClient(app)

        # Without device token — should fail
        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers=_HEADERS,
        )
        assert response.status_code == 401

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_valid_device_token_accepted(self, mock_get_orch, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="device-1,device-2",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_orchestrator_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers={**_HEADERS, "X-Device-Token": "device-1"},
        )
        assert response.status_code == 200
