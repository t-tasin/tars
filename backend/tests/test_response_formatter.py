"""Tests for ResponseFormatter."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from agents.base import AgentResult
from orchestrator.response_formatter import ResponseFormatter
from shared.constants import ContentType


@pytest.fixture
def fmt() -> ResponseFormatter:
    return ResponseFormatter()


@pytest.fixture
def conv_id() -> UUID:
    return uuid4()


@pytest.fixture
def msg_id() -> UUID:
    return uuid4()


def _make_result(**kwargs) -> AgentResult:
    defaults = {"success": True, "text": "Hello, Tasin."}
    defaults.update(kwargs)
    return AgentResult(**defaults)


# ── format() ────────────────────────────────────────────────────────────


class TestFormat:
    def test_basic_shape(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result()
        resp = fmt.format(result, conv_id, msg_id, "general", "gemini_flash")

        assert resp["conversation_id"] == str(conv_id)
        assert resp["message_id"] == str(msg_id)
        assert resp["agent_used"] == "general"
        assert resp["model_used"] == "gemini_flash"

    def test_response_payload(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Good morning!", content_type="briefing")
        resp = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")

        payload = resp["response"]
        assert payload["text"] == "Good morning!"
        assert payload["content_type"] == "briefing"
        assert payload["cards"] == []
        assert payload["approval"] is None

    def test_cards_passed_through(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        cards = [{"title": "Weather", "body": "Sunny, 72°F"}]
        result = _make_result(cards=cards)
        resp = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")

        assert resp["response"]["cards"] == cards

    def test_uuid_serialised_as_string(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        resp = fmt.format(_make_result(), conv_id, msg_id, "general", "local")
        # Must be strings, not UUID objects, for JSON serialisation
        assert isinstance(resp["conversation_id"], str)
        assert isinstance(resp["message_id"], str)


# ── format_approval() ──────────────────────────────────────────────────


class TestFormatApproval:
    def test_approval_shape(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Draft ready for review.")
        approval_id = uuid4()
        approval = {
            "id": approval_id,
            "action_type": "send_email",
            "title": "Send email to Prof. Sadigh",
            "preview_payload": {
                "to": "sadigh@cs.stanford.edu",
                "subject": "Following up",
                "body": "Dear Professor...",
            },
        }
        resp = fmt.format_approval(result, approval, conv_id, msg_id, "communication", "claude_code")

        assert resp["response"]["content_type"] == ContentType.APPROVAL
        ap = resp["response"]["approval"]
        assert ap["approval_id"] == str(approval_id)
        assert ap["action_type"] == "send_email"
        assert ap["title"] == "Send email to Prof. Sadigh"
        assert ap["preview"]["to"] == "sadigh@cs.stanford.edu"

    def test_approval_preserves_text(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Here's the draft.")
        approval = {
            "id": uuid4(),
            "action_type": "create_event",
            "title": "Create event",
            "preview_payload": {},
        }
        resp = fmt.format_approval(result, approval, conv_id, msg_id, "daily_life", "gemini_flash")
        assert resp["response"]["text"] == "Here's the draft."


# ── format_briefing() ──────────────────────────────────────────────────


class TestFormatBriefing:
    def test_briefing_shape(self, fmt: ResponseFormatter):
        briefing_id = uuid4()
        briefing = {
            "id": briefing_id,
            "briefing_type": "morning",
            "briefing_date": "2026-03-10",
            "payload": {"weather": "sunny", "calendar": []},
            "narrative": "Good morning! Today is sunny.",
            "created_at": "2026-03-10T07:00:00Z",
            "delivered": True,
        }
        resp = fmt.format_briefing(briefing)

        assert resp["briefing_id"] == str(briefing_id)
        assert resp["type"] == "morning"
        assert resp["date"] == "2026-03-10"
        assert resp["narrative"] == "Good morning! Today is sunny."
        assert resp["delivered"] is True
        assert resp["payload"]["weather"] == "sunny"

    def test_briefing_defaults_delivered_false(self, fmt: ResponseFormatter):
        briefing = {
            "id": uuid4(),
            "briefing_type": "eod",
            "briefing_date": "2026-03-10",
            "payload": {},
            "narrative": "Summary.",
            "created_at": "2026-03-10T21:00:00Z",
        }
        resp = fmt.format_briefing(briefing)
        assert resp["delivered"] is False


# ── format_error() ─────────────────────────────────────────────────────


class TestFormatError:
    def test_error_shape(self, fmt: ResponseFormatter):
        resp = fmt.format_error("agent_failed", "The briefing agent timed out.")

        assert resp["error"]["code"] == "agent_failed"
        assert resp["error"]["message"] == "The briefing agent timed out."
        assert resp["error"]["details"] == {}

    def test_error_with_details(self, fmt: ResponseFormatter):
        details = {"agent": "coding", "retries": 3}
        resp = fmt.format_error("timeout", "Request timed out", details=details)

        assert resp["error"]["details"] == details

    def test_error_none_details_becomes_empty_dict(self, fmt: ResponseFormatter):
        resp = fmt.format_error("not_found", "Not found", details=None)
        assert resp["error"]["details"] == {}


# ── format_for_telegram() ──────────────────────────────────────────────


class TestFormatForTelegram:
    def test_simple_text(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Your schedule is clear today.")
        response = fmt.format(result, conv_id, msg_id, "daily_life", "gemini_flash")
        tg = fmt.format_for_telegram(response)

        assert "Your schedule is clear today" in tg

    def test_cards_formatted_as_bullets(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        cards = [
            {"title": "Weather", "body": "Sunny 72°F"},
            {"title": "Calendar", "body": "No events"},
        ]
        result = _make_result(cards=cards)
        response = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")
        tg = fmt.format_for_telegram(response)

        assert "Weather" in tg
        assert "Calendar" in tg
        assert "•" in tg

    def test_approval_block(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Draft ready.")
        approval = {
            "id": uuid4(),
            "action_type": "send_email",
            "title": "Email to recruiter",
            "preview_payload": {"to": "test@example.com"},
        }
        response = fmt.format_approval(result, approval, conv_id, msg_id, "communication", "claude_code")
        tg = fmt.format_for_telegram(response)

        assert "Approval required" in tg
        assert "Email to recruiter" in tg
        assert "/approve" in tg or "approve" in tg.lower()

    def test_escapes_special_chars(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Use array[0].value to get the result.")
        response = fmt.format(result, conv_id, msg_id, "coding", "claude_code")
        tg = fmt.format_for_telegram(response)

        # Telegram MarkdownV2 requires escaping [ ] . ( )
        assert "\\[" in tg
        assert "\\]" in tg
        assert "\\." in tg

    def test_empty_response(self, fmt: ResponseFormatter):
        response = {"response": {"text": "", "cards": [], "approval": None}}
        tg = fmt.format_for_telegram(response)
        assert tg == "No response\\."

    def test_card_with_only_title(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        cards = [{"title": "Status"}]
        result = _make_result(cards=cards)
        response = fmt.format(result, conv_id, msg_id, "general", "local")
        tg = fmt.format_for_telegram(response)
        assert "Status" in tg

    def test_card_with_only_body(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        cards = [{"body": "All systems green."}]
        result = _make_result(cards=cards)
        response = fmt.format(result, conv_id, msg_id, "general", "local")
        tg = fmt.format_for_telegram(response)
        assert "All systems green" in tg


# ── format_for_tts() ───────────────────────────────────────────────────


class TestFormatForTTS:
    def test_simple_text(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Good morning, Tasin.")
        response = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")
        tts = fmt.format_for_tts(response)

        assert tts == "Good morning, Tasin."

    def test_strips_markdown_bold(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Today is **sunny** and *warm*.")
        response = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")
        tts = fmt.format_for_tts(response)

        assert "**" not in tts
        assert "*" not in tts
        assert "sunny" in tts
        assert "warm" in tts

    def test_strips_urls(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Check this: https://example.com/foo for details.")
        response = fmt.format(result, conv_id, msg_id, "research", "claude_code")
        tts = fmt.format_for_tts(response)

        assert "https://" not in tts
        assert "example.com" not in tts
        assert "Check this:" in tts

    def test_strips_markdown_links(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="See [the docs](https://docs.example.com) for more.")
        response = fmt.format(result, conv_id, msg_id, "research", "claude_code")
        tts = fmt.format_for_tts(response)

        assert "the docs" in tts
        assert "https://" not in tts
        assert "[" not in tts

    def test_strips_headers(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="## Morning Briefing\nToday looks good.")
        response = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")
        tts = fmt.format_for_tts(response)

        assert "##" not in tts
        assert "Morning Briefing" in tts

    def test_strips_code_backticks(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Run `pip install tars` to set up.")
        response = fmt.format(result, conv_id, msg_id, "coding", "claude_code")
        tts = fmt.format_for_tts(response)

        assert "`" not in tts
        assert "pip install tars" in tts

    def test_cards_spoken_naturally(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        cards = [
            {"title": "Weather", "body": "Sunny, 72°F"},
            {"title": "Calendar", "description": "Team standup at 10 AM"},
        ]
        result = _make_result(cards=cards)
        response = fmt.format(result, conv_id, msg_id, "briefing", "gemini_pro")
        tts = fmt.format_for_tts(response)

        assert "Weather" in tts
        assert "Sunny" in tts
        assert "Calendar" in tts
        assert "Team standup" in tts

    def test_approval_spoken(self, fmt: ResponseFormatter, conv_id: UUID, msg_id: UUID):
        result = _make_result(text="Email drafted.")
        approval = {
            "id": uuid4(),
            "action_type": "send_email",
            "title": "Send follow-up email",
            "preview_payload": {},
        }
        response = fmt.format_approval(result, approval, conv_id, msg_id, "communication", "claude_code")
        tts = fmt.format_for_tts(response)

        assert "Approval required" in tts
        assert "Send follow-up email" in tts

    def test_empty_response(self, fmt: ResponseFormatter):
        response = {"response": {"text": "", "cards": [], "approval": None}}
        tts = fmt.format_for_tts(response)
        assert tts == "No response."
