"""Tests for the workout repository (unit tests with mock session)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.repositories.workout import WorkoutRepository


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def repo(mock_session):
    return WorkoutRepository(mock_session)


class TestGetActiveSplit:
    async def test_returns_none_when_no_active(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_active_split()
        assert result is None


class TestCalculateStreak:
    async def test_empty_sessions_returns_zero(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        streak = await repo.calculate_streak(split_id=uuid.uuid4())
        assert streak == 0
