"""Tests for workout API endpoints."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.workout import router

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_HEADERS = {"Authorization": "Bearer test-api-key"}


def _build_app(mock_session: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    from src.dependencies import get_db

    if mock_session is not None:
        app.dependency_overrides[get_db] = lambda: mock_session

    return app


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------


def _make_split(
    split_id: uuid.UUID | None = None,
    name: str = "PPL",
) -> MagicMock:
    split = MagicMock()
    split.id = split_id or uuid.uuid4()
    split.name = name
    split.rotation_days = ["push", "pull", "legs"]
    split.active = True
    split.exercises = []
    return split


# ---------------------------------------------------------------------------
# Tests — Create Split
# ---------------------------------------------------------------------------


class TestCreateSplit:
    @patch("src.api.auth.get_settings")
    def test_create_split_returns_201(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_split = _make_split()
        mock_repo = MagicMock()
        mock_repo.create_split = AsyncMock(return_value=mock_split)

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch("src.api.workout.WorkoutRepository", return_value=mock_repo):
            app = _build_app(mock_session)
            client = TestClient(app)
            resp = client.post(
                "/api/v1/workout/splits",
                json={
                    "name": "PPL",
                    "rotation_days": ["push", "pull", "legs"],
                    "exercises": [
                        {
                            "day_name": "push",
                            "exercise_name": "Bench Press",
                            "target_sets": 3,
                            "target_reps": 10,
                            "current_weight": 135.0,
                        }
                    ],
                },
                headers=_HEADERS,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "PPL"
        assert data["active"] is True


# ---------------------------------------------------------------------------
# Tests — Get Active Split
# ---------------------------------------------------------------------------


class TestGetActiveSplit:
    @patch("src.api.auth.get_settings")
    def test_no_active_split_returns_404(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = MagicMock()
        mock_repo.get_active_split = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with patch("src.api.workout.WorkoutRepository", return_value=mock_repo):
            app = _build_app(mock_session)
            client = TestClient(app)
            resp = client.get("/api/v1/workout/splits/active", headers=_HEADERS)

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — Start Session
# ---------------------------------------------------------------------------


class TestStartSession:
    @patch("src.api.auth.get_settings")
    def test_start_nonexistent_session_returns_400(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = MagicMock()
        mock_repo.start_session = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with patch("src.api.workout.WorkoutRepository", return_value=mock_repo):
            app = _build_app(mock_session)
            client = TestClient(app)
            sid = uuid.uuid4()
            resp = client.post(f"/api/v1/workout/sessions/{sid}/start", headers=_HEADERS)

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests — Skip Session
# ---------------------------------------------------------------------------


class TestSkipSession:
    @patch("src.api.auth.get_settings")
    def test_skip_requires_reason(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = AsyncMock()
        app = _build_app(mock_session)
        client = TestClient(app)
        sid = uuid.uuid4()
        resp = client.post(
            f"/api/v1/workout/sessions/{sid}/skip",
            json={"reason": ""},
            headers=_HEADERS,
        )

        assert resp.status_code == 422  # validation error — empty reason


# ---------------------------------------------------------------------------
# Tests — Log Set
# ---------------------------------------------------------------------------


class TestLogSet:
    @patch("src.api.auth.get_settings")
    def test_log_set_not_found_returns_404(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = MagicMock()
        mock_repo.log_set = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with patch("src.api.workout.WorkoutRepository", return_value=mock_repo):
            app = _build_app(mock_session)
            client = TestClient(app)
            resp = client.post(
                "/api/v1/workout/logs",
                json={
                    "session_id": str(uuid.uuid4()),
                    "exercise_id": str(uuid.uuid4()),
                    "set_number": 1,
                    "actual_reps": 10,
                    "actual_weight": 135.0,
                },
                headers=_HEADERS,
            )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — Streak
# ---------------------------------------------------------------------------


class TestStreak:
    @patch("src.api.auth.get_settings")
    def test_streak_with_no_split_returns_zero(self, mock_settings):
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = MagicMock()
        mock_repo.get_active_split = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with patch("src.api.workout.WorkoutRepository", return_value=mock_repo):
            app = _build_app(mock_session)
            client = TestClient(app)
            resp = client.get("/api/v1/workout/streak", headers=_HEADERS)

        assert resp.status_code == 200
        assert resp.json()["streak"] == 0
