"""Public-stream event sanitizer (HC-13).

Public dashboard / SSE stream MUST NEVER leak PII. This module is the gate.
``sanitize_public_event`` is whitelist-driven (denylist is wrong here): if a
field is not explicitly allowed, it is dropped. Free-text fields are scrubbed
for known PII patterns (email, phone, paths, IP addresses, API keys), then
truncated to ``MAX_FREE_TEXT_LEN``. Identifier fields are replaced with
deterministic short hashes derived from ``PUBLIC_STREAM_HASH_SECRET``.

The sanitizer is intentionally conservative: when in doubt it returns
``None`` (drop the event entirely). HC-13 is a hard constraint.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
from typing import Any, Final

# Maximum length of any free-text field that may be emitted on the public
# stream. The cap is small because public-dashboard events are summaries,
# not transcripts.
MAX_FREE_TEXT_LEN: Final[int] = 120

# Whitelist of allowed top-level fields per event ``type``. Anything else
# is dropped silently. ``"_common"`` lists fields permitted on every type.
_ALLOWED_FIELDS: Final[dict[str, frozenset[str]]] = {
    "_common": frozenset({"type", "ts", "subtype"}),
    "agent_status": frozenset({"agent", "state", "duration_ms"}),
    "model_call": frozenset({"tier", "model", "tokens_in", "tokens_out", "duration_ms"}),
    "sensor_publish": frozenset({"sensor", "fresh"}),
    "approval": frozenset({"action_type", "decision"}),
    "heartbeat": frozenset({"node", "uptime_s"}),
    "summary": frozenset({"label", "count", "text"}),
}

# Fields that should be replaced with stable hashes rather than dropped.
# Map field name to the prefix used in the rendered hash.
_HASH_FIELDS: Final[dict[str, str]] = {
    "user_id": "user_",
    "actor": "actor_",
    "account_id": "acct_",
    "session_id": "sess_",
    "device_id": "dev_",
}

# Free-text fields that get pattern-redacted + truncated rather than hashed.
_FREE_TEXT_FIELDS: Final[frozenset[str]] = frozenset({"text", "label", "model", "agent", "sensor", "node", "subtype"})

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+\b")
# Phone numbers: US-style and international with optional separators.
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\w)")
# Filesystem paths under user homes.
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/[\w.\-]+(?:/[^\s\"']*)?")
# API keys: explicit prefixes first, then a long alphanum/underscore catchall.
_API_KEY_PREFIX_RE = re.compile(r"\b(?:sk-|gho_|ghp_|gha_|ghu_|ghs_|AKIA)[A-Za-z0-9_\-]{16,}\b")
_API_KEY_GENERIC_RE = re.compile(r"\b[A-Za-z0-9]{32,}\b")
# Plausible IPv4 token (validated separately to avoid eating version numbers).
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

_PII_REPLACEMENT: Final[str] = "[REDACTED]"


def _hash_secret() -> bytes:
    """Return the HMAC secret for identifier hashing.

    Uses ``PUBLIC_STREAM_HASH_SECRET`` env var; falls back to a fixed salt
    when unset (test/dev). Production deploys must set this.
    """
    secret = os.environ.get("PUBLIC_STREAM_HASH_SECRET", "")
    return secret.encode("utf-8") if secret else b"tars-public-stream-default-salt"


def _hash_identifier(value: str, prefix: str) -> str:
    """Return ``<prefix><hex8>`` HMAC-SHA256 hash of ``value``."""
    digest = hmac.new(_hash_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}{digest[:8]}"


def _is_private_or_tailscale_ip(token: str) -> bool:
    """True if ``token`` parses as an RFC1918 / loopback / Tailscale IPv4."""
    try:
        ip = ipaddress.IPv4Address(token)
    except (ipaddress.AddressValueError, ValueError):
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    # Tailscale CGNAT space: 100.64.0.0/10
    return ipaddress.IPv4Address("100.64.0.0") <= ip <= ipaddress.IPv4Address("100.127.255.255")


def _redact_ips(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_private_or_tailscale_ip(token):
            return _PII_REPLACEMENT
        return token

    return _IPV4_RE.sub(_sub, text)


def redact_text(text: str) -> str:
    """Apply every redaction pattern to ``text`` and truncate to the cap.

    Order matters: prefix-API-keys before generic API keys (so prefixes
    survive their stricter rule), email before phone (so phone digits in an
    email-looking token don't double-redact), then paths and IPs.
    """
    if not text:
        return text
    out = text
    out = _EMAIL_RE.sub(_PII_REPLACEMENT, out)
    out = _API_KEY_PREFIX_RE.sub(_PII_REPLACEMENT, out)
    out = _HOME_PATH_RE.sub(_PII_REPLACEMENT, out)
    out = _PHONE_RE.sub(_PII_REPLACEMENT, out)
    out = _redact_ips(out)
    out = _API_KEY_GENERIC_RE.sub(_PII_REPLACEMENT, out)
    if len(out) > MAX_FREE_TEXT_LEN:
        out = out[: MAX_FREE_TEXT_LEN - 1] + "…"
    return out


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def sanitize_public_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Sanitize ``event`` for the public SSE stream.

    Returns a new dict containing only whitelisted fields, with PII patterns
    redacted and identifiers replaced by stable hashes. Returns ``None`` if
    the event has no recognisable ``type`` or contains no permitted fields
    after sanitization (callers should drop ``None`` events).

    The function is pure and side-effect free (apart from reading the
    ``PUBLIC_STREAM_HASH_SECRET`` env var on each identifier hash).
    """
    if not isinstance(event, dict):
        return None

    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None

    type_allowed = _ALLOWED_FIELDS.get(event_type)
    if type_allowed is None:
        # Unknown event type -> drop. Whitelist, not denylist.
        return None

    allowed = _ALLOWED_FIELDS["_common"] | type_allowed

    out: dict[str, Any] = {"type": event_type}

    for key, value in event.items():
        if key == "type":
            continue
        if key in _HASH_FIELDS:
            if isinstance(value, str) and value:
                out[key] = _hash_identifier(value, _HASH_FIELDS[key])
            continue
        if key not in allowed:
            continue
        sanitised = _sanitize_value(key, value)
        if sanitised is None and value is not None:
            # Unsupported shape (list/dict) — drop the key entirely rather
            # than emitting a misleading null.
            continue
        out[key] = sanitised

    # Require at least one non-meta field to survive; otherwise drop.
    informative = [k for k in out if k not in {"type", "ts"}]
    if not informative:
        return None

    return out


def _sanitize_value(key: str, value: Any) -> Any:
    """Sanitize an individual field value based on its name."""
    if isinstance(value, str):
        if key in _FREE_TEXT_FIELDS or key == "ts":
            return redact_text(value)
        # Conservative default: redact strings even outside the explicit
        # free-text set so an attacker can't smuggle PII via, say, "state".
        return redact_text(value)
    if isinstance(value, bool | int | float):
        return value
    if value is None:
        return None
    # Lists / dicts are not part of any whitelist today -> drop.
    return None


__all__ = [
    "MAX_FREE_TEXT_LEN",
    "redact_text",
    "sanitize_public_event",
]
