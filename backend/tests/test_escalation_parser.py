"""Tests for L1 self-escalation JSON protocol parser (P2-12)."""

from __future__ import annotations

import pytest
from shared.constants import ModelName

from orchestrator.escalation_parser import (
    EscalationRequest,
    parse_escalation,
)

# ---------------------------------------------------------------------------
# Happy path — recognised tier strings map to ModelName
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier_str", "expected_model"),
    [
        ("web", ModelName.GEMINI_FLASH),
        ("gemini_pro", ModelName.GEMINI_PRO),
        ("claude", ModelName.CLAUDE_CODE),
    ],
)
def test_parses_valid_escalation_json(tier_str: str, expected_model: ModelName) -> None:
    text = f'{{"escalate": "{tier_str}", "reason": "needs upstream"}}'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == expected_model
    assert result.reason == "needs upstream"


def test_parses_json_with_leading_whitespace() -> None:
    text = '   \n  {"escalate": "claude", "reason": "complex"}'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == ModelName.CLAUDE_CODE


def test_parses_json_inside_code_fence() -> None:
    text = '```json\n{"escalate": "web", "reason": "current data"}\n```'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == ModelName.GEMINI_FLASH
    assert result.reason == "current data"


def test_parses_json_inside_bare_code_fence() -> None:
    text = '```\n{"escalate": "gemini_pro", "reason": "long ctx"}\n```'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == ModelName.GEMINI_PRO


def test_parses_json_after_prose() -> None:
    text = 'Hmm, I don\'t have enough info. {"escalate": "claude", "reason": "needs reasoning"}'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == ModelName.CLAUDE_CODE


def test_handles_missing_reason_field() -> None:
    text = '{"escalate": "web"}'
    result = parse_escalation(text)
    assert result is not None
    assert result.target_model == ModelName.GEMINI_FLASH
    assert result.reason == ""


# ---------------------------------------------------------------------------
# Negative cases — return None
# ---------------------------------------------------------------------------


def test_returns_none_for_plain_text() -> None:
    assert parse_escalation("Here's my answer: 42.") is None


def test_returns_none_for_empty_string() -> None:
    assert parse_escalation("") is None


def test_returns_none_for_json_without_escalate_key() -> None:
    assert parse_escalation('{"answer": "42", "confidence": 0.9}') is None


def test_returns_none_for_unknown_tier() -> None:
    assert parse_escalation('{"escalate": "mars", "reason": "..."}') is None


def test_returns_none_for_malformed_json() -> None:
    assert parse_escalation("{escalate: claude, reason: oops}") is None


def test_returns_none_for_escalate_non_string() -> None:
    assert parse_escalation('{"escalate": true, "reason": "..."}') is None


def test_returns_none_for_json_array() -> None:
    assert parse_escalation('[{"escalate": "claude"}]') is None


def test_returns_none_when_text_is_just_brace() -> None:
    assert parse_escalation("{") is None


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


def test_escalation_request_is_frozen_dataclass() -> None:
    req = EscalationRequest(target_model=ModelName.CLAUDE_CODE, reason="x")
    with pytest.raises((AttributeError, Exception)):  # frozen=True raises FrozenInstanceError
        req.target_model = ModelName.GEMINI_FLASH  # type: ignore[misc]
