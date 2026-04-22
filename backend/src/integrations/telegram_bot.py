"""Telegram bot gateway for T.A.R.S."""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()

# Telegram message size limit
_TELEGRAM_MAX_LENGTH = 4096


class TelegramGateway:
    """Telegram bot interface for sending messages and briefings."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._bot: Any = None

    async def _ensure_bot(self) -> Any:
        """Lazy-init the telegram bot instance."""
        if self._bot is None:
            from telegram import Bot

            self._bot = Bot(token=self._bot_token)
        return self._bot

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Any = None,
    ) -> None:
        """Send a text message to the configured chat."""
        bot = await self._ensure_bot()
        chunks = _split_message(text)

        for i, chunk in enumerate(chunks):
            # Only attach reply_markup to the last chunk
            markup = reply_markup if i == len(chunks) - 1 else None
            await bot.send_message(
                chat_id=self._chat_id,
                text=chunk,
                parse_mode=parse_mode,
                reply_markup=markup,
            )

        log.info(
            "telegram_message_sent",
            chat_id=self._chat_id,
            chunks=len(chunks),
            total_length=len(text),
        )

    async def deliver_briefing(self, briefing: dict[str, Any]) -> None:
        """Send the morning or EOD briefing to Telegram.

        Formats the narrative with Telegram HTML, splits into chunks
        if > 4096 chars, and includes an inline keyboard for full details.
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        briefing_type = briefing.get("type", "morning")
        narrative = briefing.get("narrative", "")
        briefing_id = briefing.get("briefing_id", "")

        # Format header
        if briefing_type == "end_of_day":
            header = "\U0001f319 <b>End-of-Day Summary</b>"
        else:
            header = "\u2600\ufe0f <b>Morning Briefing</b>"

        formatted = f"{header}\n\n{narrative}"

        # Inline keyboard with "Full Details" button
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\U0001f4cb Full Details",
                        callback_data=f"briefing_detail:{briefing_id}",
                    )
                ],
            ]
        )

        await self.send_message(formatted, reply_markup=keyboard)

        log.info(
            "telegram_briefing_delivered",
            briefing_id=briefing_id,
            type=briefing_type,
            narrative_len=len(narrative),
        )


def _split_message(text: str) -> list[str]:
    """Split a message into chunks that fit within Telegram's 4096 char limit.

    Splits on newline boundaries when possible to avoid breaking mid-sentence.
    """
    if len(text) <= _TELEGRAM_MAX_LENGTH:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= _TELEGRAM_MAX_LENGTH:
            chunks.append(remaining)
            break

        # Find the last newline within the limit
        split_at = remaining.rfind("\n", 0, _TELEGRAM_MAX_LENGTH)
        if split_at == -1:
            # No newline found — hard split at the limit
            split_at = _TELEGRAM_MAX_LENGTH

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks
