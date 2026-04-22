"""Test that workout ORM models are importable and have correct table names."""

from __future__ import annotations

from db.models import WorkoutExercise, WorkoutLog, WorkoutSession, WorkoutSplit


def test_workout_split_tablename():
    assert WorkoutSplit.__tablename__ == "workout_splits"


def test_workout_exercise_tablename():
    assert WorkoutExercise.__tablename__ == "workout_exercises"


def test_workout_session_tablename():
    assert WorkoutSession.__tablename__ == "workout_sessions"


def test_workout_log_tablename():
    assert WorkoutLog.__tablename__ == "workout_logs"


def test_workout_split_has_exercises_relationship():
    assert hasattr(WorkoutSplit, "exercises")


def test_workout_session_has_logs_relationship():
    assert hasattr(WorkoutSession, "logs")
