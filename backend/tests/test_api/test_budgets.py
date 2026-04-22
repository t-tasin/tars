"""Tests for budget API endpoints — /api/v1/finance/budgets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.budgets import router

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_HEADERS = {"Authorization": "Bearer test-api-key"}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_budget(
    category: str = "dining",
    monthly_limit: float = 200.0,
    active: bool = True,
) -> MagicMock:
    """Build a mock Budget ORM object."""
    b = MagicMock()
    b.id = "budget-uuid-001"
    b.category = category
    b.monthly_limit = Decimal(str(monthly_limit))
    b.active = active
    return b


def _mock_budget_status(
    category: str = "dining",
    monthly_limit: float = 200.0,
    spent: float = 100.0,
    remaining: float = 100.0,
    percent_used: float = 50.0,
    days_remaining: int = 15,
    on_track: bool = True,
) -> dict[str, Any]:
    return {
        "category": category,
        "monthly_limit": monthly_limit,
        "spent": spent,
        "remaining": remaining,
        "percent_used": percent_used,
        "days_remaining": days_remaining,
        "on_track": on_track,
    }


# ---------------------------------------------------------------------------
# Tests: GET /finance/budgets
# ---------------------------------------------------------------------------


class TestListBudgets:
    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_list_budgets_empty(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.get_active = AsyncMock(return_value=[])
        mock_repo_cls.return_value = mock_repo
        mock_mtd.return_value = {}
        mock_compute.return_value = []

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/budgets", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["budgets"] == []

    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_list_budgets_with_data(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        budgets = [_mock_budget("dining"), _mock_budget("entertainment", 300.0)]
        mock_repo = AsyncMock()
        mock_repo.get_active = AsyncMock(return_value=budgets)
        mock_repo_cls.return_value = mock_repo

        mock_mtd.return_value = {"dining": 100.0, "entertainment": 50.0}
        mock_compute.return_value = [
            _mock_budget_status("dining", spent=100.0, percent_used=50.0),
            _mock_budget_status("entertainment", 300.0, 50.0, 250.0, 16.7, 15, True),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/budgets", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert len(data["budgets"]) == 2
        assert data["budgets"][0]["category"] == "dining"
        assert data["budgets"][0]["percent_used"] == 50.0


# ---------------------------------------------------------------------------
# Tests: GET /finance/budgets/status
# ---------------------------------------------------------------------------


class TestBudgetStatusSummary:
    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_status_endpoint(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.get_active = AsyncMock(return_value=[_mock_budget()])
        mock_repo_cls.return_value = mock_repo

        mock_mtd.return_value = {"dining": 178.0}
        mock_compute.return_value = [
            _mock_budget_status(spent=178.0, remaining=22.0, percent_used=89.0, on_track=False),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/budgets/status", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert len(data["budgets"]) == 1
        assert data["budgets"][0]["percent_used"] == 89.0
        assert data["budgets"][0]["on_track"] is False


# ---------------------------------------------------------------------------
# Tests: POST /finance/budgets
# ---------------------------------------------------------------------------


class TestCreateOrUpdateBudget:
    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_create_new_budget(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.get_by_category = AsyncMock(
            side_effect=[None, _mock_budget("groceries", 500.0)],
        )
        mock_repo.create = AsyncMock(return_value=_mock_budget("groceries", 500.0))
        mock_repo_cls.return_value = mock_repo

        mock_mtd.return_value = {}
        mock_compute.return_value = [
            _mock_budget_status("groceries", 500.0, 0.0, 500.0, 0.0, 15, True),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            "/api/v1/finance/budgets",
            json={"category": "groceries", "monthly_limit": 500.0},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "groceries"
        assert data["monthly_limit"] == 500.0
        assert data["spent"] == 0.0
        mock_repo.create.assert_awaited_once()

    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_update_existing_budget(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        existing = _mock_budget("dining", 200.0)
        mock_repo = AsyncMock()
        mock_repo.get_by_category = AsyncMock(return_value=existing)
        mock_repo.update_limit = AsyncMock(return_value=existing)
        mock_repo_cls.return_value = mock_repo

        mock_mtd.return_value = {"dining": 100.0}
        mock_compute.return_value = [
            _mock_budget_status("dining", 300.0, 100.0, 200.0, 33.3, 15, True),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.post(
            "/api/v1/finance/budgets",
            json={"category": "dining", "monthly_limit": 300.0},
            headers=_HEADERS,
        )

        assert response.status_code == 200
        mock_repo.update_limit.assert_awaited_once()

    @patch("src.api.auth.get_settings")
    def test_create_budget_invalid_limit(self, mock_settings) -> None:
        """monthly_limit must be > 0."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        app = _build_app()
        client = TestClient(app)
        response = client.post(
            "/api/v1/finance/budgets",
            json={"category": "dining", "monthly_limit": 0},
            headers=_HEADERS,
        )

        assert response.status_code == 422  # Pydantic validation error


# ---------------------------------------------------------------------------
# Tests: DELETE /finance/budgets/{category}
# ---------------------------------------------------------------------------


class TestDeactivateBudget:
    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    def test_deactivate_success(self, mock_repo_cls, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.deactivate_by_category = AsyncMock(return_value=True)
        mock_repo_cls.return_value = mock_repo

        app = _build_app()
        client = TestClient(app)
        response = client.delete("/api/v1/finance/budgets/dining", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deactivated"
        assert data["category"] == "dining"

    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    def test_deactivate_not_found(self, mock_repo_cls, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.deactivate_by_category = AsyncMock(return_value=False)
        mock_repo_cls.return_value = mock_repo

        app = _build_app()
        client = TestClient(app)
        response = client.delete("/api/v1/finance/budgets/nonexistent", headers=_HEADERS)

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Budget percentage scenarios
# ---------------------------------------------------------------------------


class TestBudgetPercentageScenarios:
    """Test various budget usage levels via compute_budget_statuses."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_budget_zero_spending(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        """0% usage — no spending at all."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.get_active = AsyncMock(return_value=[_mock_budget()])
        mock_repo_cls.return_value = mock_repo
        mock_mtd.return_value = {}
        mock_compute.return_value = [
            _mock_budget_status(spent=0.0, remaining=200.0, percent_used=0.0),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/budgets", headers=_HEADERS)

        assert response.status_code == 200
        assert response.json()["budgets"][0]["percent_used"] == 0.0
        assert response.json()["budgets"][0]["on_track"] is True

    @patch("src.api.auth.get_settings")
    @patch("src.api.budgets.BudgetRepository")
    @patch("src.api.budgets._mtd_spending_by_category", new_callable=AsyncMock)
    @patch("src.api.budgets.compute_budget_statuses")
    def test_budget_exceeded(
        self,
        mock_compute,
        mock_mtd,
        mock_repo_cls,
        mock_settings,
    ) -> None:
        """100%+ usage — over budget."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_repo = AsyncMock()
        mock_repo.get_active = AsyncMock(return_value=[_mock_budget()])
        mock_repo_cls.return_value = mock_repo
        mock_mtd.return_value = {"dining": 250.0}
        mock_compute.return_value = [
            _mock_budget_status(spent=250.0, remaining=0.0, percent_used=125.0, on_track=False),
        ]

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/budgets", headers=_HEADERS)

        assert response.status_code == 200
        assert response.json()["budgets"][0]["percent_used"] == 125.0
        assert response.json()["budgets"][0]["on_track"] is False


# ---------------------------------------------------------------------------
# Tests: compute_budget_statuses on_track calculation
# ---------------------------------------------------------------------------


class TestOnTrackCalculation:
    """Test the on_track projection math in utils.finance."""

    def test_on_track_at_half_month(self) -> None:
        """Spending under projected pace → on track."""
        from utils.finance import compute_budget_statuses

        budget = _mock_budget("dining", 300.0)
        # Day 15 of 31-day month: projected = (140/15)*31 = 289.3 < 300 → on track
        spending = {"dining": 140.0}
        today = date(2026, 3, 15)
        statuses = compute_budget_statuses([budget], spending, today)

        assert len(statuses) == 1
        assert statuses[0]["on_track"] is True

    def test_not_on_track_heavy_spending(self) -> None:
        """Heavy early spending → projected to exceed → not on track."""
        from utils.finance import compute_budget_statuses

        budget = _mock_budget("dining", 200.0)
        spending = {"dining": 180.0}
        # Day 10 of a 31-day month → projected = (180/10)*31 = 558
        today = date(2026, 3, 10)
        statuses = compute_budget_statuses([budget], spending, today)

        assert statuses[0]["on_track"] is False

    def test_zero_spending_always_on_track(self) -> None:
        """Zero spending → projected = 0 → on track."""
        from utils.finance import compute_budget_statuses

        budget = _mock_budget("dining", 200.0)
        spending = {}
        today = date(2026, 3, 10)
        statuses = compute_budget_statuses([budget], spending, today)

        assert statuses[0]["on_track"] is True
        assert statuses[0]["spent"] == 0.0

    def test_min_three_days_projection(self) -> None:
        """On day 1, projection uses min 3 days to avoid noisy projection."""
        from utils.finance import compute_budget_statuses

        budget = _mock_budget("dining", 300.0)
        spending = {"dining": 50.0}
        # Day 1 → days_elapsed=1, but projection uses max(1, 3)=3
        # projected = (50/3)*31 ≈ 516.67 > 300 → not on track
        today = date(2026, 3, 1)
        statuses = compute_budget_statuses([budget], spending, today)

        assert statuses[0]["on_track"] is False
