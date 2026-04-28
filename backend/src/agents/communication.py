"""Communication drafter agent — drafts emails and messages via Claude Code.

Always routes to Claude Code (complex reasoning needed for high-stakes
communication).  Every draft goes through the approval system (HC-01) —
no email or message is ever sent without explicit user approval.

Professor / advisor emails are escalated to tier 3.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from shared.constants import AutonomyClass
from sqlalchemy import String, func, or_, select, type_coerce

from agents.base import AgentContext, AgentResult, BaseAgent
from models.claude_spawner import ClaudeCodeSpawner, ClaudeSpawnError

log = structlog.get_logger()

# Patterns that indicate a professor / advisor recipient (tier 3).
_PROFESSOR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bprof(essor)?\.?\s", re.IGNORECASE),
    re.compile(r"\bdr\.?\s", re.IGNORECASE),
    re.compile(r"\badvisor\b", re.IGNORECASE),
    re.compile(r"\bfaculty\b", re.IGNORECASE),
    re.compile(r"\.edu\s*$", re.IGNORECASE),
]

_PROFESSOR_RELATIONSHIP_TYPES = frozenset(
    {
        "professor",
        "advisor",
        "faculty",
        "mentor",
    }
)

_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant drafting an email on behalf of Tasin. "
    "Write in Tasin's voice — professional yet warm, concise, and clear. "
    "Never add placeholder brackets like [Name] — use the actual values provided. "
    "Return ONLY the email body text, no subject line, no metadata."
)


class CommunicationAgent(BaseAgent):
    """Drafts emails and messages using Claude Code.

    Always requires approval before sending (HC-01).  Professor / advisor
    emails are routed to tier 3 escalation for extra review.
    """

    agent_type = "communication"
    autonomy_class = AutonomyClass.WRITE_WORLD

    def __init__(self) -> None:
        self._claude = ClaudeCodeSpawner()

    async def execute(self, context: AgentContext) -> AgentResult:
        """Draft an email or message using Claude Code."""
        user_message = context.user_message

        # 1. Parse the request to extract recipient hint, subject hint, intent
        parsed = _parse_request(user_message)
        recipient_hint = parsed["recipient"]
        subject_hint = parsed["subject"]
        intent = parsed["intent"]

        if not recipient_hint:
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="I need to know who you'd like me to write to. Could you specify the recipient?",
                error="no_recipient",
            )

        # 2. Look up recipient in contacts DB
        contact = await self._lookup_contact(recipient_hint)

        recipient_name = contact["full_name"] if contact else recipient_hint
        recipient_email = contact["email"] if contact else ""
        recipient_org = contact.get("organization", "") if contact else ""
        relationship_type = contact.get("relationship_type", "") if contact else ""

        if not recipient_email:
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text=(
                    f'I found a reference to "{recipient_hint}" but couldn\'t find '
                    "their email address. Could you provide it?"
                ),
                error="no_email",
            )

        # 3. Fetch past email history with this recipient
        past_emails = await self._fetch_email_history(recipient_email)

        # 4. Fetch tone preferences from config
        tone_prefs = context.config.get("tone_preferences", {})

        # 5. Build Claude prompt
        prompt = self._build_prompt(
            user_request=user_message,
            recipient_name=recipient_name,
            recipient_email=recipient_email,
            recipient_org=recipient_org,
            relationship_type=relationship_type,
            past_emails=past_emails,
            subject_hint=subject_hint,
            intent=intent,
            tone_prefs=tone_prefs,
        )

        # 6. Spawn Claude Code to generate draft
        try:
            result = await self._claude.execute(
                prompt=prompt,
                mcp_profile="communication",
                timeout=90,
                max_turns=3,
            )
        except ClaudeSpawnError:
            log.error("communication_claude_spawn_failed", exc_info=True)
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="I wasn't able to draft that message right now — Claude is unavailable. Please try again shortly.",
                error="claude_unavailable",
            )

        if not result.success or not result.text.strip():
            log.error(
                "communication_draft_failed",
                error=result.error,
                duration_ms=result.duration_ms,
            )
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="I had trouble drafting that message. Could you try rephrasing your request?",
                error=result.error or "empty_draft",
            )

        draft_body = result.text.strip()

        # Generate subject if none was provided
        subject = subject_hint or _generate_subject(intent, recipient_name, draft_body)

        # 7. Determine action type (tier 2 vs tier 3)
        action_type = self._get_action_type(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            relationship_type=relationship_type,
        )

        log.info(
            "communication_draft_ready",
            recipient=recipient_email,
            action_type=action_type,
            subject=subject,
            draft_len=len(draft_body),
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )

        # 8. Return as approval-required result (HC-01)
        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=f"I've drafted an email to {recipient_name}. Please review:",
            content_type="approval",
            has_side_effects=True,
            action_type=action_type,
            approval_title=f"Send email to {recipient_name}",
            preview={
                "to": recipient_email,
                "subject": subject,
                "body": draft_body,
                "recipient_name": recipient_name,
                "recipient_org": recipient_org,
            },
            data={
                "model": "claude_code",
                "duration_ms": result.duration_ms,
                "cost_usd": result.cost_usd,
            },
        )

    # ------------------------------------------------------------------
    # Contact lookup
    # ------------------------------------------------------------------

    async def _lookup_contact(self, hint: str) -> dict[str, Any] | None:
        """Search contacts DB by name or email for the given hint.

        Returns a dict with ``full_name``, ``email``, ``organization``,
        and ``relationship_type``, or ``None`` if no match.
        """
        from db.models import Contact
        from db.session import get_db_session

        hint_lower = hint.lower().strip()

        async with get_db_session() as session:
            # Try exact email match first, then fuzzy name match
            result = await session.execute(
                select(
                    Contact.full_name,
                    Contact.email_addresses,
                    Contact.organization,
                    Contact.relationship_type,
                )
                .where(
                    or_(
                        func.lower(Contact.full_name).contains(hint_lower),
                        type_coerce(Contact.email_addresses, String).ilike(f"%{hint_lower}%"),
                    )
                )
                .limit(1)
            )
            row = result.first()

        if row is None:
            log.info("communication_contact_not_found", hint=hint)
            return None

        # Extract the primary email from the JSONB array
        email_addresses: list[Any] = row.email_addresses or []
        primary_email = ""
        if email_addresses:
            first = email_addresses[0]
            if isinstance(first, dict):
                primary_email = first.get("address", first.get("email", ""))
            elif isinstance(first, str):
                primary_email = first

        log.info(
            "communication_contact_found",
            name=row.full_name,
            email=primary_email,
            org=row.organization,
        )

        return {
            "full_name": row.full_name,
            "email": primary_email,
            "organization": row.organization or "",
            "relationship_type": row.relationship_type or "",
        }

    # ------------------------------------------------------------------
    # Email history
    # ------------------------------------------------------------------

    async def _fetch_email_history(
        self,
        recipient_email: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch recent email classifications involving this recipient."""
        from db.models import EmailClassification
        from db.session import get_db_session

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    EmailClassification.from_address,
                    EmailClassification.from_name,
                    EmailClassification.subject,
                    EmailClassification.snippet,
                    EmailClassification.received_at,
                )
                .where(EmailClassification.from_address == recipient_email)
                .order_by(EmailClassification.received_at.desc())
                .limit(limit)
            )
            rows = result.all()

        return [
            {
                "from": row.from_name or row.from_address,
                "subject": row.subject or "(no subject)",
                "snippet": row.snippet or "",
                "date": row.received_at.isoformat() if row.received_at else "",
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        user_request: str,
        recipient_name: str,
        recipient_email: str,
        recipient_org: str,
        relationship_type: str,
        past_emails: list[dict[str, Any]],
        subject_hint: str,
        intent: str,
        tone_prefs: dict[str, Any],
    ) -> str:
        """Build the prompt for Claude Code to generate the email draft."""
        parts: list[str] = [_SYSTEM_PROMPT, ""]

        parts.append(f"## User's request\n{user_request}\n")

        parts.append("## Recipient context")
        parts.append(f"- Name: {recipient_name}")
        parts.append(f"- Email: {recipient_email}")
        if recipient_org:
            parts.append(f"- Organization: {recipient_org}")
        if relationship_type:
            parts.append(f"- Relationship: {relationship_type}")
        parts.append("")

        if past_emails:
            parts.append("## Recent correspondence (most recent first)")
            for email in past_emails:
                parts.append(f"- [{email['date']}] Subject: {email['subject']} — {email['snippet'][:150]}")
            parts.append("")

        if subject_hint:
            parts.append(f"## Suggested subject line\n{subject_hint}\n")

        if intent:
            parts.append(f"## Intent\n{intent}\n")

        # Tone preferences
        default_tone = "professional yet warm"
        tone = tone_prefs.get("email_tone", default_tone)
        formality = tone_prefs.get(
            "formality", "formal" if relationship_type in _PROFESSOR_RELATIONSHIP_TYPES else "professional"
        )
        parts.append(f"## Tone\n- Style: {tone}\n- Formality: {formality}\n")

        parts.append(
            "## Instructions\n"
            "Draft the email body. Do NOT include the subject line — only the body.\n"
            "Use an appropriate greeting and sign-off.\n"
            'Sign off as "Tasin" (or "Best, Tasin" for formal emails).\n'
            "Keep it concise — say what needs to be said, nothing more."
        )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Risk tier determination
    # ------------------------------------------------------------------

    def _get_action_type(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        relationship_type: str,
    ) -> str:
        """Determine the action type — professors get tier3_escalation."""
        # Check relationship type from contacts DB
        if relationship_type.lower() in _PROFESSOR_RELATIONSHIP_TYPES:
            return "email_professor"

        # Check name patterns
        combined = f"{recipient_name} {recipient_email}"
        for pattern in _PROFESSOR_PATTERNS:
            if pattern.search(combined):
                return "email_professor"

        return "send_email"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_request(user_message: str) -> dict[str, str]:
    """Extract recipient, subject, and intent from the user's message.

    Uses simple heuristic parsing — the AI model handles ambiguity.
    """
    recipient = ""
    subject = ""
    intent = ""

    msg_lower = user_message.lower()

    # Try to extract "to <recipient>" pattern
    to_match = re.search(
        r"\b(?:[Tt]o|[Ff]or|[Ww]ith|[Ee]mail(?:ing)?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        user_message,
    )
    if to_match:
        recipient = to_match.group(1).strip()

    # Try email address pattern
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", user_message)
    if email_match:
        recipient = recipient or email_match.group(0)

    # Try to extract "about <subject>" or "regarding <subject>"
    subj_match = re.search(
        r"\b(?:about|regarding|re|subject)\s+(.+?)(?:\.|$)",
        user_message,
        re.IGNORECASE,
    )
    if subj_match:
        subject = subj_match.group(1).strip()

    # Detect intent from keywords
    if any(w in msg_lower for w in ["follow up", "following up", "followup"]):
        intent = "follow_up"
    elif any(w in msg_lower for w in ["thank", "thanks", "grateful"]):
        intent = "thank_you"
    elif any(w in msg_lower for w in ["introduce", "introduction"]):
        intent = "introduction"
    elif any(w in msg_lower for w in ["apolog", "sorry"]):
        intent = "apology"
    elif any(w in msg_lower for w in ["schedule", "meeting", "availability"]):
        intent = "scheduling"
    elif any(w in msg_lower for w in ["ask", "question", "inquir"]):
        intent = "inquiry"
    elif any(w in msg_lower for w in ["reply", "respond", "response"]):
        intent = "reply"

    return {
        "recipient": recipient,
        "subject": subject,
        "intent": intent,
    }


def _generate_subject(intent: str, recipient_name: str, body: str) -> str:
    """Generate a reasonable subject line when none was provided.

    Keeps it short — the user can always edit via the approval preview.
    """
    intent_subjects: dict[str, str] = {
        "follow_up": "Following up",
        "thank_you": "Thank you",
        "introduction": "Introduction",
        "apology": "Apologies",
        "scheduling": "Meeting request",
        "inquiry": "Quick question",
        "reply": "Re:",
    }

    if intent and intent in intent_subjects:
        return intent_subjects[intent]

    # Fallback: use first ~8 words of the body
    words = body.split()[:8]
    if words:
        snippet = " ".join(words)
        if len(snippet) > 50:
            snippet = snippet[:47] + "..."
        return snippet

    return "Message"
