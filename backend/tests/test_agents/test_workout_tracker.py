"""Tests for the Workout Tracker agent."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from agents.workout_tracker import WorkoutTrackerAgent

# ---------------------------------------------------------------------------
# Progressive overload logic
# ---------------------------------------------------------------------------


class TestProgressiveOverloadLogic:
    def test_all_sets_hit_returns_true(self):
        """When all sets meet target reps and weight, should_progress returns True."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is True

    def test_one_set_missed_returns_false(self):
        """When any set misses target, should_progress returns False."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=8, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is False

    def test_unlogged_set_returns_false(self):
        """When a set has no actual data, should_progress returns False."""
        logs = [
            MagicMock(actual_reps=10, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
            MagicMock(actual_reps=None, actual_weight=None, target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is False

    def test_exceeded_reps_returns_true(self):
        """When actual reps exceed target, should_progress returns True."""
        logs = [
            MagicMock(actual_reps=12, actual_weight=Decimal("135"), target_reps=10, target_weight=Decimal("135")),
        ]
        assert WorkoutTrackerAgent._should_progress(logs) is True

    def test_empty_logs_returns_true(self):
        """Empty logs list (no sets) vacuously returns True."""
        assert WorkoutTrackerAgent._should_progress([]) is True


# ---------------------------------------------------------------------------
# Streak milestone messages
# ---------------------------------------------------------------------------


class TestStreakMessage:
    def test_milestone_message(self):
        msg = WorkoutTrackerAgent._streak_milestone_message(7)
        assert msg is not None
        assert "7" in msg

    def test_no_milestone(self):
        msg = WorkoutTrackerAgent._streak_milestone_message(3)
        assert msg is None

    def test_all_milestones(self):
        for n in (14, 30, 60, 90, 180, 365):
            msg = WorkoutTrackerAgent._streak_milestone_message(n)
            assert msg is not None
            assert str(n) in msg
