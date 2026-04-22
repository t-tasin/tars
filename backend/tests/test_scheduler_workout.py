"""Test workout scheduler jobs exist and are callable."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from scheduler.jobs import create_daily_workout_session, workout_reminder_poll


class TestCreateDailyWorkoutSession:
    async def test_no_active_split_returns_gracefully(self):
        """Job function handles missing active split gracefully."""
        mock_orchestrator = AsyncMock()

        mock_repo = MagicMock()
        mock_repo.get_active_split = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with (
            patch("db.session.get_db_session") as mock_db,
            patch("db.repositories.workout.WorkoutRepository", return_value=mock_repo) as mock_repo_cls,
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            # Also patch inside the function's local import
            with patch.dict("sys.modules", {}):
                # Use a simpler approach - just call and let the lazy imports work
                pass

            await create_daily_workout_session(orchestrator=mock_orchestrator)

        # Verify it ran without errors


class TestWorkoutReminderPoll:
    async def test_no_pending_sessions_returns_gracefully(self):
        """Job function handles no pending sessions gracefully."""
        mock_orchestrator = AsyncMock()

        mock_repo = MagicMock()
        mock_repo.get_pending_sessions_past_schedule = AsyncMock(return_value=[])

        mock_session = AsyncMock()

        with (
            patch("db.session.get_db_session") as mock_db,
            patch("db.repositories.workout.WorkoutRepository", return_value=mock_repo),
        ):
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            await workout_reminder_poll(orchestrator=mock_orchestrator)
