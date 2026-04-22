"""Tests for finance API endpoints — /api/v1/finance/summary."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.finance import router

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


def _mock_db_for_summary(
    total_spent: float = 245.50,
    txn_rows: list[Any] | None = None,
    mtd_total: float = 500.0,
    mtd_count: int = 20,
    cat_rows: list[Any] | None = None,
    previous_summary: Any = None,
) -> AsyncMock:
    """Build a mock DB session returning results for the 5 queries in get_finance_summary."""
    session = AsyncMock()
    results: list[MagicMock] = []

    # 1. Total spent (scalar_one)
    r1 = MagicMock()
    r1.scalar_one.return_value = Decimal(str(total_spent))
    results.append(r1)

    # 2. Transaction rows (.all())
    r2 = MagicMock()
    r2.all.return_value = txn_rows or []
    results.append(r2)

    # 3. MTD (total, count) via .one()
    r3 = MagicMock()
    mtd_row = (Decimal(str(mtd_total)), mtd_count)
    r3.one.return_value = mtd_row
    results.append(r3)

    # 4. Category breakdown (.all())
    r4 = MagicMock()
    r4.all.return_value = cat_rows or []
    results.append(r4)

    # 5. Previous period summary (scalar_one_or_none)
    r5 = MagicMock()
    r5.scalar_one_or_none.return_value = previous_summary
    results.append(r5)

    session.execute = AsyncMock(side_effect=results)
    return session


# ---------------------------------------------------------------------------
# Tests: GET /finance/summary
# ---------------------------------------------------------------------------


class TestGetFinanceSummary:
    @patch("src.api.auth.get_settings")
    def test_basic_summary(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(total_spent=245.50)

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get("/api/v1/finance/summary", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["period"] == "week"
        assert data["total_spent"] == 245.50
        assert "trends" in data
        assert "alerts" in data
        assert isinstance(data["transactions"], list)

    @patch("src.api.auth.get_settings")
    def test_day_period(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(total_spent=50.0)

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get(
            "/api/v1/finance/summary?period=day",
            headers=_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["period"] == "day"

    @patch("src.api.auth.get_settings")
    def test_month_period(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(total_spent=1500.0)

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get(
            "/api/v1/finance/summary?period=month",
            headers=_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["period"] == "month"

    @patch("src.api.auth.get_settings")
    def test_empty_transactions(self, mock_settings) -> None:
        """No transactions should return empty list, not crash."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(
            total_spent=0.0,
            txn_rows=[],
            mtd_total=0.0,
            mtd_count=0,
        )

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get("/api/v1/finance/summary", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["total_spent"] == 0.0
        assert data["transactions"] == []

    @patch("src.api.auth.get_settings")
    def test_trends_included_when_previous_exists(self, mock_settings) -> None:
        """When a previous period summary exists, trends should be populated."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )

        prev_summary = MagicMock()
        prev_summary.total_spent = Decimal("200.00")

        mock_session = _mock_db_for_summary(
            total_spent=250.0,
            previous_summary=prev_summary,
        )

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get("/api/v1/finance/summary", headers=_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert data["trends"] is not None
        assert data["trends"]["total_change_pct"] == 25.0
        assert data["trends"]["previous_total"] == 200.0

    @patch("src.api.auth.get_settings")
    def test_trends_null_when_no_previous(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(total_spent=245.50, previous_summary=None)

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get("/api/v1/finance/summary", headers=_HEADERS)

        assert response.status_code == 200
        assert response.json()["trends"] is None

    @patch("src.api.auth.get_settings")
    def test_high_daily_spending_alert(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        mock_session = _mock_db_for_summary(total_spent=300.0)

        app = _build_app()
        from src.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: mock_session

        client = TestClient(app)
        response = client.get(
            "/api/v1/finance/summary?period=day",
            headers=_HEADERS,
        )

        assert response.status_code == 200
        alerts = response.json()["alerts"]
        assert any("High daily spending" in a for a in alerts)

    @patch("src.api.auth.get_settings")
    def test_auth_required(self, mock_settings) -> None:
        """Requests without auth should be rejected."""
        mock_settings.return_value = MagicMock(
            tars_api_key="test-api-key",
            allowed_device_tokens="",
        )
        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/finance/summary")

        assert response.status_code == 401
