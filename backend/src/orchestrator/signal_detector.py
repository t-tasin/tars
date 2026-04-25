"""Detects EscalationSignals on incoming messages — drives local-vs-cloud routing."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

import structlog
from shared.constants import IntentType

from orchestrator.intent_classifier import Intent

log = structlog.get_logger()

LONG_CONTEXT_CHAR_THRESHOLD = 50_000


class EscalationSignal(StrEnum):
    """Signals that promote a request from local to cloud tiers.

    See ``docs/model_tiers.md`` for the routing table.
    """

    WEB_GROUNDING_NEEDED = "web_grounding"
    DEEP_RESEARCH = "deep_research"
    IMAGE_GENERATION = "image_gen"
    IMAGE_UNDERSTANDING = "image_in"
    OCR_DOCUMENT = "ocr"
    LONG_CONTEXT_REQUIRED = "long_ctx"
    CRITICAL_DIAGNOSTIC = "crit_diag"
    SERIOUS_DISCUSSION = "serious"
    ARCHITECTURAL_CODE = "arch_code"
    TIER3_ESCALATION = "tier3"
    LOCAL_LOW_CONFIDENCE = "uncertain"


_WEB_GROUNDING = re.compile(
    r"\b(today|right\s*now|latest|current|recent|news|this\s*(week|month)|happening)\b",
    re.IGNORECASE,
)
_IMAGE_GEN = re.compile(
    r"\b(generate|create|make|draw|render|design)\b(?:\s+\S+){0,3}?\s+(image|picture|photo|logo|illustration|drawing)\b",
    re.IGNORECASE,
)
_OUTAGE = re.compile(
    r"\b(down|crash(ed|ing)?|outage|broken|failing|errors?|degraded)\b",
    re.IGNORECASE,
)
_SERIOUS = re.compile(
    r"\b(be\s+serious|for\s+real|no\s+jokes?|honest(ly)?|this\s+is\s+important|important\s+matter)\b",
    re.IGNORECASE,
)
_ARCH = re.compile(
    r"\b(refactor|redesign|architecture|multi[\s-]*file|across\s+(multiple\s+)?files|system[\s-]*wide|migrate)\b",
    re.IGNORECASE,
)

_DEEP_RESEARCH_INTENTS = {
    IntentType.RESEARCH,
    IntentType.BRIEFING,
    IntentType.PRODUCT_RESEARCH,
}
_DOCUMENT_TYPES = {"pdf", "document", "doc", "docx"}
_IMAGE_TYPES = {"image", "photo", "jpeg", "png"}


class SignalDetector:
    """Pre-routing signal detector.

    Pure function over (message, intent, attachments). No I/O, no side effects,
    no AI calls. Returns the set of signals that the router should consider.
    """

    def detect(
        self,
        message: str,
        intent: Intent,
        attachments: list[dict[str, Any]] | None = None,
    ) -> set[EscalationSignal]:
        attachments = attachments or []
        signals: set[EscalationSignal] = set()

        if _WEB_GROUNDING.search(message):
            signals.add(EscalationSignal.WEB_GROUNDING_NEEDED)

        if intent.agent in _DEEP_RESEARCH_INTENTS and intent.complexity == "high":
            signals.add(EscalationSignal.DEEP_RESEARCH)

        is_image_gen = bool(_IMAGE_GEN.search(message)) or intent.action == "image_gen"
        has_image_attachment = any(att.get("type") in _IMAGE_TYPES for att in attachments)
        has_doc_attachment = any(att.get("type") in _DOCUMENT_TYPES for att in attachments)

        if is_image_gen:
            signals.add(EscalationSignal.IMAGE_GENERATION)
        elif has_image_attachment:
            signals.add(EscalationSignal.IMAGE_UNDERSTANDING)

        if has_doc_attachment:
            signals.add(EscalationSignal.OCR_DOCUMENT)

        if len(message) > LONG_CONTEXT_CHAR_THRESHOLD:
            signals.add(EscalationSignal.LONG_CONTEXT_REQUIRED)

        if intent.agent == IntentType.HEALTH_MONITOR and _OUTAGE.search(message):
            signals.add(EscalationSignal.CRITICAL_DIAGNOSTIC)

        if _SERIOUS.search(message):
            signals.add(EscalationSignal.SERIOUS_DISCUSSION)

        if intent.agent == IntentType.CODING and _ARCH.search(message):
            signals.add(EscalationSignal.ARCHITECTURAL_CODE)

        if signals:
            log.debug(
                "signals_detected",
                intent=intent.agent,
                signals=sorted(s.value for s in signals),
            )

        return signals
