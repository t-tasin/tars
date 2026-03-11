"""Tests for EmailClassifierAgent — Gmail and Gemini calls are mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext, AgentResult
from agents.email_classifier import (
    EmailClassifierAgent,
    _build_few_shot_block,
    _check_contact_priority,
    _check_contact_rules,
    _find_contact,
    _parse_tier,
)
from shared.constants import EmailTier, ModelName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_email(
    message_id: str = "msg-1",
    from_address: str = "alice@example.com",
    from_name: str = "Alice",
    subject: str = "Hello",
    snippet: str = "Preview text",
) -> dict:
    return {
        "message_id": message_id,
        "from_address": from_address,
        "from_name": from_name,
        "subject": subject,
        "snippet": snippet,
    }


def _make_context(
    gmail_clients: dict | None = None,
    gemini_client: MagicMock | None = None,
    email_contacts: dict | None = None,
    corrections: list | None = None,
    contacts: list | None = None,
) -> AgentContext:
    return AgentContext(
        user_message="check my email",
        intent_type="email_classifier",
        config={
            "gmail_clients": gmail_clients or {},
            "gemini_client": gemini_client,
            "email_contacts": email_contacts or {},
        },
        db_context={
            "recent_corrections": corrections or [],
            "contacts": contacts or [],
        },
    )


def _mock_gmail_client(emails: list[dict] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.get_unread_emails = AsyncMock(return_value=emails or [])
    return client


def _mock_gemini_client(classify_return: str = "informational") -> MagicMock:
    gemini = MagicMock()
    gemini.classify = AsyncMock(return_value=classify_return)
    return gemini


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestCheckContactRules:
    def test_matches_always_urgent(self):
        rules = {"always_urgent": ["boss@company.com", "*@stanford.edu"]}
        assert _check_contact_rules("boss@company.com", rules) == EmailTier.URGENT
        assert _check_contact_rules("prof@stanford.edu", rules) == EmailTier.URGENT

    def test_matches_always_actionable(self):
        rules = {"always_actionable": ["hr@company.com"]}
        assert _check_contact_rules("hr@company.com", rules) == EmailTier.ACTIONABLE

    def test_matches_always_noise(self):
        rules = {"always_noise": ["*@marketing.spam.com"]}
        assert _check_contact_rules("promo@marketing.spam.com", rules) == EmailTier.NOISE

    def test_no_match_returns_none(self):
        rules = {"always_urgent": ["boss@company.com"]}
        assert _check_contact_rules("unknown@example.com", rules) is None

    def test_case_insensitive(self):
        rules = {"always_urgent": ["Boss@Company.COM"]}
        assert _check_contact_rules("boss@company.com", rules) == EmailTier.URGENT

    def test_empty_rules(self):
        assert _check_contact_rules("anyone@example.com", {}) is None

    def test_priority_order_urgent_before_noise(self):
        rules = {
            "always_urgent": ["*@example.com"],
            "always_noise": ["*@example.com"],
        }
        assert _check_contact_rules("user@example.com", rules) == EmailTier.URGENT


class TestCheckContactPriority:
    def test_urgent_hint(self):
        contacts = [{"email_addresses": ["vip@example.com"], "priority_hint": "urgent"}]
        assert _check_contact_priority("vip@example.com", contacts) == EmailTier.URGENT

    def test_important_hint(self):
        contacts = [{"email_addresses": ["colleague@work.com"], "priority_hint": "important"}]
        assert _check_contact_priority("colleague@work.com", contacts) == EmailTier.ACTIONABLE

    def test_low_hint(self):
        contacts = [{"email_addresses": ["newsletter@list.com"], "priority_hint": "low"}]
        assert _check_contact_priority("newsletter@list.com", contacts) == EmailTier.NOISE

    def test_normal_hint_returns_none(self):
        contacts = [{"email_addresses": ["someone@example.com"], "priority_hint": "normal"}]
        assert _check_contact_priority("someone@example.com", contacts) is None

    def test_no_match_returns_none(self):
        contacts = [{"email_addresses": ["other@example.com"], "priority_hint": "urgent"}]
        assert _check_contact_priority("unknown@example.com", contacts) is None

    def test_case_insensitive_email(self):
        contacts = [{"email_addresses": ["VIP@Example.COM"], "priority_hint": "urgent"}]
        assert _check_contact_priority("vip@example.com", contacts) == EmailTier.URGENT


class TestParseTier:
    def test_exact_match(self):
        assert _parse_tier("urgent") == EmailTier.URGENT
        assert _parse_tier("actionable") == EmailTier.ACTIONABLE
        assert _parse_tier("informational") == EmailTier.INFORMATIONAL
        assert _parse_tier("noise") == EmailTier.NOISE

    def test_strips_whitespace(self):
        assert _parse_tier("  urgent  ") == EmailTier.URGENT

    def test_case_insensitive(self):
        assert _parse_tier("URGENT") == EmailTier.URGENT
        assert _parse_tier("Actionable") == EmailTier.ACTIONABLE

    def test_contained_match(self):
        assert _parse_tier("The category is urgent.") == EmailTier.URGENT

    def test_fallback_to_informational(self):
        assert _parse_tier("unknown garbage") == EmailTier.INFORMATIONAL


class TestBuildFewShotBlock:
    def test_empty_corrections(self):
        assert _build_few_shot_block([]) == ""

    def test_builds_block(self):
        corrections = [
            {"from_address": "a@b.com", "subject": "Hello", "corrected_tier": "urgent"},
            {"from_address": "c@d.com", "subject": "Sale!", "corrected_tier": "noise"},
        ]
        result = _build_few_shot_block(corrections)
        assert "a@b.com" in result
        assert "urgent" in result
        assert "noise" in result


class TestFindContact:
    def test_finds_matching_contact(self):
        contacts = [
            {"email_addresses": ["alice@example.com"], "full_name": "Alice"},
            {"email_addresses": ["bob@example.com"], "full_name": "Bob"},
        ]
        result = _find_contact("bob@example.com", contacts)
        assert result is not None
        assert result["full_name"] == "Bob"

    def test_returns_none_when_not_found(self):
        contacts = [{"email_addresses": ["alice@example.com"], "full_name": "Alice"}]
        assert _find_contact("unknown@example.com", contacts) is None

    def test_case_insensitive(self):
        contacts = [{"email_addresses": ["Alice@Example.COM"], "full_name": "Alice"}]
        result = _find_contact("alice@example.com", contacts)
        assert result is not None


# ---------------------------------------------------------------------------
# Agent execution tests
# ---------------------------------------------------------------------------


class TestEmailClassifierAgent:
    @pytest.fixture
    def agent(self) -> EmailClassifierAgent:
        return EmailClassifierAgent()

    async def test_no_gmail_clients(self, agent: EmailClassifierAgent):
        context = _make_context(gmail_clients={}, gemini_client=_mock_gemini_client())
        result = await agent.execute(context)

        assert not result.success
        assert result.error == "no_gmail_clients"

    async def test_no_gemini_client(self, agent: EmailClassifierAgent):
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client()},
            gemini_client=None,
        )
        result = await agent.execute(context)

        assert not result.success
        assert result.error == "no_gemini_client"

    async def test_no_unread_emails(self, agent: EmailClassifierAgent):
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails=[])},
            gemini_client=_mock_gemini_client(),
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["total"] == 0

    async def test_classifies_via_contact_rules(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="boss@company.com")]
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=_mock_gemini_client(),
            email_contacts={"always_urgent": ["boss@company.com"]},
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["total"] == 1
        assert result.data["urgent"] == 1
        assert result.data["classifications"][0]["classified_tier"] == "urgent"
        assert result.data["classifications"][0]["model_used"] == ModelName.LOCAL

    async def test_classifies_via_contact_priority(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="vip@example.com")]
        contacts = [{"email_addresses": ["vip@example.com"], "priority_hint": "urgent"}]
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=_mock_gemini_client(),
            contacts=contacts,
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["urgent"] == 1
        assert result.data["classifications"][0]["model_used"] == ModelName.LOCAL

    async def test_classifies_via_gemini(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="unknown@example.com")]
        gemini = _mock_gemini_client(classify_return="actionable")
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["actionable"] == 1
        assert result.data["classifications"][0]["model_used"] == ModelName.GEMINI_FLASH
        gemini.classify.assert_awaited_once()

    async def test_gemini_failure_falls_back_to_informational(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="unknown@example.com")]
        gemini = _mock_gemini_client()
        gemini.classify = AsyncMock(side_effect=Exception("API error"))
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["informational"] == 1

    async def test_multiple_accounts(self, agent: EmailClassifierAgent):
        personal_emails = [_make_email(message_id="p1", from_address="a@example.com")]
        professional_emails = [_make_email(message_id="w1", from_address="b@example.com")]
        gemini = _mock_gemini_client(classify_return="noise")

        context = _make_context(
            gmail_clients={
                "personal": _mock_gmail_client(personal_emails),
                "professional": _mock_gmail_client(professional_emails),
            },
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["total"] == 2
        accounts = {c["gmail_account"] for c in result.data["classifications"]}
        assert accounts == {"personal", "professional"}

    async def test_contact_rules_take_priority_over_gemini(self, agent: EmailClassifierAgent):
        """Emails matching contact rules should NOT call Gemini."""
        emails = [_make_email(from_address="boss@company.com")]
        gemini = _mock_gemini_client()
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
            email_contacts={"always_urgent": ["boss@company.com"]},
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["urgent"] == 1
        gemini.classify.assert_not_awaited()

    async def test_has_urgent_flag(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="boss@company.com")]
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=_mock_gemini_client(),
            email_contacts={"always_urgent": ["boss@company.com"]},
        )
        result = await agent.execute(context)

        assert result.data["has_urgent"] is True

    async def test_no_urgent_flag(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="newsletter@list.com")]
        gemini = _mock_gemini_client(classify_return="noise")
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert result.data["has_urgent"] is False

    async def test_corrections_included_in_prompt(self, agent: EmailClassifierAgent):
        emails = [_make_email(from_address="unknown@example.com")]
        gemini = _mock_gemini_client(classify_return="actionable")
        corrections = [
            {"from_address": "prev@example.com", "subject": "Old", "corrected_tier": "urgent"},
        ]
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
            corrections=corrections,
        )
        result = await agent.execute(context)

        # Verify the classify call included corrections in the prompt
        call_args = gemini.classify.call_args
        prompt_text = call_args.kwargs.get("text") or call_args.args[0]
        assert "prev@example.com" in prompt_text
        assert "urgent" in prompt_text

    async def test_summary_text_format(self, agent: EmailClassifierAgent):
        emails = [
            _make_email(message_id="1", from_address="a@b.com"),
            _make_email(message_id="2", from_address="c@d.com"),
        ]
        gemini = _mock_gemini_client(classify_return="noise")
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert "Classified 2 emails" in result.text
        assert "0 urgent" in result.text
        assert "2 noise" in result.text

    async def test_gmail_fetch_failure_continues(self, agent: EmailClassifierAgent):
        """If one account fails, the other should still be processed."""
        failing_client = AsyncMock()
        failing_client.get_unread_emails = AsyncMock(side_effect=Exception("OAuth expired"))

        working_emails = [_make_email(from_address="ok@example.com")]
        gemini = _mock_gemini_client(classify_return="informational")

        context = _make_context(
            gmail_clients={
                "personal": failing_client,
                "professional": _mock_gmail_client(working_emails),
            },
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        assert result.success
        assert result.data["total"] == 1

    async def test_classification_data_structure(self, agent: EmailClassifierAgent):
        emails = [_make_email(
            message_id="msg-42",
            from_address="test@example.com",
            from_name="Test User",
            subject="Test Subject",
            snippet="Test snippet",
        )]
        gemini = _mock_gemini_client(classify_return="actionable")
        context = _make_context(
            gmail_clients={"personal": _mock_gmail_client(emails)},
            gemini_client=gemini,
        )
        result = await agent.execute(context)

        c = result.data["classifications"][0]
        assert c["gmail_account"] == "personal"
        assert c["gmail_message_id"] == "msg-42"
        assert c["from_address"] == "test@example.com"
        assert c["from_name"] == "Test User"
        assert c["subject"] == "Test Subject"
        assert c["snippet"] == "Test snippet"
        assert c["classified_tier"] == "actionable"
        assert c["model_used"] == ModelName.GEMINI_FLASH
