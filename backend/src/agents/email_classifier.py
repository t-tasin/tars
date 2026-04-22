"""Email classification agent — classifies unread emails into 4 tiers."""

from __future__ import annotations

import fnmatch
from typing import Any

import structlog
from shared.constants import EmailTier, ModelName

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

_TIERS = [t.value for t in EmailTier]

_SYSTEM_PROMPT = (
    "You are an email classifier for a personal AI assistant. "
    "Classify this email into exactly one category: urgent, actionable, informational, noise.\n\n"
    "Definitions:\n"
    "- urgent: Requires immediate attention (deadlines, security alerts, time-sensitive requests from important contacts)\n"
    "- actionable: Requires a response or action but not immediately (meeting requests, questions, tasks)\n"
    "- informational: Worth reading but no action needed (newsletters you subscribed to, status updates, receipts)\n"
    "- noise: Not worth reading (spam, marketing, automated notifications)\n\n"
    "Consider the sender, subject, and content. Respond with ONLY the category name."
)


def _build_few_shot_block(corrections: list[dict[str, Any]]) -> str:
    """Build few-shot examples from recent user corrections."""
    if not corrections:
        return ""

    lines = ["\nHere are recent corrections the user made to improve accuracy:"]
    for c in corrections:
        lines.append(
            f"- From: {c.get('from_address', '?')}, "
            f'Subject: "{c.get("subject", "?")}" → '
            f"Correct tier: {c.get('corrected_tier', '?')}"
        )
    return "\n".join(lines)


def _check_contact_rules(
    from_address: str,
    email_contacts: dict[str, list[str]],
) -> EmailTier | None:
    """Check static contact-based rules before AI classification.

    Returns the tier if a rule matches, or None if no rule applies.
    The patterns in each list support fnmatch-style wildcards (e.g. ``*@stanford.edu``).
    """
    addr_lower = from_address.lower()

    for pattern in email_contacts.get("always_urgent", []):
        if fnmatch.fnmatch(addr_lower, pattern.lower()):
            return EmailTier.URGENT

    for pattern in email_contacts.get("always_actionable", []):
        if fnmatch.fnmatch(addr_lower, pattern.lower()):
            return EmailTier.ACTIONABLE

    for pattern in email_contacts.get("always_noise", []):
        if fnmatch.fnmatch(addr_lower, pattern.lower()):
            return EmailTier.NOISE

    return None


def _check_contact_priority(
    from_address: str,
    contacts: list[dict[str, Any]],
) -> EmailTier | None:
    """Check contacts DB for sender's priority_hint."""
    addr_lower = from_address.lower()
    for contact in contacts:
        for email in contact.get("email_addresses", []):
            if isinstance(email, str) and email.lower() == addr_lower:
                hint = contact.get("priority_hint", "")
                if hint == "urgent":
                    return EmailTier.URGENT
                if hint == "important":
                    return EmailTier.ACTIONABLE
                if hint == "low":
                    return EmailTier.NOISE
                return None
    return None


def _format_email_prompt(
    email: dict[str, Any],
    contact_info: dict[str, Any] | None,
) -> str:
    """Format a single email into a classification prompt."""
    parts = [
        f"From: {email.get('from_name', '')} <{email.get('from_address', '')}>",
        f"Subject: {email.get('subject', '(no subject)')}",
        f"Snippet: {email.get('snippet', '')}",
    ]
    if contact_info:
        parts.append(
            f"Contact info: {contact_info.get('full_name', '?')}, "
            f"relationship: {contact_info.get('relationship', 'unknown')}, "
            f"org: {contact_info.get('organization', 'unknown')}"
        )
    return "\n".join(parts)


class EmailClassifierAgent(BaseAgent):
    """Classify unread emails into 4 tiers using contact rules + Gemini Flash."""

    agent_type: str = "email_classifier"

    async def execute(self, context: AgentContext) -> AgentResult:
        """Classify new emails using contact rules and Gemini Flash.

        Expects ``context`` to contain:
        - ``config["email_contacts"]``: dict with always_urgent/always_actionable/always_noise lists
        - ``config["gemini_client"]``: a :class:`GeminiClient` instance
        - ``config["gmail_clients"]``: dict mapping account name → :class:`GmailClient`
        - ``db_context["recent_corrections"]``: list of recent user corrections
        - ``db_context["contacts"]``: list of contact dicts from the DB
        """
        gmail_clients: dict[str, Any] = context.config.get("gmail_clients", {})
        gemini: GeminiClient | None = context.config.get("gemini_client")
        email_contacts: dict[str, list[str]] = context.config.get("email_contacts", {})
        corrections: list[dict[str, Any]] = context.db_context.get("recent_corrections", [])
        contacts: list[dict[str, Any]] = context.db_context.get("contacts", [])

        if not gmail_clients:
            return AgentResult(
                success=False,
                text="No Gmail clients configured.",
                error="no_gmail_clients",
            )

        if not gemini:
            return AgentResult(
                success=False,
                text="Gemini client not available.",
                error="no_gemini_client",
            )

        # Fetch unread emails from all accounts
        all_emails: list[tuple[str, dict[str, Any]]] = []
        for account_name, client in gmail_clients.items():
            try:
                emails = await client.get_unread_emails(max_results=50, since_hours=12)
                for email in emails:
                    all_emails.append((account_name, email))
            except Exception:
                log.error("gmail_fetch_failed", account=account_name, exc_info=True)

        if not all_emails:
            return AgentResult(
                success=True,
                text="No new emails to classify.",
                data={"total": 0, "urgent": 0, "actionable": 0, "informational": 0, "noise": 0},
            )

        # Build few-shot block once
        few_shot = _build_few_shot_block(corrections[:20])

        # Classify each email
        classifications: list[dict[str, Any]] = []
        counts = {tier: 0 for tier in _TIERS}

        for account_name, email in all_emails:
            from_address = email.get("from_address", "")

            # 1. Check static contact rules
            tier = _check_contact_rules(from_address, email_contacts)
            model_used = ModelName.LOCAL

            # 2. Check contacts DB priority_hint
            if tier is None:
                tier = _check_contact_priority(from_address, contacts)
                if tier is not None:
                    model_used = ModelName.LOCAL

            # 3. Fall back to Gemini Flash classification
            if tier is None:
                contact_info = _find_contact(from_address, contacts)
                prompt = _format_email_prompt(email, contact_info)
                if few_shot:
                    prompt = prompt + "\n" + few_shot

                try:
                    result_text = await gemini.classify(
                        text=prompt,
                        categories=_TIERS,
                        system_instruction=_SYSTEM_PROMPT,
                    )
                    tier = _parse_tier(result_text)
                    model_used = ModelName.GEMINI_FLASH
                except Exception:
                    log.error(
                        "gemini_classify_failed",
                        email_id=email.get("message_id"),
                        exc_info=True,
                    )
                    tier = EmailTier.INFORMATIONAL
                    model_used = ModelName.GEMINI_FLASH

            counts[tier.value] += 1
            classifications.append(
                {
                    "gmail_account": account_name,
                    "gmail_message_id": email.get("message_id", ""),
                    "from_address": from_address,
                    "from_name": email.get("from_name", ""),
                    "subject": email.get("subject", ""),
                    "snippet": email.get("snippet", ""),
                    "classified_tier": tier.value,
                    "model_used": model_used.value,
                }
            )

            log.info(
                "email_classified",
                email_id=email.get("message_id"),
                account=account_name,
                from_address=from_address,
                tier=tier.value,
                model=model_used.value,
            )

        total = len(classifications)
        summary = (
            f"Classified {total} email{'s' if total != 1 else ''}: "
            f"{counts['urgent']} urgent, {counts['actionable']} actionable, "
            f"{counts['informational']} informational, {counts['noise']} noise"
        )

        has_urgent = counts["urgent"] > 0

        return AgentResult(
            success=True,
            text=summary,
            data={
                "total": total,
                "classifications": classifications,
                "has_urgent": has_urgent,
                **counts,
            },
        )


def _find_contact(
    from_address: str,
    contacts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find matching contact for a given email address."""
    addr_lower = from_address.lower()
    for contact in contacts:
        for email in contact.get("email_addresses", []):
            if isinstance(email, str) and email.lower() == addr_lower:
                return contact
    return None


def _parse_tier(text: str) -> EmailTier:
    """Parse Gemini's classification response into an EmailTier."""
    normalized = text.strip().lower()
    try:
        return EmailTier(normalized)
    except ValueError:
        # Fuzzy match: check if any tier name is contained in the response
        for tier in EmailTier:
            if tier.value in normalized:
                return tier
        log.warning("tier_parse_fallback", raw=text)
        return EmailTier.INFORMATIONAL
