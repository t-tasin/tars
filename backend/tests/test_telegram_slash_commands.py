"""Tests for Telegram slash command handlers (P2.5-01)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import integrations.telegram_handlers as mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_chat_id():
    mod._chat_id = "12345"
    yield
    mod._chat_id = None


def _make_command_update(command: str, chat_id: str = "12345") -> MagicMock:
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.text = f"/{command}"
    update.message.reply_text = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# Test: /help
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_help_returns_command_list():
    update = _make_command_update("help")
    await mod.handle_slash_help(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "/briefing" in reply
    assert "/health" in reply
    assert "/budget" in reply


# ---------------------------------------------------------------------------
# Test: /status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_status_returns_status():
    update = _make_command_update("status")
    await mod.handle_slash_status(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert reply  # non-empty response


# ---------------------------------------------------------------------------
# Test: /briefing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_briefing_calls_briefing_agent():
    update = _make_command_update("briefing")
    mock_result = MagicMock()
    mock_result.text = "Good morning, Tasin! Here is your briefing."
    mock_instance = MagicMock()
    mock_instance.execute = AsyncMock(return_value=mock_result)

    with patch("src.agents.briefing.BriefingAgent", return_value=mock_instance):
        await mod.handle_slash_briefing(update, MagicMock())

    mock_instance.execute.assert_awaited_once()
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Tasin" in reply


# ---------------------------------------------------------------------------
# Test: /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_health_returns_postgres_and_redis_status():
    update = _make_command_update("health")
    mock_pg = AsyncMock(return_value={"status": "connected", "latency_ms": 2})
    mock_redis = AsyncMock(return_value={"status": "connected", "latency_ms": 1})

    with (
        patch("src.api.health._check_postgres", mock_pg),
        patch("src.api.health._check_redis", mock_redis),
    ):
        await mod.handle_slash_health(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0].lower()
    assert "postgres" in reply or "redis" in reply


# ---------------------------------------------------------------------------
# Test: /budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_budget_returns_usage_summary():
    update = _make_command_update("budget")
    mock_summary = {
        "gemini_flash": {
            "calls": 10,
            "tokens_input": 500,
            "tokens_output": 300,
            "estimated_cost": "0.00",
        }
    }
    mock_tracker = MagicMock()
    mock_tracker.get_daily_summary = AsyncMock(return_value=mock_summary)
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.db.session.get_db_session", return_value=mock_session),
        patch("src.models.usage_tracker.UsageTracker", return_value=mock_tracker),
    ):
        await mod.handle_slash_budget(update, MagicMock())

    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0]
    assert reply


# ---------------------------------------------------------------------------
# Test: unknown slash command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_unknown_returns_friendly_message():
    update = _make_command_update("totally_unknown")
    await mod.handle_slash_unknown(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args[0][0].lower()
    assert "plain english" in reply or "help" in reply


# ---------------------------------------------------------------------------
# Test: authorization guard on slash commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_commands_reject_unauthorized_chat():
    for handler in (
        mod.handle_slash_help,
        mod.handle_slash_status,
        mod.handle_slash_unknown,
    ):
        update = _make_command_update("help", chat_id="99999")
        await handler(update, MagicMock())
        update.message.reply_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: create_telegram_application registers command handlers
# ---------------------------------------------------------------------------


def test_create_application_registers_command_handlers():
    app = mod.create_telegram_application(bot_token="fake:token", chat_id="12345")
    handler_commands: set[str] = set()
    for group_handlers in app.handlers.values():
        for h in group_handlers:
            if hasattr(h, "commands"):
                handler_commands.update(h.commands)
    for expected_cmd in ("briefing", "status", "help", "health", "budget"):
        assert expected_cmd in handler_commands, f"/{expected_cmd} not registered"
