"""Test intent classifier routes workout messages correctly."""
from __future__ import annotations

from orchestrator.intent_classifier import IntentClassifier
from shared.constants import IntentType


def test_set_done_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("set 1 done bench press 10 reps 135lbs")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_workout_split_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("show me my workout split")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_gym_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("I'm at the gym")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_exercise_routes_to_workout():
    ic = IntentClassifier()
    intent = ic.classify("what exercise is next")
    assert intent.agent == IntentType.WORKOUT_TRACKER


def test_sleep_still_routes_to_health():
    ic = IntentClassifier()
    intent = ic.classify("how did I sleep last night")
    assert intent.agent == IntentType.HEALTH_FITNESS


def test_steps_still_routes_to_health():
    ic = IntentClassifier()
    intent = ic.classify("how many steps today")
    assert intent.agent == IntentType.HEALTH_FITNESS


def test_slash_workout_command():
    ic = IntentClassifier()
    intent = ic.classify("/workout")
    assert intent.agent == IntentType.WORKOUT_TRACKER
