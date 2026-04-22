"""Tests for the unified notification service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.notification_service import NotificationService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_telegram() -> AsyncMock:
    gw = AsyncMock()
    gw.send_message = AsyncMock()
    return gw


@pytest.fixture()
def mock_ws() -> MagicMock:
    ws = MagicMock()
    ws.broadcast = AsyncMock()
    ws.connection_count = 2
    return ws


@pytest.fixture()
def mock_apns() -> AsyncMock:
    client = AsyncMock()
    client.is_available = True
    client.send = AsyncMock(return_value=True)
    client.send_to_all = AsyncMock(return_value=1)
    return client


@pytest.fixture()
def service(mock_telegram: AsyncMock, mock_ws: MagicMock) -> NotificationService:
    return NotificationService(
        telegram_gateway=mock_telegram,
        ws_manager=mock_ws,
    )


@pytest.fixture()
def service_with_apns(
    mock_telegram: AsyncMock,
    mock_ws: MagicMock,
    mock_apns: AsyncMock,
) -> NotificationService:
    return NotificationService(
        telegram_gateway=mock_telegram,
        ws_manager=mock_ws,
        apns_client=mock_apns,
    )


# ---------------------------------------------------------------------------
# notify()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_notify_info_sends_ws_and_telegram(
    service: NotificationService,
    mock_ws: MagicMock,
    mock_telegram: AsyncMock,
) -> None:
    await service.notify("Test Title", "Test body", priority="info")

    mock_ws.broadcast.assert_awaited_once()
    call_args = mock_ws.broadcast.call_args
    assert call_args[0][0] == "notification"
    assert call_args[0][1]["title"] == "Test Title"

    mock_telegram.send_message.assert_awaited_once()
    sent_text = mock_telegram.send_message.call_args.kwargs["text"]
    assert "Test Title" in sent_text


@pytest.mark.asyncio()
async def test_notify_critical_sends_apns(
    service_with_apns: NotificationService,
    mock_apns: AsyncMock,
) -> None:
    with patch.object(service_with_apns, "_get_active_device_tokens", return_value=["token1"]):
        await service_with_apns.notify("Alert", "Something bad", priority="critical")

    mock_apns.send_to_all.assert_awaited_once()
    assert mock_apns.send_to_all.call_args.kwargs["priority"] == 10  # critical = 10


@pytest.mark.asyncio()
async def test_notify_info_skips_apns(
    service_with_apns: NotificationService,
    mock_apns: AsyncMock,
) -> None:
    await service_with_apns.notify("Hello", "World", priority="info")

    mock_apns.send.assert_not_awaited()


@pytest.mark.asyncio()
async def test_notify_no_apns_client(
    service: NotificationService,
) -> None:
    """Should not raise when APNs client is None."""
    await service.notify("Title", "Body", priority="critical")


# ---------------------------------------------------------------------------
# notify_approval()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_notify_approval_sends_ws_and_telegram(
    service: NotificationService,
    mock_ws: MagicMock,
    mock_telegram: AsyncMock,
) -> None:
    approval = {
        "id": "abc-123",
        "title": "Send email to Prof. X",
        "action_type": "send_email",
        "risk_tier": "tier2_approval",
        "preview_payload": {"to": "prof@example.com", "subject": "Hi"},
    }

    await service.notify_approval(approval)

    # WebSocket should receive approval_request type
    ws_call = mock_ws.broadcast.call_args
    assert ws_call[0][0] == "approval_request"
    assert ws_call[0][1]["approval_id"] == "abc-123"

    # Telegram should receive message with inline keyboard
    mock_telegram.send_message.assert_awaited_once()
    call_kwargs = mock_telegram.send_message.call_args.kwargs
    assert "Approval Required" in call_kwargs["text"]
    assert call_kwargs["reply_markup"] is not None


@pytest.mark.asyncio()
async def test_notify_approval_with_apns(
    service_with_apns: NotificationService,
    mock_apns: AsyncMock,
) -> None:
    approval = {
        "id": "xyz-789",
        "title": "Create PR",
        "action_type": "create_pr",
        "risk_tier": "tier2_approval",
        "preview_payload": {},
    }

    with patch.object(service_with_apns, "_get_active_device_tokens", return_value=["token1"]):
        await service_with_apns.notify_approval(approval)

    mock_apns.send_to_all.assert_awaited_once()
    assert mock_apns.send_to_all.call_args.kwargs["category"] == "APPROVAL"


# ---------------------------------------------------------------------------
# notify_alert()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_notify_alert_prefixes_title(
    service: NotificationService,
    mock_ws: MagicMock,
) -> None:
    await service.notify_alert("DB overloaded", "CPU at 95%", severity="critical")

    ws_data = mock_ws.broadcast.call_args[0][1]
    assert ws_data["title"] == "[ALERT] DB overloaded"


# ---------------------------------------------------------------------------
# notify_budget_alert()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_notify_budget_alert(
    service: NotificationService,
    mock_telegram: AsyncMock,
) -> None:
    alert = {"model": "claude_code", "current": 12, "limit": 15, "period": "daily"}

    await service.notify_budget_alert(alert)

    sent_text = mock_telegram.send_message.call_args.kwargs["text"]
    assert "claude_code" in sent_text
    assert "12/15" in sent_text


# ---------------------------------------------------------------------------
# Priority emoji routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "priority,expected_emoji",
    [
        ("critical", "\U0001f6a8"),
        ("warning", "\u26a0\ufe0f"),
        ("info", "\u2139\ufe0f"),
    ],
)
async def test_telegram_priority_emoji(
    service: NotificationService,
    mock_telegram: AsyncMock,
    priority: str,
    expected_emoji: str,
) -> None:
    await service.notify("Title", "Body", priority=priority)  # type: ignore[arg-type]

    sent_text = mock_telegram.send_message.call_args.kwargs["text"]
    assert expected_emoji in sent_text


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_telegram_failure_does_not_crash(
    service: NotificationService,
    mock_telegram: AsyncMock,
    mock_ws: MagicMock,
) -> None:
    """If Telegram raises, WS should still succeed."""
    mock_telegram.send_message.side_effect = RuntimeError("Telegram down")

    await service.notify("Title", "Body")

    # WS should still have been called
    mock_ws.broadcast.assert_awaited_once()


@pytest.mark.asyncio()
async def test_ws_failure_does_not_crash(
    service: NotificationService,
    mock_telegram: AsyncMock,
    mock_ws: MagicMock,
) -> None:
    """If WS raises, Telegram should still succeed."""
    mock_ws.broadcast.side_effect = RuntimeError("WS down")

    await service.notify("Title", "Body")

    # Telegram should still have been called
    mock_telegram.send_message.assert_awaited_once()
