"""Formats agent results into client-ready API responses."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from agents.base import AgentResult
from shared.constants import ContentType


class ResponseFormatter:
    """Formats agent results into client-ready API responses.

    All ``format*`` methods return plain dicts matching the Pydantic response
    schemas defined in ``api.schemas`` so they can be returned directly from
    FastAPI endpoints or serialised over WebSocket.
    """

    # ------------------------------------------------------------------
    # Standard response
    # ------------------------------------------------------------------

    def format(
        self,
        result: AgentResult,
        conversation_id: UUID,
        message_id: UUID,
        agent_type: str,
        model_used: str,
    ) -> dict[str, Any]:
        """Format a standard agent result into a ``TARSResponse`` shape."""
        return {
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "response": {
                "text": result.text,
                "content_type": result.content_type,
                "cards": result.cards,
                "approval": None,
            },
            "agent_used": agent_type,
            "model_used": model_used,
        }

    # ------------------------------------------------------------------
    # Approval response
    # ------------------------------------------------------------------

    def format_approval(
        self,
        result: AgentResult,
        approval: dict[str, Any],
        conversation_id: UUID,
        message_id: UUID,
        agent_type: str,
        model_used: str,
    ) -> dict[str, Any]:
        """Format a result that requires user approval."""
        response = self.format(result, conversation_id, message_id, agent_type, model_used)
        response["response"]["content_type"] = ContentType.APPROVAL
        response["response"]["approval"] = {
            "approval_id": str(approval["id"]),
            "action_type": approval["action_type"],
            "title": approval["title"],
            "preview": approval["preview_payload"],
        }
        return response

    # ------------------------------------------------------------------
    # Briefing response
    # ------------------------------------------------------------------

    def format_briefing(self, briefing: dict[str, Any]) -> dict[str, Any]:
        """Format a briefing record into a ``BriefingResponse`` shape."""
        return {
            "briefing_id": str(briefing["id"]),
            "type": briefing["briefing_type"],
            "date": str(briefing["briefing_date"]),
            "payload": briefing["payload"],
            "narrative": briefing["narrative"],
            "created_at": briefing["created_at"],
            "delivered": briefing.get("delivered", False),
        }

    # ------------------------------------------------------------------
    # Error response
    # ------------------------------------------------------------------

    def format_error(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Format an ``ErrorResponse`` shape."""
        return {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        }

    # ------------------------------------------------------------------
    # Telegram formatter
    # ------------------------------------------------------------------

    def format_for_telegram(self, response: dict[str, Any]) -> str:
        """Convert a ``TARSResponse`` into Telegram MarkdownV2 text.

        Flattens cards into bullet lists, formats approval blocks as
        descriptive text with action hints, and escapes special characters
        required by Telegram MarkdownV2.
        """
        payload = response.get("response", {})
        parts: list[str] = []

        # Main text
        text = payload.get("text", "")
        if text:
            parts.append(text)

        # Cards → bullet list
        cards: list[dict[str, Any]] = payload.get("cards", [])
        if cards:
            card_lines: list[str] = []
            for card in cards:
                title = card.get("title", "")
                body = card.get("body", card.get("description", ""))
                if title and body:
                    card_lines.append(f"• *{title}*: {body}")
                elif title:
                    card_lines.append(f"• *{title}*")
                elif body:
                    card_lines.append(f"• {body}")
            if card_lines:
                parts.append("\n".join(card_lines))

        # Approval block
        approval = payload.get("approval")
        if approval:
            parts.append(
                f"⏳ *Approval required*: {approval['title']}\n"
                f"Action: {approval['action_type']}\n"
                f"Reply /approve or /reject"
            )

        combined = "\n\n".join(parts) if parts else "No response."
        return _escape_telegram_md(combined)

    # ------------------------------------------------------------------
    # TTS formatter
    # ------------------------------------------------------------------

    def format_for_tts(self, response: dict[str, Any]) -> str:
        """Convert a ``TARSResponse`` into plain spoken text for TTS.

        Strips markdown, URLs, and special characters.  Produces natural
        spoken-language output suitable for AirPlay to HomePod.
        """
        payload = response.get("response", {})
        parts: list[str] = []

        text = payload.get("text", "")
        if text:
            parts.append(_strip_for_speech(text))

        cards: list[dict[str, Any]] = payload.get("cards", [])
        for card in cards:
            title = card.get("title", "")
            body = card.get("body", card.get("description", ""))
            if title and body:
                parts.append(f"{title}. {body}")
            elif title:
                parts.append(title)
            elif body:
                parts.append(body)

        approval = payload.get("approval")
        if approval:
            parts.append(
                f"Approval required: {approval['title']}. "
                f"Please approve or reject this action."
            )

        return " ".join(parts) if parts else "No response."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Telegram MarkdownV2 requires escaping these characters outside of
# code blocks and pre-formatted text.
_TG_ESCAPE_RE = re.compile(r"([_\[\]()~`>#+\-=|{}.!\\])")


def _escape_telegram_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    Characters inside existing ``*bold*`` pairs are preserved — only
    the surrounding markup characters that Telegram requires escaping
    are handled.
    """
    return _TG_ESCAPE_RE.sub(r"\\\1", text)


# Patterns stripped for TTS output.
_URL_RE = re.compile(r"https?://\S+")
_MD_BOLD_RE = re.compile(r"\*\*?([^*]+)\*\*?")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[\-\*]\s+", re.MULTILINE)
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_EXTRA_SPACES_RE = re.compile(r" {2,}")


def _strip_for_speech(text: str) -> str:
    """Remove markdown, URLs, and special characters for TTS."""
    text = _MD_LINK_RE.sub(r"\1", text)  # [link text](url) → link text
    text = _URL_RE.sub("", text)          # drop bare URLs
    text = _MD_BOLD_RE.sub(r"\1", text)   # **bold** / *bold* → bold
    text = _MD_HEADER_RE.sub("", text)    # ## header → header
    text = _MD_BULLET_RE.sub("", text)    # - item → item
    text = _MD_CODE_RE.sub(r"\1", text)   # `code` → code
    text = _EXTRA_SPACES_RE.sub(" ", text)
    return text.strip()
