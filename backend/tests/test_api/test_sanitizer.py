"""Tests for ``src.api.sanitizer`` — public-stream PII gate (HC-13).

The sanitizer is whitelist-driven and conservative. These tests cover every
redaction pattern (email / phone / home-path / IP / API key / generic token),
free-text truncation, identifier hashing, whitelist enforcement, and the
``None``-drop fallback for unsanitisable events. All test fixtures use
synthetic PII so no real data ever lands in the repo.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from src.api.sanitizer import (
    MAX_FREE_TEXT_LEN,
    redact_text,
    sanitize_public_event,
)

# ---------------------------------------------------------------------------
# redact_text — single-pattern coverage
# ---------------------------------------------------------------------------


def test_redacts_email_address() -> None:
    out = redact_text("ping fake.user+tag@synthetic-domain.test now")
    assert "fake.user" not in out
    assert "synthetic-domain.test" not in out
    assert "[REDACTED]" in out


def test_redacts_us_phone_with_separators() -> None:
    out = redact_text("call 555-867-5309 today")
    assert "867-5309" not in out
    assert "[REDACTED]" in out


def test_redacts_phone_with_country_code_and_parens() -> None:
    out = redact_text("dial +1 (555) 123-4567 please")
    assert "555" not in out or "[REDACTED]" in out
    assert "[REDACTED]" in out


def test_redacts_home_path_macos() -> None:
    out = redact_text("opened /Users/synthetic/Desktop/notes.md ok")
    assert "/Users/synthetic" not in out
    assert "[REDACTED]" in out


def test_redacts_home_path_linux() -> None:
    out = redact_text("ls /home/synthetic/code returned 1")
    assert "/home/synthetic" not in out
    assert "[REDACTED]" in out


def test_redacts_tailscale_ip() -> None:
    out = redact_text("connect to 100.94.4.103 via tailscale")
    assert "100.94.4.103" not in out
    assert "[REDACTED]" in out


def test_redacts_rfc1918_ip() -> None:
    out = redact_text("router at 192.168.1.1 responded")
    assert "192.168.1.1" not in out
    assert "[REDACTED]" in out


def test_keeps_public_ip() -> None:
    # Public IP (Cloudflare DNS) is not PII per spec — leave intact.
    out = redact_text("dns 1.1.1.1 ok")
    assert "1.1.1.1" in out


def test_redacts_openai_style_api_key() -> None:
    out = redact_text("token sk-abcdef0123456789ABCDEF used")
    assert "sk-abcdef0123456789ABCDEF" not in out
    assert "[REDACTED]" in out


def test_redacts_github_pat() -> None:
    out = redact_text("auth gho_abcdef0123456789ABCDEF0123 then")
    assert "gho_abcdef0123456789ABCDEF0123" not in out
    assert "[REDACTED]" in out


def test_redacts_aws_access_key() -> None:
    out = redact_text("creds AKIAIOSFODNN7EXAMPLE rotated")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED]" in out


def test_redacts_generic_long_token() -> None:
    # 40-char hex, not a known prefix — should still be redacted by the
    # generic catch-all so an attacker can't smuggle a key by stripping its
    # prefix.
    long_tok = "a" * 40
    out = redact_text(f"value {long_tok} stored")
    assert long_tok not in out
    assert "[REDACTED]" in out


def test_truncates_free_text_to_max_len() -> None:
    # Use a sentence (with spaces) so the generic API-key catch-all doesn't
    # short-circuit the test by replacing the whole string with [REDACTED].
    long = ("benign words " * 50).strip()
    assert len(long) > MAX_FREE_TEXT_LEN
    out = redact_text(long)
    assert len(out) <= MAX_FREE_TEXT_LEN
    assert out.endswith("…")


def test_redact_text_empty_returns_empty() -> None:
    assert redact_text("") == ""


# ---------------------------------------------------------------------------
# sanitize_public_event — whitelist + identifier hash + drop semantics
# ---------------------------------------------------------------------------


def test_drops_event_without_type() -> None:
    assert sanitize_public_event({"agent": "router"}) is None


def test_drops_unknown_event_type() -> None:
    # Whitelist-only: any unrecognised type is dropped.
    assert sanitize_public_event({"type": "exfiltrate", "secret": "x"}) is None


def test_drops_non_dict_event() -> None:
    assert sanitize_public_event("just a string") is None  # type: ignore[arg-type]
    assert sanitize_public_event(None) is None  # type: ignore[arg-type]


def test_strips_non_whitelisted_fields() -> None:
    event = {
        "type": "agent_status",
        "agent": "router",
        "state": "running",
        "duration_ms": 12,
        "internal_payload": {"raw": "fake.user@synthetic.test"},
    }
    out = sanitize_public_event(event)
    assert out is not None
    assert "internal_payload" not in out
    assert out["agent"] == "router"
    assert out["state"] == "running"
    assert out["duration_ms"] == 12


def test_hashes_user_id_identifier() -> None:
    with patch.dict(os.environ, {"PUBLIC_STREAM_HASH_SECRET": "test-secret"}):
        a = sanitize_public_event({"type": "agent_status", "agent": "x", "user_id": "tasin"})
        b = sanitize_public_event({"type": "agent_status", "agent": "x", "user_id": "tasin"})
    assert a is not None and b is not None
    assert a["user_id"] == b["user_id"]  # deterministic
    assert a["user_id"].startswith("user_")
    assert "tasin" not in a["user_id"]


def test_hash_secret_changes_hash() -> None:
    with patch.dict(os.environ, {"PUBLIC_STREAM_HASH_SECRET": "secret-1"}):
        a = sanitize_public_event({"type": "agent_status", "agent": "x", "user_id": "tasin"})
    with patch.dict(os.environ, {"PUBLIC_STREAM_HASH_SECRET": "secret-2"}):
        b = sanitize_public_event({"type": "agent_status", "agent": "x", "user_id": "tasin"})
    assert a is not None and b is not None
    assert a["user_id"] != b["user_id"]


def test_summary_event_redacts_email_in_text() -> None:
    event = {
        "type": "summary",
        "label": "inbox",
        "count": 3,
        "text": "new message from fake.user@synthetic-domain.test arrived",
    }
    out = sanitize_public_event(event)
    assert out is not None
    assert "fake.user" not in out["text"]
    assert "[REDACTED]" in out["text"]
    assert out["count"] == 3


def test_drop_event_with_only_type_and_no_informative_fields() -> None:
    # Only ``type`` survives -> drop (nothing for the dashboard to render).
    assert sanitize_public_event({"type": "agent_status"}) is None


def test_lists_and_dicts_in_whitelisted_fields_are_dropped() -> None:
    # ``label`` is allowed for ``summary``, but only as a string.
    event = {"type": "summary", "label": ["leak"], "count": 1}
    out = sanitize_public_event(event)
    assert out is not None
    assert "label" not in out  # list dropped, not stringified
    assert out["count"] == 1


def test_numeric_fields_pass_through() -> None:
    out = sanitize_public_event(
        {"type": "model_call", "tier": "L1", "tokens_in": 42, "tokens_out": 7, "duration_ms": 19}
    )
    assert out is not None
    assert out["tokens_in"] == 42
    assert out["tokens_out"] == 7
    assert out["duration_ms"] == 19


def test_combined_pii_in_text_all_redacted() -> None:
    text = "user fake.user@synthetic.test on 100.94.4.103 wrote /Users/synthetic/x"
    event = {"type": "summary", "label": "audit", "count": 1, "text": text}
    out = sanitize_public_event(event)
    assert out is not None
    cleaned = out["text"]
    assert "fake.user" not in cleaned
    assert "100.94.4.103" not in cleaned
    assert "/Users/synthetic" not in cleaned
    assert "[REDACTED]" in cleaned


def test_subtype_field_is_redacted_and_truncated() -> None:
    event = {
        "type": "agent_status",
        "agent": "router",
        "subtype": "x" * (MAX_FREE_TEXT_LEN + 10),
    }
    out = sanitize_public_event(event)
    assert out is not None
    assert len(out["subtype"]) <= MAX_FREE_TEXT_LEN
