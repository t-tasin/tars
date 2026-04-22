"""Tests for the Q&A bank config: DEFAULT_CONFIG keys and seed logic."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from api.config_api import DEFAULT_CONFIG, seed_default_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


EXPECTED_JOB_APPLICATION_KEYS = [
    "full_name",
    "email",
    "phone",
    "location",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "years_of_experience",
    "current_title",
    "current_company",
    "education",
    "skills",
    "resume_path",
    "work_authorization",
    "visa_sponsorship_needed",
    "salary_expectation",
    "willing_to_relocate",
    "start_date",
    "why_looking",
    "preferred_role_types",
    "preferred_locations",
    "excluded_companies",
    "cover_letter_highlights",
    "research_interests",
]


# ---------------------------------------------------------------------------
# DEFAULT_CONFIG completeness
# ---------------------------------------------------------------------------


class TestDefaultConfigQABank:
    """Verify DEFAULT_CONFIG includes all job_application keys."""

    def test_default_config_includes_qa_bank(self) -> None:
        """Every expected Q&A bank key should exist under the job_application namespace."""
        job_app_keys = [key for (ns, key) in DEFAULT_CONFIG if ns == "job_application"]
        for expected_key in EXPECTED_JOB_APPLICATION_KEYS:
            assert expected_key in job_app_keys, f"Missing job_application key in DEFAULT_CONFIG: {expected_key}"

    def test_job_application_namespace_present(self) -> None:
        """The 'job_application' namespace must exist in DEFAULT_CONFIG."""
        namespaces = {ns for ns, _ in DEFAULT_CONFIG}
        assert "job_application" in namespaces


# ---------------------------------------------------------------------------
# seed_default_config
# ---------------------------------------------------------------------------


class TestSeedDefaultConfig:
    """Test the seed function against a mocked DB session."""

    @pytest.mark.asyncio
    async def test_seed_default_config_creates_namespace(self) -> None:
        """When the DB is empty, all DEFAULT_CONFIG keys should be inserted."""
        mock_session = AsyncMock()

        # ConfigRepository.get_all returns empty dict (no existing config)
        mock_repo_instance = AsyncMock()
        mock_repo_instance.get_all = AsyncMock(return_value={})
        mock_repo_instance.set = AsyncMock()

        with (
            patch("api.config_api.ConfigRepository", return_value=mock_repo_instance),
            patch("api.config_api.seed_default_budgets", new_callable=AsyncMock),
        ):
            await seed_default_config(mock_session)

        # Every key in DEFAULT_CONFIG should have been set
        assert mock_repo_instance.set.await_count == len(DEFAULT_CONFIG)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seed_default_config_skips_existing(self) -> None:
        """Pre-existing keys should not be overwritten during seeding."""
        mock_session = AsyncMock()

        # Simulate that job_application.full_name and general.location already exist
        existing_config: dict[str, dict[str, Any]] = {
            "job_application": {"full_name": "Tasin"},
            "general": {"location": "Wooster, OH"},
        }

        mock_repo_instance = AsyncMock()
        mock_repo_instance.get_all = AsyncMock(return_value=existing_config)
        mock_repo_instance.set = AsyncMock()

        with (
            patch("api.config_api.ConfigRepository", return_value=mock_repo_instance),
            patch("api.config_api.seed_default_budgets", new_callable=AsyncMock),
        ):
            await seed_default_config(mock_session)

        # Two keys should be skipped
        expected_inserts = len(DEFAULT_CONFIG) - 2
        assert mock_repo_instance.set.await_count == expected_inserts

    @pytest.mark.asyncio
    async def test_seed_no_commit_when_all_exist(self) -> None:
        """If every key already exists, no commit should occur."""
        mock_session = AsyncMock()

        # Build a complete existing config matching all DEFAULT_CONFIG keys
        existing_config: dict[str, dict[str, Any]] = {}
        for (ns, key), value in DEFAULT_CONFIG.items():
            existing_config.setdefault(ns, {})[key] = value

        mock_repo_instance = AsyncMock()
        mock_repo_instance.get_all = AsyncMock(return_value=existing_config)
        mock_repo_instance.set = AsyncMock()

        with (
            patch("api.config_api.ConfigRepository", return_value=mock_repo_instance),
            patch("api.config_api.seed_default_budgets", new_callable=AsyncMock),
        ):
            await seed_default_config(mock_session)

        mock_repo_instance.set.assert_not_awaited()
        mock_session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Config API endpoint (PUT /api/v1/config)
# ---------------------------------------------------------------------------


class TestConfigUpdateAPI:
    """Test the PUT /api/v1/config endpoint for job_application namespace."""

    @pytest.mark.asyncio
    async def test_config_update_api(self) -> None:
        """PUT /api/v1/config with a job_application key should return success."""
        # Import FastAPI TestClient and router
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api.config_api import router

        app = FastAPI()
        app.include_router(router)

        # Override dependencies
        mock_session = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.get = AsyncMock(return_value=None)  # no old value
        mock_repo.set = AsyncMock()

        async def override_get_db():
            yield mock_session

        async def override_verify():
            return {"authenticated": True}

        app.dependency_overrides = {}

        from src.api.auth import verify_api_key
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[verify_api_key] = override_verify

        with patch("api.config_api.ConfigRepository", return_value=mock_repo):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.put(
                    "/api/v1/config",
                    json={
                        "namespace": "job_application",
                        "key": "full_name",
                        "value": "Tasin Ahmed",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["namespace"] == "job_application"
        assert data["key"] == "full_name"
        assert data["value"] == "Tasin Ahmed"
