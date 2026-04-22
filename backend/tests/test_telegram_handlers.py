"""Tests for Telegram incoming message and callback handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import integrations.telegram_handlers as mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_chat_id():
    """Set the module-level _chat_id for all tests, reset after."""
    mod._chat_id = "12345"
    yield
    mod._chat_id = None


def _make_update(chat_id: str = "12345", text: str = "hello") -> MagicMock:
    """Create a mock Update object with effective_chat and message."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_callback_update(callback_data: str, chat_id: str = "12345") -> MagicMock:
    """Create a mock Update with a callback_query."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.callback_query = MagicMock()
    update.callback_query.data = callback_data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.text = "Original message"
    return update


# ---------------------------------------------------------------------------
# Test: message routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_message_through_orchestrator():
    """Text messages are routed through orchestrator.process_message."""
    update = _make_update(text="What's the weather?")
    mock_orchestrator = MagicMock()
    mock_orchestrator.process_message = AsyncMock(
        return_value={"response": {"text": "It's sunny!", "content_type": "text"}}
    )

    with patch("src.orchestrator.engine.get_orchestrator", return_value=mock_orchestrator):
        await mod.handle_message(update, MagicMock())

    mock_orchestrator.process_message.assert_awaited_once_with(
        text="What's the weather?", source="telegram"
    )
    update.message.reply_text.assert_awaited_once_with("It's sunny!")


@pytest.mark.asyncio
async def test_ignores_unauthorized_chat():
    """Messages from unauthorized chats are silently ignored."""
    update = _make_update(chat_id="99999", text="sneaky message")

    await mod.handle_message(update, MagicMock())

    update.message.reply_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: approval callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_callback():
    """approve:{uuid} callback calls approval_manager.approve."""
    aid = str(uuid4())
    update = _make_callback_update(f"approve:{aid}")

    mock_approval_mgr = MagicMock()
    mock_approval_mgr.approve = AsyncMock(return_value={"id": aid, "status": "approved"})

    mock_orchestrator = MagicMock()
    mock_orchestrator.approval_manager = mock_approval_mgr

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.orchestrator.engine.get_orchestrator", return_value=mock_orchestrator), \
         patch("src.db.session.get_db_session", return_value=mock_session):
        await mod.handle_callback(update, MagicMock())

    update.callback_query.answer.assert_awaited_once()
    mock_approval_mgr.approve.assert_awaited_once()
    # Verify source="telegram" was passed
    call_kwargs = mock_approval_mgr.approve.call_args
    assert call_kwargs.kwargs.get("source") == "telegram" or call_kwargs[1].get("source") == "telegram"


@pytest.mark.asyncio
async def test_reject_callback():
    """reject:{uuid} callback calls approval_manager.reject."""
    aid = str(uuid4())
    update = _make_callback_update(f"reject:{aid}")

    mock_approval_mgr = MagicMock()
    mock_approval_mgr.reject = AsyncMock(return_value={"id": aid, "status": "rejected"})

    mock_orchestrator = MagicMock()
    mock_orchestrator.approval_manager = mock_approval_mgr

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("src.orchestrator.engine.get_orchestrator", return_value=mock_orchestrator), \
         patch("src.db.session.get_db_session", return_value=mock_session):
        await mod.handle_callback(update, MagicMock())

    update.callback_query.answer.assert_awaited_once()
    mock_approval_mgr.reject.assert_awaited_once()
    call_kwargs = mock_approval_mgr.reject.call_args
    assert call_kwargs.kwargs.get("source") == "telegram" or call_kwargs[1].get("source") == "telegram"


# ---------------------------------------------------------------------------
# Test: job callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_skip_callback():
    """job_skip_{id} callback calls handle_job_skip."""
    update = _make_callback_update("job_skip_abc123")

    mock_skip = AsyncMock(return_value={"success": True, "message": "Skipped: Engineer at Acme"})

    with patch("src.integrations.telegram_job_handlers.handle_job_skip", mock_skip), \
         patch("src.integrations.telegram_job_handlers.handle_job_apply", AsyncMock()), \
         patch("src.integrations.telegram_job_handlers.handle_job_details", AsyncMock()), \
         patch("src.integrations.telegram_job_handlers.send_job_details_message", AsyncMock()), \
         patch("src.integrations.notification_service.get_notification_service", MagicMock()):
        await mod.handle_callback(update, MagicMock())

    update.callback_query.answer.assert_awaited_once()
    mock_skip.assert_awaited_once_with("abc123")


# ---------------------------------------------------------------------------
# Test: unknown callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_callback_ignored():
    """Unknown callback_data does not crash — just logs a warning."""
    update = _make_callback_update("some_random_data_xyz")

    # Should not raise
    await mod.handle_callback(update, MagicMock())

    update.callback_query.answer.assert_awaited_once()
    # No edit_message_text should be called for unknown callbacks
    update.callback_query.edit_message_text.assert_not_awaited()
