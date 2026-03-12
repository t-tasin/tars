"""Test that workout-related constants exist in shared.constants."""
from __future__ import annotations

from shared.constants import (
    AGENT_MODEL_MAP,
    IntentType,
    ModelName,
    WorkoutSessionStatus,
)


def test_workout_session_status_values():
    assert WorkoutSessionStatus.PENDING == "pending"
    assert WorkoutSessionStatus.ACTIVE == "active"
    assert WorkoutSessionStatus.COMPLETED == "completed"
    assert WorkoutSessionStatus.SKIPPED == "skipped"


def test_intent_type_has_workout_tracker():
    assert IntentType.WORKOUT_TRACKER == "workout_tracker"


def test_agent_model_map_includes_workout_tracker():
    assert IntentType.WORKOUT_TRACKER in AGENT_MODEL_MAP
    assert AGENT_MODEL_MAP[IntentType.WORKOUT_TRACKER] == ModelName.GEMINI_FLASH
