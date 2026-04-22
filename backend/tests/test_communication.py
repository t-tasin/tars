"""Tests for the CommunicationAgent."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext
from agents.communication import (
    CommunicationAgent,
    _generate_subject,
    _parse_request,
)
from models.claude_spawner import ClaudeCodeResult, ClaudeSpawnError

# Access the fake db.session module injected by conftest
_fake_db_session_module = sys.modules["db.session"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(message: str, **config_overrides: Any) -> AgentContext:
    return AgentContext(
        user_message=message,
        intent_type="communication",
        config=config_overrides,
    )


def _make_claude_result(
    text: str = "Dear Professor Sadigh,\n\nThank you for your time.\n\nBest,\nTasin",
    success: bool = True,
) -> ClaudeCodeResult:
    return ClaudeCodeResult(
        text=text,
        success=success,
        duration_ms=2500,
        cost_usd=0.02,
        session_id="test-session",
        num_turns=1,
        error=None if success else "timeout",
    )


class _FakeDbCtx:
    """Minimal async context manager wrapping an AsyncMock session."""

    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _install_mock_session(
    execute_results: list[MagicMock] | None = None,
) -> AsyncMock:
    """Install a mock get_db_session on the fake module and return the session."""
    mock_session = AsyncMock()
    if execute_results is not None:
        mock_session.execute = AsyncMock(side_effect=execute_results)
    else:
        result = MagicMock()
        result.first.return_value = None
        result.all.return_value = []
        mock_session.execute = AsyncMock(return_value=result)

    _fake_db_session_module.get_db_session = lambda: _FakeDbCtx(mock_session)
    return mock_session


def _make_contact_result(
    full_name: str = "Prof. Dorsa Sadigh",
    email: str = "sadigh@cs.stanford.edu",
    organization: str = "Stanford University",
    relationship_type: str = "professor",
) -> MagicMock:
    """Build a MagicMock that mimics a DB result for a contact query."""
    row = MagicMock()
    row.full_name = full_name
    row.email_addresses = [{"address": email}]
    row.organization = organization
    row.relationship_type = relationship_type

    result = MagicMock()
    result.first.return_value = row
    return result


def _make_email_history_result(
    emails: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a MagicMock that mimics a DB result for email history query."""
    if emails is None:
        emails = [
            {
                "from_address": "sadigh@cs.stanford.edu",
                "from_name": "Dorsa Sadigh",
                "subject": "Re: Research opportunity",
                "snippet": "Thanks for reaching out...",
            }
        ]

    rows = []
    for email in emails:
        row = MagicMock()
        row.from_address = email["from_address"]
        row.from_name = email["from_name"]
        row.subject = email["subject"]
        row.snippet = email["snippet"]
        row.received_at = datetime(2026, 3, 8, 14, 30, tzinfo=UTC)
        rows.append(row)

    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# Test: _parse_request
# ---------------------------------------------------------------------------


class TestParseRequest:
    def test_extracts_recipient_name(self) -> None:
        result = _parse_request("Draft an email to Professor Sadigh about research")
        assert result["recipient"] == "Professor Sadigh"

    def test_extracts_email_address(self) -> None:
        result = _parse_request("Email sadigh@stanford.edu about the project")
        assert result["recipient"] == "sadigh@stanford.edu"

    def test_extracts_subject(self) -> None:
        result = _parse_request("Write an email to John about the meeting tomorrow")
        assert "meeting tomorrow" in result["subject"]

    def test_detects_follow_up_intent(self) -> None:
        result = _parse_request("Follow up with Dr. Smith about the proposal")
        assert result["intent"] == "follow_up"

    def test_detects_thank_you_intent(self) -> None:
        result = _parse_request("Send a thank you email to Sarah")
        assert result["intent"] == "thank_you"

    def test_detects_scheduling_intent(self) -> None:
        result = _parse_request("Email Bob to schedule a meeting")
        assert result["intent"] == "scheduling"

    def test_no_recipient(self) -> None:
        result = _parse_request("write an email")
        assert result["recipient"] == ""


# ---------------------------------------------------------------------------
# Test: _generate_subject
# ---------------------------------------------------------------------------


class TestGenerateSubject:
    def test_follow_up_subject(self) -> None:
        assert _generate_subject("follow_up", "John", "body") == "Following up"

    def test_thank_you_subject(self) -> None:
        assert _generate_subject("thank_you", "Jane", "body") == "Thank you"

    def test_fallback_from_body(self) -> None:
        body = "Hello I wanted to discuss the upcoming conference presentation"
        subject = _generate_subject("", "Someone", body)
        assert len(subject) <= 53  # 50 + "..."
        assert subject  # not empty

    def test_empty_body_fallback(self) -> None:
        assert _generate_subject("", "Someone", "") == "Message"


# ---------------------------------------------------------------------------
# Test: CommunicationAgent.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCommunicationAgentExecute:
    async def test_successful_draft_professor(self) -> None:
        """Drafting to a professor returns tier3 escalation."""
        agent = CommunicationAgent()

        # DB returns: 1) contact lookup, 2) email history
        _install_mock_session(
            [
                _make_contact_result(),
                _make_email_history_result(),
            ]
        )

        with patch.object(agent, "_claude") as mock_claude:
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            result = await agent.execute(
                _make_context("Draft an email to Professor Sadigh about research opportunities")
            )

        assert result.success is True
        assert result.has_side_effects is True
        assert result.action_type == "email_professor"
        assert result.preview is not None
        assert result.preview["to"] == "sadigh@cs.stanford.edu"
        assert result.preview["body"]
        assert result.approval_title == "Send email to Prof. Dorsa Sadigh"

    async def test_successful_draft_normal_recipient(self) -> None:
        """Drafting to a normal contact returns tier2 approval."""
        agent = CommunicationAgent()

        _install_mock_session(
            [
                _make_contact_result(
                    full_name="Sarah Chen",
                    email="sarah@company.com",
                    organization="Acme Corp",
                    relationship_type="colleague",
                ),
                _make_email_history_result(emails=[]),
            ]
        )

        with patch.object(agent, "_claude") as mock_claude:
            mock_claude.execute = AsyncMock(
                return_value=_make_claude_result(
                    text="Hi Sarah,\n\nJust following up.\n\nBest,\nTasin",
                )
            )

            result = await agent.execute(_make_context("Follow up with Sarah Chen about the project"))

        assert result.success is True
        assert result.has_side_effects is True
        assert result.action_type == "send_email"
        assert result.preview["to"] == "sarah@company.com"

    async def test_no_recipient_returns_error(self) -> None:
        """When no recipient can be parsed, return a helpful error."""
        agent = CommunicationAgent()
        result = await agent.execute(_make_context("write an email"))

        assert result.success is False
        assert result.error == "no_recipient"

    async def test_contact_no_email_returns_error(self) -> None:
        """When the contact has no email, return an error."""
        agent = CommunicationAgent()

        # Return a contact with empty email_addresses
        row = MagicMock()
        row.full_name = "John Doe"
        row.email_addresses = []
        row.organization = None
        row.relationship_type = None
        contact_result = MagicMock()
        contact_result.first.return_value = row

        _install_mock_session([contact_result])

        result = await agent.execute(_make_context("Email John Doe about the meeting"))

        assert result.success is False
        assert result.error == "no_email"

    async def test_contact_not_found_returns_error(self) -> None:
        """When no contact is found in DB, return an error."""
        agent = CommunicationAgent()

        # Return empty contact result
        contact_result = MagicMock()
        contact_result.first.return_value = None

        _install_mock_session([contact_result])

        result = await agent.execute(_make_context("Email John Doe about the meeting"))

        assert result.success is False
        # No contact found means no email → no_email or no contact
        assert result.error is not None

    async def test_claude_unavailable_returns_error(self) -> None:
        """When Claude spawn fails, return a graceful error."""
        agent = CommunicationAgent()

        _install_mock_session(
            [
                _make_contact_result(
                    full_name="Sarah Chen",
                    email="sarah@company.com",
                    relationship_type="colleague",
                ),
                _make_email_history_result(emails=[]),
            ]
        )

        with patch.object(agent, "_claude") as mock_claude:
            mock_claude.execute = AsyncMock(side_effect=ClaudeSpawnError("not found"))

            result = await agent.execute(_make_context("Email Sarah Chen about the deadline"))

        assert result.success is False
        assert result.error == "claude_unavailable"

    async def test_claude_empty_response_returns_error(self) -> None:
        """When Claude returns empty text, return an error."""
        agent = CommunicationAgent()

        _install_mock_session(
            [
                _make_contact_result(
                    full_name="Sarah Chen",
                    email="sarah@company.com",
                    relationship_type="colleague",
                ),
                _make_email_history_result(emails=[]),
            ]
        )

        with patch.object(agent, "_claude") as mock_claude:
            mock_claude.execute = AsyncMock(return_value=_make_claude_result(text="", success=True))

            result = await agent.execute(_make_context("Email Sarah Chen about the deadline"))

        assert result.success is False
        assert result.error == "empty_draft"

    async def test_preview_includes_full_draft(self) -> None:
        """The preview dict must include to, subject, and body."""
        agent = CommunicationAgent()

        _install_mock_session(
            [
                _make_contact_result(
                    full_name="Sarah Chen",
                    email="sarah@company.com",
                    relationship_type="colleague",
                ),
                _make_email_history_result(emails=[]),
            ]
        )

        draft = "Hi Sarah,\n\nWanted to follow up.\n\nBest,\nTasin"
        with patch.object(agent, "_claude") as mock_claude:
            mock_claude.execute = AsyncMock(return_value=_make_claude_result(text=draft))

            result = await agent.execute(_make_context("Follow up with Sarah Chen about the deadline"))

        assert result.preview is not None
        assert "to" in result.preview
        assert "subject" in result.preview
        assert "body" in result.preview
        assert result.preview["body"] == draft


# ---------------------------------------------------------------------------
# Test: _get_action_type
# ---------------------------------------------------------------------------


class TestGetActionType:
    def test_professor_relationship(self) -> None:
        agent = CommunicationAgent()
        action = agent._get_action_type(
            recipient_email="prof@university.edu",
            recipient_name="John Smith",
            relationship_type="professor",
        )
        assert action == "email_professor"

    def test_advisor_relationship(self) -> None:
        agent = CommunicationAgent()
        action = agent._get_action_type(
            recipient_email="advisor@uni.edu",
            recipient_name="Jane Doe",
            relationship_type="advisor",
        )
        assert action == "email_professor"

    def test_name_with_professor_prefix(self) -> None:
        agent = CommunicationAgent()
        action = agent._get_action_type(
            recipient_email="someone@company.com",
            recipient_name="Prof. Smith",
            relationship_type="",
        )
        assert action == "email_professor"

    def test_edu_email_escalates(self) -> None:
        agent = CommunicationAgent()
        action = agent._get_action_type(
            recipient_email="smith@cs.stanford.edu",
            recipient_name="Smith",
            relationship_type="",
        )
        assert action == "email_professor"

    def test_normal_contact(self) -> None:
        agent = CommunicationAgent()
        action = agent._get_action_type(
            recipient_email="bob@gmail.com",
            recipient_name="Bob Jones",
            relationship_type="friend",
        )
        assert action == "send_email"


# ---------------------------------------------------------------------------
# Test: Contact lookup (DB layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLookupContact:
    async def test_contact_found(self) -> None:
        agent = CommunicationAgent()
        _install_mock_session([_make_contact_result()])

        contact = await agent._lookup_contact("Sadigh")

        assert contact is not None
        assert contact["full_name"] == "Prof. Dorsa Sadigh"
        assert contact["email"] == "sadigh@cs.stanford.edu"
        assert contact["relationship_type"] == "professor"

    async def test_contact_not_found(self) -> None:
        agent = CommunicationAgent()

        no_result = MagicMock()
        no_result.first.return_value = None
        _install_mock_session([no_result])

        contact = await agent._lookup_contact("Nobody")
        assert contact is None

    async def test_email_string_format(self) -> None:
        """Handles email_addresses stored as plain strings."""
        agent = CommunicationAgent()

        row = MagicMock()
        row.full_name = "Bob Smith"
        row.email_addresses = ["bob@example.com"]
        row.organization = None
        row.relationship_type = None

        result = MagicMock()
        result.first.return_value = row
        _install_mock_session([result])

        contact = await agent._lookup_contact("Bob")

        assert contact is not None
        assert contact["email"] == "bob@example.com"
