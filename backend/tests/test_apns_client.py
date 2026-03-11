"""Tests for APNs client and device token registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.apns_client import APNsClient, CATEGORY_APPROVAL


# ---------------------------------------------------------------------------
# APNsClient unit tests
# ---------------------------------------------------------------------------

class TestAPNsClient:
    """Tests for the APNsClient wrapper."""

    def test_init_without_key_file(self, tmp_path):
        """Client initializes but is unavailable when key file doesn't exist."""
        client = APNsClient(
            key_path=str(tmp_path / "nonexistent.p8"),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )
        assert not client.is_available

    @patch("integrations.apns_client.APNs")
    def test_init_with_valid_key(self, mock_apns_cls, tmp_path):
        """Client initializes and is available when key file exists."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----")

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )
        assert client.is_available
        mock_apns_cls.assert_called_once_with(
            key=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            topic="com.tasin.tars",
            use_sandbox=False,
        )

    @patch("integrations.apns_client.APNs")
    async def test_send_success(self, mock_apns_cls, tmp_path):
        """Successful send returns True."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("fake-key")

        mock_result = MagicMock()
        mock_result.is_successful = True
        mock_apns_instance = MagicMock()
        mock_apns_instance.send_notification = AsyncMock(return_value=mock_result)
        mock_apns_cls.return_value = mock_apns_instance

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )

        result = await client.send(
            device_token="abc123def456",
            title="Test",
            body="Test body",
        )
        assert result is True

    @patch("integrations.apns_client.APNs")
    async def test_send_failure(self, mock_apns_cls, tmp_path):
        """Failed send returns False."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("fake-key")

        mock_result = MagicMock()
        mock_result.is_successful = False
        mock_result.description = "BadDeviceToken"
        mock_result.status = "400"
        mock_apns_instance = MagicMock()
        mock_apns_instance.send_notification = AsyncMock(return_value=mock_result)
        mock_apns_cls.return_value = mock_apns_instance

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )

        result = await client.send(
            device_token="bad_token",
            title="Test",
            body="Test body",
        )
        assert result is False

    async def test_send_no_client(self):
        """Send returns False when client is not available."""
        client = APNsClient(
            key_path="/nonexistent/path.p8",
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )
        result = await client.send(
            device_token="abc123",
            title="Test",
            body="Test body",
        )
        assert result is False

    @patch("integrations.apns_client.APNs")
    async def test_send_to_all(self, mock_apns_cls, tmp_path):
        """send_to_all returns count of successful sends."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("fake-key")

        mock_result = MagicMock()
        mock_result.is_successful = True
        mock_apns_instance = MagicMock()
        mock_apns_instance.send_notification = AsyncMock(return_value=mock_result)
        mock_apns_cls.return_value = mock_apns_instance

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )

        count = await client.send_to_all(
            device_tokens=["token1", "token2", "token3"],
            title="Broadcast",
            body="Test broadcast",
            category=CATEGORY_APPROVAL,
            custom_data={"approval_id": "test-uuid"},
        )
        assert count == 3

    async def test_send_to_all_empty_tokens(self):
        """send_to_all with empty list returns 0."""
        client = APNsClient(
            key_path="/nonexistent/path.p8",
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )
        count = await client.send_to_all(
            device_tokens=[],
            title="Test",
            body="Test body",
        )
        assert count == 0

    @patch("integrations.apns_client.APNs")
    async def test_send_with_approval_category(self, mock_apns_cls, tmp_path):
        """Send with APPROVAL category passes correct payload."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("fake-key")

        mock_result = MagicMock()
        mock_result.is_successful = True
        mock_apns_instance = MagicMock()
        mock_apns_instance.send_notification = AsyncMock(return_value=mock_result)
        mock_apns_cls.return_value = mock_apns_instance

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )

        result = await client.send(
            device_token="abc123",
            title="Approval: Send email",
            body="send_email — tap to review",
            category=CATEGORY_APPROVAL,
            custom_data={"approval_id": "uuid-123"},
            priority=10,
        )
        assert result is True

        # Verify send_notification was called with a NotificationRequest
        call_args = mock_apns_instance.send_notification.call_args
        assert call_args is not None
        request = call_args[0][0]
        assert request.device_token == "abc123"
        assert request.message["aps"]["category"] == CATEGORY_APPROVAL
        assert request.message["aps"]["alert"]["title"] == "Approval: Send email"
        assert request.message["approval_id"] == "uuid-123"

    @patch("integrations.apns_client.APNs")
    async def test_send_exception_returns_false(self, mock_apns_cls, tmp_path):
        """If sending raises an exception, return False gracefully."""
        key_file = tmp_path / "apns-key.p8"
        key_file.write_text("fake-key")

        mock_apns_instance = MagicMock()
        mock_apns_instance.send_notification = AsyncMock(side_effect=ConnectionError("APNs unreachable"))
        mock_apns_cls.return_value = mock_apns_instance

        client = APNsClient(
            key_path=str(key_file),
            key_id="KEYID123",
            team_id="TEAM123",
            bundle_id="com.tasin.tars",
        )

        result = await client.send(
            device_token="abc123",
            title="Test",
            body="Test body",
        )
        assert result is False
