"""Tests for SignalDetector — keyword + intent-based escalation signal detection."""

from __future__ import annotations

import pytest
from shared.constants import IntentType

from orchestrator.intent_classifier import Intent
from orchestrator.signal_detector import EscalationSignal, SignalDetector


@pytest.fixture
def detector() -> SignalDetector:
    return SignalDetector()


# ---------------------------------------------------------------------------
# WEB_GROUNDING_NEEDED — current-info keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "what's the weather today",
    "any news right now",
    "latest stock price for AAPL",
    "current Bitcoin value",
    "this week in tech",
])
def test_web_grounding_fires_on_current_info_keywords(detector, message):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message=message, intent=intent)
    assert EscalationSignal.WEB_GROUNDING_NEEDED in signals


def test_web_grounding_does_not_fire_on_neutral_message(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message="hello", intent=intent)
    assert EscalationSignal.WEB_GROUNDING_NEEDED not in signals


# ---------------------------------------------------------------------------
# DEEP_RESEARCH — research intent + complexity high
# ---------------------------------------------------------------------------


def test_deep_research_fires_on_research_intent_with_high_complexity(detector):
    intent = Intent(agent=IntentType.RESEARCH, complexity="high")
    signals = detector.detect(message="comprehensive analysis of X", intent=intent)
    assert EscalationSignal.DEEP_RESEARCH in signals


def test_deep_research_does_not_fire_on_normal_complexity(detector):
    intent = Intent(agent=IntentType.RESEARCH, complexity="normal")
    signals = detector.detect(message="quick research summary", intent=intent)
    assert EscalationSignal.DEEP_RESEARCH not in signals


# ---------------------------------------------------------------------------
# IMAGE_GENERATION — keyword-based
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "generate an image of a sunset",
    "create a picture of my outfit",
    "draw me a logo",
    "make image of cat in space",
])
def test_image_generation_fires_on_keywords(detector, message):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message=message, intent=intent)
    assert EscalationSignal.IMAGE_GENERATION in signals


# ---------------------------------------------------------------------------
# IMAGE_UNDERSTANDING — image attachment without generation intent
# ---------------------------------------------------------------------------


def test_image_understanding_fires_on_image_attachment(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(
        message="what's in this photo",
        intent=intent,
        attachments=[{"type": "image", "url": "..."}],
    )
    assert EscalationSignal.IMAGE_UNDERSTANDING in signals


def test_image_understanding_yields_to_generation_when_both_match(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(
        message="generate an image like this one",
        intent=intent,
        attachments=[{"type": "image"}],
    )
    assert EscalationSignal.IMAGE_GENERATION in signals
    assert EscalationSignal.IMAGE_UNDERSTANDING not in signals


# ---------------------------------------------------------------------------
# OCR_DOCUMENT — pdf/document attachment
# ---------------------------------------------------------------------------


def test_ocr_fires_on_pdf_attachment(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(
        message="summarize this",
        intent=intent,
        attachments=[{"type": "pdf"}],
    )
    assert EscalationSignal.OCR_DOCUMENT in signals


# ---------------------------------------------------------------------------
# LONG_CONTEXT_REQUIRED — long messages
# ---------------------------------------------------------------------------


def test_long_context_fires_on_message_over_threshold(detector):
    intent = Intent(agent=IntentType.GENERAL)
    long_msg = "x" * 50_001
    signals = detector.detect(message=long_msg, intent=intent)
    assert EscalationSignal.LONG_CONTEXT_REQUIRED in signals


def test_long_context_does_not_fire_on_short_message(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message="short", intent=intent)
    assert EscalationSignal.LONG_CONTEXT_REQUIRED not in signals


# ---------------------------------------------------------------------------
# CRITICAL_DIAGNOSTIC — health_monitor + outage keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "atlasdesk is down",
    "production crash, please check",
    "we have an outage on the queue worker",
])
def test_critical_diagnostic_fires_on_health_intent_with_outage_keyword(detector, message):
    intent = Intent(agent=IntentType.HEALTH_MONITOR)
    signals = detector.detect(message=message, intent=intent)
    assert EscalationSignal.CRITICAL_DIAGNOSTIC in signals


def test_critical_diagnostic_does_not_fire_outside_health_intent(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message="server is down", intent=intent)
    assert EscalationSignal.CRITICAL_DIAGNOSTIC not in signals


# ---------------------------------------------------------------------------
# SERIOUS_DISCUSSION — explicit user override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "be serious for a moment",
    "this is important, no jokes",
    "for real, give me your honest opinion",
])
def test_serious_discussion_fires_on_explicit_override(detector, message):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message=message, intent=intent)
    assert EscalationSignal.SERIOUS_DISCUSSION in signals


# ---------------------------------------------------------------------------
# ARCHITECTURAL_CODE — coding intent + multi-file/architecture keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "refactor the auth layer across multiple files",
    "redesign the architecture of the orchestrator",
    "multi-file edit to swap the queue backend",
])
def test_architectural_code_fires_on_coding_intent_with_arch_keywords(detector, message):
    intent = Intent(agent=IntentType.CODING)
    signals = detector.detect(message=message, intent=intent)
    assert EscalationSignal.ARCHITECTURAL_CODE in signals


def test_architectural_code_does_not_fire_for_simple_coding(detector):
    intent = Intent(agent=IntentType.CODING)
    signals = detector.detect(message="fix typo in README", intent=intent)
    assert EscalationSignal.ARCHITECTURAL_CODE not in signals


# ---------------------------------------------------------------------------
# Empty/no signals
# ---------------------------------------------------------------------------


def test_no_signals_for_simple_local_message(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message="say hi", intent=intent)
    assert signals == set()


def test_detect_returns_set(detector):
    intent = Intent(agent=IntentType.GENERAL)
    signals = detector.detect(message="hi", intent=intent)
    assert isinstance(signals, set)


# ---------------------------------------------------------------------------
# Multi-signal compatibility — orthogonal signals can stack
# ---------------------------------------------------------------------------


def test_multiple_signals_can_fire_simultaneously(detector):
    intent = Intent(agent=IntentType.RESEARCH, complexity="high")
    signals = detector.detect(
        message="comprehensive deep research on latest AI developments today",
        intent=intent,
    )
    assert EscalationSignal.DEEP_RESEARCH in signals
    assert EscalationSignal.WEB_GROUNDING_NEEDED in signals
