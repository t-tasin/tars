"""End-to-end test for the approval flow.

Verifies the full lifecycle: create approval → list pending → approve/reject/edit,
with all external dependencies mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.approvals import router as approvals_router
from src.api.messages import router as messages_router

# ---------------------------------------------------------------------------
# App setup — combine messages + approvals routers
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(messages_router)
    app.include_router(approvals_router)
    return app


_HEADERS = {"Authorization": "Bearer test-api-key"}


def _make_approval_dict(
    *,
    approval_id: str | None = None,
    action_type: str = "send_email",
    status: str = "pending",
) -> dict[str, Any]:
    aid = approval_id or str(uuid.uuid4())
    now = datetime.now(UTC)
    return {
        "id": aid,
        "task_id": None,
        "action_type": action_type,
        "risk_tier": "tier2_approval",
        "status": status,
        "title": "Send email to Prof. Smith",
        "preview_payload": {"to": "smith@example.com", "subject": "Hello"},
        "edited_payload": None,
        "decision_source": None,
        "decided_at": None,
        "executed_at": None,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "created_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# E2E: approval lifecycle
# ---------------------------------------------------------------------------


class TestE2EApprovalFlow:
    """End-to-end test covering the create → list → decide lifecycle."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_create_list_approve_lifecycle(self, mock_mgr, mock_settings):
        """Simulate: message creates approval → list pending → approve it."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = str(uuid.uuid4())
        pending_approval = _make_approval_dict(approval_id=approval_id)

        # Step 1: list pending — shows the approval
        mock_mgr.get_pending = AsyncMock(return_value=([pending_approval], 1))

        app = _build_app()
        client = TestClient(app)

        list_response = client.get("/api/v1/approvals", headers=_HEADERS)
        assert list_response.status_code == 200
        data = list_response.json()
        assert data["pending_count"] == 1
        assert data["approvals"][0]["action_type"] == "send_email"
        listed_id = data["approvals"][0]["id"]

        # Step 2: approve it
        approved_dict = _make_approval_dict(
            approval_id=approval_id,
            status="approved",
        )
        mock_mgr.approve = AsyncMock(return_value=approved_dict)

        approve_response = client.post(
            f"/api/v1/approvals/{listed_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["status"] == "approved"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_create_list_reject_lifecycle(self, mock_mgr, mock_settings):
        """Simulate: list pending → reject it with a reason."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = str(uuid.uuid4())

        # Step 1: list pending
        pending_approval = _make_approval_dict(approval_id=approval_id)
        mock_mgr.get_pending = AsyncMock(return_value=([pending_approval], 1))

        app = _build_app()
        client = TestClient(app)

        list_response = client.get("/api/v1/approvals", headers=_HEADERS)
        assert list_response.status_code == 200
        listed_id = list_response.json()["approvals"][0]["id"]

        # Step 2: reject it
        rejected_dict = _make_approval_dict(
            approval_id=approval_id,
            status="rejected",
        )
        mock_mgr.reject = AsyncMock(return_value=rejected_dict)

        reject_response = client.post(
            f"/api/v1/approvals/{listed_id}/reject",
            json={"source": "ios", "reason": "Not now"},
            headers=_HEADERS,
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["status"] == "rejected"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_edit_and_approve_lifecycle(self, mock_mgr, mock_settings):
        """Simulate: list pending → edit payload → approve."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = str(uuid.uuid4())

        pending_approval = _make_approval_dict(approval_id=approval_id)
        mock_mgr.get_pending = AsyncMock(return_value=([pending_approval], 1))

        app = _build_app()
        client = TestClient(app)

        list_response = client.get("/api/v1/approvals", headers=_HEADERS)
        assert list_response.status_code == 200
        listed_id = list_response.json()["approvals"][0]["id"]

        # Edit and approve
        edited_dict = _make_approval_dict(
            approval_id=approval_id,
            status="edited",
        )
        mock_mgr.edit_and_approve = AsyncMock(return_value=edited_dict)

        edit_response = client.post(
            f"/api/v1/approvals/{listed_id}/edit",
            json={
                "source": "ios",
                "edited_payload": {"subject": "Updated subject line"},
            },
            headers=_HEADERS,
        )
        assert edit_response.status_code == 200
        assert edit_response.json()["status"] == "edited"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_double_approve_returns_409(self, mock_mgr, mock_settings):
        """Approving an already-approved action should return 409."""
        from src.orchestrator.approval_manager import ApprovalAlreadyDecidedError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.approve = AsyncMock(
            side_effect=ApprovalAlreadyDecidedError("already approved"),
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "approval_already_decided"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_expired_approval_returns_410(self, mock_mgr, mock_settings):
        """Trying to act on an expired approval should return 410."""
        from src.orchestrator.approval_manager import ApprovalExpiredError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.approve = AsyncMock(
            side_effect=ApprovalExpiredError("expired"),
        )

        app = _build_app()
        client = TestClient(app)

        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )
        assert response.status_code == 410

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_tier3_escalation_visible_in_list(self, mock_mgr, mock_settings):
        """Tier 3 escalations should appear in the pending approvals list."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        tier3_approval = _make_approval_dict(
            action_type="email_professor",
            status="pending",
        )
        tier3_approval["risk_tier"] = "tier3_escalation"

        mock_mgr.get_pending = AsyncMock(return_value=([tier3_approval], 1))

        app = _build_app()
        client = TestClient(app)

        response = client.get("/api/v1/approvals", headers=_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["approvals"][0]["risk_tier"] == "tier3_escalation"
        assert data["approvals"][0]["action_type"] == "email_professor"
