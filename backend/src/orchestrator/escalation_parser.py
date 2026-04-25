"""L1 self-escalation JSON protocol parser (P2-12).

The local L1 brain (Qwen3-8B) is given a system prompt that asks it to emit
a small JSON object when it cannot confidently answer:

    {"escalate": "<tier>", "reason": "<short explanation>"}

Recognised tier strings:

    - "web"        → L2 Gemini Flash (web grounding)
    - "gemini_pro" → L3 Gemini Pro   (long context / deep research)
    - "claude"     → L4 Claude       (reasoning / serious diagnostic)

The orchestrator parses every L1 response through ``parse_escalation``.
On a hit it reroutes the call to the requested upstream tier instead of
shipping the L1 reply. One hop max — the upstream response is never
re-parsed (see ``orchestrator.engine``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog
from shared.constants import ModelName

log = structlog.get_logger()

_TIER_MAP: dict[str, ModelName] = {
    "web": ModelName.GEMINI_FLASH,
    "gemini_pro": ModelName.GEMINI_PRO,
    "claude": ModelName.CLAUDE_CODE,
}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class EscalationRequest:
    """L1 has asked to hand off to an upstream tier."""

    target_model: ModelName
    reason: str


def parse_escalation(text: str) -> EscalationRequest | None:
    """Extract a self-escalation directive from an L1 response.

    Returns ``None`` when the text is not an escalation — i.e. the L1 reply
    should be delivered to the user as-is.
    """
    if not text:
        return None

    payload = _try_parse(text.strip())
    if payload is None:
        candidate = _extract_json_blob(text)
        if candidate is None:
            return None
        payload = _try_parse(candidate)

    if not isinstance(payload, dict):
        return None

    tier = payload.get("escalate")
    if not isinstance(tier, str):
        return None

    target = _TIER_MAP.get(tier)
    if target is None:
        log.warning("escalation_unknown_tier", tier=tier)
        return None

    reason_raw = payload.get("reason", "")
    reason = reason_raw if isinstance(reason_raw, str) else ""

    return EscalationRequest(target_model=target, reason=reason)


def _try_parse(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_json_blob(text: str) -> str | None:
    """Pull the JSON object out of an L1 reply, tolerating prose + code fences."""
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1).strip()

    obj = _OBJECT_RE.search(text)
    if obj:
        return obj.group(0)

    return None
