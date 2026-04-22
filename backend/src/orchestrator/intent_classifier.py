"""Rule-based intent classifier for T.A.R.S. — zero AI tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from shared.constants import IntentType, ModelName

log = structlog.get_logger()


@dataclass
class Intent:
    """Classification result for a user message."""

    agent: str
    action: str | None = None
    model_hint: str | None = None
    requires_vision: bool = False
    complexity: str = "normal"
    needs_docker_sandbox: bool = False
    entities: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exact slash-command mapping
# ---------------------------------------------------------------------------

_COMMAND_MAP: dict[str, Intent] = {
    "/briefing": Intent(agent=IntentType.BRIEFING),
    "/schedule": Intent(agent=IntentType.DAILY_LIFE, action="schedule"),
    "/status": Intent(agent=IntentType.HEALTH_MONITOR),
    "/jobs": Intent(agent=IntentType.JOB_SEARCH, action="show_digest"),
    "/jobs scan": Intent(agent=IntentType.JOB_SEARCH, action="full_scan"),
    "/config": Intent(agent=IntentType.CONFIG),
    "/outfit": Intent(agent=IntentType.FASHION, action="outfit"),
    "/deploy": Intent(agent=IntentType.SYSTEM, action="deploy"),
    "/help": Intent(agent=IntentType.GENERAL, action="help"),
    "/workout": Intent(agent=IntentType.WORKOUT_TRACKER),
}

# ---------------------------------------------------------------------------
# Keyword regex rules — checked in order, first match wins.
# Each entry: (compiled pattern, base Intent to return).
# ---------------------------------------------------------------------------

_INTENT_RULES: list[tuple[re.Pattern[str], Intent]] = [
    # fashion-shopping must come before generic product_research "buy"
    (
        re.compile(r"shop|outlet|store|buy\s*.*\s*clothes", re.IGNORECASE),
        Intent(agent=IntentType.FASHION, action="shopping_advisor"),
    ),
    (
        re.compile(r"schedule|calendar|event|appointment|meeting|remind", re.IGNORECASE),
        Intent(agent=IntentType.DAILY_LIFE),
    ),
    (
        re.compile(r"email|draft|write\s.*\sto\b|reply|respond", re.IGNORECASE),
        Intent(agent=IntentType.COMMUNICATION),
    ),
    (
        re.compile(r"job|career|apply|resume|cover[\s.]letter|interview", re.IGNORECASE),
        Intent(agent=IntentType.JOB_SEARCH),
    ),
    (
        re.compile(r"outfit|wear|clothes|wardrobe|fashion", re.IGNORECASE),
        Intent(agent=IntentType.FASHION),
    ),
    (
        re.compile(r"research|paper|thesis|study", re.IGNORECASE),
        Intent(agent=IntentType.RESEARCH),
    ),
    (
        re.compile(r"find|search|compare|buy|product|recommend|review", re.IGNORECASE),
        Intent(agent=IntentType.PRODUCT_RESEARCH),
    ),
    (
        re.compile(r"code|implement|fix|debug|deploy|PR\b|branch", re.IGNORECASE),
        Intent(agent=IntentType.CODING),
    ),
    (
        re.compile(r"atlasdesk|server|down|error|logs|grafana", re.IGNORECASE),
        Intent(agent=IntentType.HEALTH_MONITOR),
    ),
    (
        re.compile(r"spend|spent|budget|transaction|bank|money", re.IGNORECASE),
        Intent(agent=IntentType.FINANCE),
    ),
    (
        re.compile(
            r"set\s+\d|reps?\b.*\b(done|complete)|workout\s*(split|routine|log)?|gym|exercise|progressive\s+overload",
            re.IGNORECASE,
        ),
        Intent(agent=IntentType.WORKOUT_TRACKER),
    ),
    (
        re.compile(r"sleep|steps|health|fitness", re.IGNORECASE),
        Intent(agent=IntentType.HEALTH_FITNESS),
    ),
]

# Words that signal high complexity — triggers Claude escalation.
_COMPLEXITY_PATTERN = re.compile(
    r"complex|detailed|analyze|analysis|strategy|in[ -]depth|comprehensive",
    re.IGNORECASE,
)


class IntentClassifier:
    """Deterministic intent classifier. No AI calls, no latency, no cost."""

    def classify(
        self,
        message_text: str,
        source: str = "system",
        attachments: list[dict[str, Any]] | None = None,
    ) -> Intent:
        """Classify a user message into an Intent.

        Args:
            message_text: Raw text from the user.
            source: Channel the message arrived on (ios, telegram, …).
            attachments: Optional list of attachment dicts with a ``type`` key.

        Returns:
            An :class:`Intent` describing which agent should handle the message.
        """
        text = message_text.strip()
        attachments = attachments or []

        # 1. Exact slash-command match (try multi-word first, then single-word)
        if text.startswith("/"):
            text_lower = text.lower()
            # Try full command text (e.g. "/jobs scan")
            if text_lower in _COMMAND_MAP:
                intent = _copy_intent(_COMMAND_MAP[text_lower])
                _apply_modifiers(intent, text, attachments)
                log.debug("intent_classified", method="command", intent=intent.agent, command=text_lower)
                return intent
            # Try first two words (e.g. "/jobs scan" from "/jobs scan extra")
            words = text_lower.split()
            if len(words) >= 2:
                two_word = f"{words[0]} {words[1]}"
                if two_word in _COMMAND_MAP:
                    intent = _copy_intent(_COMMAND_MAP[two_word])
                    _apply_modifiers(intent, text, attachments)
                    log.debug("intent_classified", method="command", intent=intent.agent, command=two_word)
                    return intent
            # Try first word only (e.g. "/jobs")
            command = words[0] if words else None
            if command and command in _COMMAND_MAP:
                intent = _copy_intent(_COMMAND_MAP[command])
                _apply_modifiers(intent, text, attachments)
                log.debug("intent_classified", method="command", intent=intent.agent, command=command)
                return intent

        # 2. Keyword regex matching (first match wins)
        for pattern, base_intent in _INTENT_RULES:
            if pattern.search(text):
                intent = _copy_intent(base_intent)
                _apply_modifiers(intent, text, attachments)
                log.debug("intent_classified", method="keyword", intent=intent.agent, pattern=pattern.pattern)
                return intent

        # 3. Fallback
        intent = Intent(agent=IntentType.GENERAL, model_hint=ModelName.GEMINI_FLASH)
        _apply_modifiers(intent, text, attachments)
        log.debug("intent_classified", method="fallback", intent=intent.agent)
        return intent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_intent(src: Intent) -> Intent:
    """Shallow-copy an Intent so we don't mutate the template."""
    return Intent(
        agent=src.agent,
        action=src.action,
        model_hint=src.model_hint,
        requires_vision=src.requires_vision,
        complexity=src.complexity,
        needs_docker_sandbox=src.needs_docker_sandbox,
        entities=dict(src.entities),
    )


def _apply_modifiers(
    intent: Intent,
    text: str,
    attachments: list[dict[str, Any]],
) -> None:
    """Mutate *intent* in-place based on complexity hints and attachments."""
    # Complexity escalation
    if _COMPLEXITY_PATTERN.search(text):
        intent.complexity = "high"

    # Vision detection
    if any(att.get("type") == "image" for att in attachments):
        intent.requires_vision = True
