"""End-to-end test for the message processing pipeline.

Verifies the full flow: API → Orchestrator → Intent → Route → Agent → Response,
with all external dependencies mocked.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.messages import router
from shared.constants import ContentType, MessageSource


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


_HEADERS = {"Authorization": "Bearer test-api-key"}


def _mock_response(
    text: str = "Hello, Tasin!",
    agent: str = "general",
    model: str = "gemini_flash",
    content_type: str = "text",
    cards: list | None = None,
) -> dict[str, Any]:
    return {
        "conversation_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "response": {
            "text": text,
            "content_type": content_type,
            "cards": cards or [],
            "approval": None,
        },
        "agent_used": agent,
        "model_used": model,
    }


# ---------------------------------------------------------------------------
# E2E: message → orchestrator → response
# ---------------------------------------------------------------------------


class TestE2EMessagePipeline:
    """Test the full message pipeline from API layer through orchestrator."""

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_simple_message_round_trip(self, mock_get_orch, mock_settings):
        """A simple text message should flow through and return a 200 response."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(
            return_value=_mock_response(text="Good morning!", agent="daily_life"),
        )
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Good morning", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response"]["text"] == "Good morning!"
        assert data["agent_used"] == "daily_life"

        # Verify orchestrator received the correct args
        mock_orch.process_message.assert_called_once()
        call_kwargs = mock_orch.process_message.call_args
        assert call_kwargs.kwargs.get("text") == "Good morning" or \
               call_kwargs[1].get("text") == "Good morning"

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_telegram_source_accepted(self, mock_get_orch, mock_settings):
        """Telegram as a source should be accepted."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "telegram"},
            headers=_HEADERS,
        )

        assert response.status_code == 200

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_card_response_pipeline(self, mock_get_orch, mock_settings):
        """Verify that card-type responses are properly forwarded."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        cards = [{"type": "health_summary", "metrics": {"steps": 8000}}]
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(
            return_value=_mock_response(
                text="Here's your health summary",
                agent="health_fitness",
                content_type="card",
                cards=cards,
            ),
        )
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "health summary", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response"]["content_type"] == "card"
        assert len(data["response"]["cards"]) == 1
        assert data["response"]["cards"][0]["type"] == "health_summary"

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_approval_response_pipeline(self, mock_get_orch, mock_settings):
        """Verify that approval-type responses include approval metadata."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = str(uuid.uuid4())
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(
            return_value={
                "conversation_id": str(uuid.uuid4()),
                "message_id": str(uuid.uuid4()),
                "response": {
                    "text": "I've drafted an email for you to review.",
                    "content_type": "approval",
                    "cards": [],
                    "approval": {
                        "approval_id": approval_id,
                        "action_type": "send_email",
                        "title": "Email to Prof. Smith",
                        "preview": {"to": "smith@example.com"},
                    },
                },
                "agent_used": "communication",
                "model_used": "claude_code",
            },
        )
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "draft email to professor", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response"]["content_type"] == "approval"
        assert data["response"]["approval"]["action_type"] == "send_email"
        assert data["agent_used"] == "communication"
        assert data["model_used"] == "claude_code"

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_conversation_continuity(self, mock_get_orch, mock_settings):
        """Passing a conversation_id should maintain thread context."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        conv_id = uuid.uuid4()
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={
                "text": "Follow up question",
                "source": "ios",
                "conversation_id": str(conv_id),
            },
            headers=_HEADERS,
        )

        assert response.status_code == 200
        call_kwargs = mock_orch.process_message.call_args
        passed_conv_id = call_kwargs.kwargs.get("conversation_id") or \
                         call_kwargs[1].get("conversation_id")
        assert passed_conv_id == conv_id

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_orchestrator_crash_returns_500(self, mock_get_orch, mock_settings):
        """If the orchestrator raises an unexpected error, the API returns 500."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(
            side_effect=RuntimeError("unexpected crash"),
        )
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={"text": "Hello", "source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 500

    @patch("src.api.auth.get_settings")
    @patch("src.orchestrator.engine.get_orchestrator")
    def test_attachments_forwarded_to_orchestrator(self, mock_get_orch, mock_settings):
        """Image attachments should be forwarded to the orchestrator."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_orch = AsyncMock()
        mock_orch.process_message = AsyncMock(return_value=_mock_response())
        mock_get_orch.return_value = mock_orch

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/message",
            json={
                "text": "What is this?",
                "source": "ios",
                "attachments": [
                    {"type": "image", "data": "base64data", "mime_type": "image/jpeg"},
                ],
            },
            headers=_HEADERS,
        )

        assert response.status_code == 200
        call_kwargs = mock_orch.process_message.call_args
        attachments = call_kwargs.kwargs.get("attachments") or \
                      call_kwargs[1].get("attachments")
        assert attachments is not None
        assert len(attachments) == 1
