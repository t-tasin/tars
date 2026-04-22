"""Tests for briefings API and Telegram delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Tests: Telegram message splitting
# ---------------------------------------------------------------------------


class TestTelegramSplitting:
    def test_short_message_not_split(self) -> None:
        from integrations.telegram_bot import _split_message

        chunks = _split_message("Hello, Tasin!")
        assert len(chunks) == 1
        assert chunks[0] == "Hello, Tasin!"

    def test_long_message_split_at_newlines(self) -> None:
        from integrations.telegram_bot import _split_message

        # Create a message longer than 4096 chars
        lines = [f"Line {i}: {'x' * 80}" for i in range(60)]
        text = "\n".join(lines)
        assert len(text) > 4096

        chunks = _split_message(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 4096

        # All original content should be present
        reassembled = "\n".join(chunks)
        assert "Line 0:" in reassembled
        assert "Line 59:" in reassembled

    def test_long_line_hard_split(self) -> None:
        from integrations.telegram_bot import _split_message

        text = "x" * 5000
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4096
        assert len(chunks[1]) == 904

    def test_exact_limit_not_split(self) -> None:
        from integrations.telegram_bot import _split_message

        text = "x" * 4096
        chunks = _split_message(text)
        assert len(chunks) == 1

    def test_empty_message(self) -> None:
        from integrations.telegram_bot import _split_message

        chunks = _split_message("")
        assert len(chunks) == 1
        assert chunks[0] == ""


# ---------------------------------------------------------------------------
# Tests: TelegramGateway.deliver_briefing
# ---------------------------------------------------------------------------


class TestDeliverBriefing:
    @pytest.mark.asyncio
    async def test_deliver_morning_briefing(self) -> None:
        from integrations.telegram_bot import TelegramGateway

        gateway = TelegramGateway(bot_token="fake", chat_id="123")
        gateway.send_message = AsyncMock()

        briefing = {
            "narrative": "Good morning, Tasin. Sunny day ahead.",
            "briefing_id": "abc-123",
            "type": "morning",
        }

        await gateway.deliver_briefing(briefing)

        gateway.send_message.assert_awaited_once()
        call_args = gateway.send_message.call_args
        sent_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "Morning Briefing" in sent_text
        assert "Good morning, Tasin" in sent_text

    @pytest.mark.asyncio
    async def test_deliver_eod_briefing(self) -> None:
        from integrations.telegram_bot import TelegramGateway

        gateway = TelegramGateway(bot_token="fake", chat_id="123")
        gateway.send_message = AsyncMock()

        briefing = {
            "narrative": "Good evening, Tasin.",
            "briefing_id": "def-456",
            "type": "end_of_day",
        }

        await gateway.deliver_briefing(briefing)

        gateway.send_message.assert_awaited_once()
        call_args = gateway.send_message.call_args
        sent_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "End-of-Day Summary" in sent_text

    @pytest.mark.asyncio
    async def test_briefing_includes_keyboard(self) -> None:
        from integrations.telegram_bot import TelegramGateway

        gateway = TelegramGateway(bot_token="fake", chat_id="123")
        gateway.send_message = AsyncMock()

        await gateway.deliver_briefing(
            {
                "narrative": "Hello",
                "briefing_id": "test-id",
                "type": "morning",
            }
        )

        call_kwargs = gateway.send_message.call_args.kwargs
        assert "reply_markup" in call_kwargs
        assert call_kwargs["reply_markup"] is not None


# ---------------------------------------------------------------------------
# Tests: TelegramGateway.send_message (chunked)
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_chunks_long_text(self) -> None:
        from integrations.telegram_bot import TelegramGateway

        gateway = TelegramGateway(bot_token="fake", chat_id="123")
        mock_bot = AsyncMock()
        gateway._bot = mock_bot

        long_text = "\n".join([f"Line {i}: {'y' * 80}" for i in range(60)])
        await gateway.send_message(long_text)

        assert mock_bot.send_message.await_count > 1

    @pytest.mark.asyncio
    async def test_reply_markup_only_on_last_chunk(self) -> None:
        from integrations.telegram_bot import TelegramGateway

        gateway = TelegramGateway(bot_token="fake", chat_id="123")
        mock_bot = AsyncMock()
        gateway._bot = mock_bot

        long_text = "\n".join([f"Line {i}: {'z' * 80}" for i in range(60)])
        fake_markup = MagicMock()
        await gateway.send_message(long_text, reply_markup=fake_markup)

        calls = mock_bot.send_message.call_args_list
        # All but last should have reply_markup=None
        for call in calls[:-1]:
            assert call.kwargs.get("reply_markup") is None
        # Last should have the markup
        assert calls[-1].kwargs.get("reply_markup") is fake_markup
