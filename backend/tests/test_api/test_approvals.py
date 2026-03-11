"""Tests for the /api/v1/approvals endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.approvals import router
from shared.constants import ApprovalStatus, RiskTier


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


_HEADERS = {"Authorization": "Bearer test-api-key"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_approval_dict(
    *,
    approval_id: str | None = None,
    action_type: str = "send_email",
    status: str = "pending",
    risk_tier: str = "tier2_approval",
    title: str = "Send email to Prof. Smith",
) -> dict[str, Any]:
    aid = approval_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    return {
        "id": aid,
        "task_id": None,
        "action_type": action_type,
        "risk_tier": risk_tier,
        "status": status,
        "title": title,
        "preview_payload": {"to": "smith@example.com", "subject": "Hello"},
        "edited_payload": None,
        "decision_source": None,
        "decided_at": None,
        "executed_at": None,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "created_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Tests — list approvals
# ---------------------------------------------------------------------------


class TestListApprovals:
    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_list_pending_default(self, mock_mgr, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approvals = [_make_approval_dict(), _make_approval_dict()]
        mock_mgr.get_pending = AsyncMock(return_value=(approvals, 2))

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/approvals", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["pending_count"] == 2
        assert len(data["approvals"]) == 2

    @patch("src.api.auth.get_settings")
    def test_non_pending_status_returns_empty(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)
        response = client.get(
            "/api/v1/approvals",
            params={"status": "approved"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["approvals"] == []

    @patch("src.api.auth.get_settings")
    def test_missing_auth_returns_401(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/approvals")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests — approve
# ---------------------------------------------------------------------------


class TestApproveAction:
    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_approve_success(self, mock_mgr, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        result = _make_approval_dict(
            approval_id=str(approval_id), status="approved",
        )
        mock_mgr.approve = AsyncMock(return_value=result)

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_approve_not_found_returns_404(self, mock_mgr, mock_settings):
        from src.orchestrator.approval_manager import ApprovalNotFoundError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.approve = AsyncMock(side_effect=ApprovalNotFoundError("not found"))

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "approval_not_found"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_approve_expired_returns_410(self, mock_mgr, mock_settings):
        from src.orchestrator.approval_manager import ApprovalExpiredError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.approve = AsyncMock(side_effect=ApprovalExpiredError("expired"))

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 410
        assert response.json()["detail"]["error"]["code"] == "approval_expired"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_approve_already_decided_returns_409(self, mock_mgr, mock_settings):
        from src.orchestrator.approval_manager import ApprovalAlreadyDecidedError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.approve = AsyncMock(
            side_effect=ApprovalAlreadyDecidedError("already decided"),
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


# ---------------------------------------------------------------------------
# Tests — reject
# ---------------------------------------------------------------------------


class TestRejectAction:
    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_reject_success(self, mock_mgr, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        result = _make_approval_dict(
            approval_id=str(approval_id), status="rejected",
        )
        mock_mgr.reject = AsyncMock(return_value=result)

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            json={"source": "ios", "reason": "Not now"},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_reject_not_found_returns_404(self, mock_mgr, mock_settings):
        from src.orchestrator.approval_manager import ApprovalNotFoundError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.reject = AsyncMock(side_effect=ApprovalNotFoundError("not found"))

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            json={"source": "ios"},
            headers=_HEADERS,
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests — edit and approve
# ---------------------------------------------------------------------------


class TestEditAndApproveAction:
    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_edit_and_approve_success(self, mock_mgr, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        result = _make_approval_dict(
            approval_id=str(approval_id), status="edited",
        )
        mock_mgr.edit_and_approve = AsyncMock(return_value=result)

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/edit",
            json={
                "source": "ios",
                "edited_payload": {"subject": "Updated subject"},
            },
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "edited"

    @patch("src.api.auth.get_settings")
    @patch("src.api.approvals._approval_manager")
    def test_edit_expired_returns_410(self, mock_mgr, mock_settings):
        from src.orchestrator.approval_manager import ApprovalExpiredError

        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        approval_id = uuid.uuid4()
        mock_mgr.edit_and_approve = AsyncMock(
            side_effect=ApprovalExpiredError("expired"),
        )

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            f"/api/v1/approvals/{approval_id}/edit",
            json={
                "source": "ios",
                "edited_payload": {"subject": "Updated"},
            },
            headers=_HEADERS,
        )

        assert response.status_code == 410
