"""Test workout Pydantic schemas validate correctly."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from api.schemas import (
    CreateExerciseSchema,
    CreateSplitRequest,
    LogSetRequest,
    SkipSessionRequest,
)


def test_create_split_valid():
    req = CreateSplitRequest(
        name="PPL",
        rotation_days=["push", "pull", "legs", "rest"],
        exercises=[
            CreateExerciseSchema(
                day_name="push",
                exercise_name="Bench Press",
                target_sets=3,
                target_reps=10,
                current_weight=135.0,
            )
        ],
    )
    assert req.name == "PPL"
    assert len(req.exercises) == 1


def test_create_split_empty_name_fails():
    with pytest.raises(ValidationError):
        CreateSplitRequest(
            name="",
            rotation_days=["push"],
            exercises=[],
        )


def test_log_set_valid():
    req = LogSetRequest(
        session_id=uuid.uuid4(),
        exercise_id=uuid.uuid4(),
        set_number=1,
        actual_reps=10,
        actual_weight=135.0,
    )
    assert req.set_number == 1


def test_skip_session_empty_reason_fails():
    with pytest.raises(ValidationError):
        SkipSessionRequest(reason="")
