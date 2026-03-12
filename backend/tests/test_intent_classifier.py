"""Tests for the rule-based intent classifier."""

from __future__ import annotations

import pytest

from shared.constants import IntentType, ModelName
from src.orchestrator.intent_classifier import Intent, IntentClassifier


@pytest.fixture
def classifier() -> IntentClassifier:
    return IntentClassifier()


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

class TestSlashCommands:
    @pytest.mark.parametrize(
        "command, expected_agent, expected_action",
        [
            ("/briefing", IntentType.BRIEFING, None),
            ("/schedule", IntentType.DAILY_LIFE, "schedule"),
            ("/status", IntentType.HEALTH_MONITOR, None),
            ("/jobs", IntentType.JOB_SEARCH, "show_digest"),
            ("/config", IntentType.CONFIG, None),
            ("/outfit", IntentType.FASHION, "outfit"),
            ("/deploy", IntentType.SYSTEM, "deploy"),
            ("/help", IntentType.GENERAL, "help"),
        ],
    )
    def test_exact_commands(
        self,
        classifier: IntentClassifier,
        command: str,
        expected_agent: str,
        expected_action: str | None,
    ) -> None:
        intent = classifier.classify(command, "telegram")
        assert intent.agent == expected_agent
        assert intent.action == expected_action

    def test_command_with_trailing_text(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("/briefing today please", "ios")
        assert intent.agent == IntentType.BRIEFING


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

class TestKeywordMatching:
    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("What's on my schedule today?", IntentType.DAILY_LIFE),
            ("Add a calendar event for Friday", IntentType.DAILY_LIFE),
            ("Remind me about the appointment", IntentType.DAILY_LIFE),
            ("Set up a meeting with John", IntentType.DAILY_LIFE),
        ],
    )
    def test_daily_life(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("Draft an email to my professor", IntentType.COMMUNICATION),
            ("Reply to that thread", IntentType.COMMUNICATION),
            ("Write a message to John", IntentType.COMMUNICATION),
            ("Respond to the recruiter", IntentType.COMMUNICATION),
        ],
    )
    def test_communication(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("Find me new job listings", IntentType.JOB_SEARCH),
            ("Update my resume", IntentType.JOB_SEARCH),
            ("Help with my cover letter", IntentType.JOB_SEARCH),
            ("Prep for my interview tomorrow", IntentType.JOB_SEARCH),
            ("Apply to that position", IntentType.JOB_SEARCH),
        ],
    )
    def test_job_search(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("What should I wear today?", IntentType.FASHION),
            ("Suggest an outfit for dinner", IntentType.FASHION),
            ("Add this to my wardrobe", IntentType.FASHION),
        ],
    )
    def test_fashion(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    def test_fashion_shopping(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("I want to shop at the outlet store", "ios")
        assert intent.agent == IntentType.FASHION
        assert intent.action == "shopping_advisor"

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("Compare these two laptops", IntentType.PRODUCT_RESEARCH),
            ("Find me a good monitor", IntentType.PRODUCT_RESEARCH),
            ("Recommend a keyboard", IntentType.PRODUCT_RESEARCH),
        ],
    )
    def test_product_research(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("Implement the login feature", IntentType.CODING),
            ("Fix the bug in auth", IntentType.CODING),
            ("Debug the failing test", IntentType.CODING),
            ("Create a PR for this change", IntentType.CODING),
        ],
    )
    def test_coding(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("Research quantum computing", IntentType.RESEARCH),
            ("Find papers on reinforcement learning", IntentType.RESEARCH),
            ("Help with my thesis outline", IntentType.RESEARCH),
        ],
    )
    def test_research(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("AtlasDesk is showing errors", IntentType.HEALTH_MONITOR),
            ("Check the server logs", IntentType.HEALTH_MONITOR),
            ("Is grafana showing anything weird?", IntentType.HEALTH_MONITOR),
        ],
    )
    def test_health_monitor(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("How much did I spend this week?", IntentType.FINANCE),
            ("Show my budget", IntentType.FINANCE),
            ("What bank transactions came in?", IntentType.FINANCE),
        ],
    )
    def test_finance(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent

    @pytest.mark.parametrize(
        "message, expected_agent",
        [
            ("How was my sleep last night?", IntentType.HEALTH_FITNESS),
            ("Log my workout", IntentType.WORKOUT_TRACKER),
            ("How many steps today?", IntentType.HEALTH_FITNESS),
            ("Track my gym session", IntentType.WORKOUT_TRACKER),
        ],
    )
    def test_health_fitness(self, classifier: IntentClassifier, message: str, expected_agent: str) -> None:
        assert classifier.classify(message, "ios").agent == expected_agent


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unrecognised_message(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("Hello, how are you?", "telegram")
        assert intent.agent == IntentType.GENERAL
        assert intent.model_hint == ModelName.GEMINI_FLASH

    def test_empty_message(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("", "ios")
        assert intent.agent == IntentType.GENERAL


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------

class TestModifiers:
    def test_complexity_high(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("Give me a detailed analysis of my spending", "ios")
        assert intent.complexity == "high"

    def test_complexity_strategy(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("Help me build a strategy for job hunting", "ios")
        assert intent.complexity == "high"

    def test_complexity_normal_by_default(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("/briefing", "ios")
        assert intent.complexity == "normal"

    def test_vision_from_image_attachment(self, classifier: IntentClassifier) -> None:
        attachments = [{"type": "image", "data": "base64...", "mime_type": "image/jpeg"}]
        intent = classifier.classify("What is this?", "ios", attachments=attachments)
        assert intent.requires_vision is True

    def test_no_vision_without_image(self, classifier: IntentClassifier) -> None:
        intent = classifier.classify("What is this?", "ios", attachments=[])
        assert intent.requires_vision is False
