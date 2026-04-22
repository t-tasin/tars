"""Tests for the /api/v1/health endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.health import router

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_psutil(cpu: float = 25.0, mem_percent: float = 55.0) -> MagicMock:
    mock = MagicMock()
    mock.cpu_percent.return_value = cpu
    mem = MagicMock()
    mem.percent = mem_percent
    mem.used = 4 * 1024 * 1024 * 1024  # 4 GB
    mem.total = 8 * 1024 * 1024 * 1024  # 8 GB
    mock.virtual_memory.return_value = mem
    return mock


def _mock_registry(statuses: dict[str, Any] | None = None) -> MagicMock:
    registry = MagicMock()
    if statuses is None:
        statuses = {}
    registry.get_all_statuses.return_value = statuses
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_all_healthy(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        mock_pg.return_value = {"status": "connected", "latency_ms": 2}
        mock_redis.return_value = {"status": "connected", "latency_ms": 3}

        psutil_mock = _mock_psutil()
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry()

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["tars"]["status"] == "green"
        assert data["tars"]["infrastructure"]["postgres"]["status"] == "connected"
        assert data["tars"]["infrastructure"]["redis"]["status"] == "connected"
        assert "node1" in data["tars"]

    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_postgres_down_returns_red(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        mock_pg.return_value = {"status": "disconnected", "latency_ms": 0, "error": "refused"}
        mock_redis.return_value = {"status": "connected", "latency_ms": 3}

        psutil_mock = _mock_psutil()
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry()

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["tars"]["status"] == "red"

    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_redis_down_returns_yellow(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        mock_pg.return_value = {"status": "connected", "latency_ms": 2}
        mock_redis.return_value = {"status": "disconnected", "latency_ms": 0}

        psutil_mock = _mock_psutil()
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry()

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["tars"]["status"] == "yellow"

    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_circuit_breaker_open_degrades_to_yellow(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        mock_pg.return_value = {"status": "connected", "latency_ms": 2}
        mock_redis.return_value = {"status": "connected", "latency_ms": 3}

        psutil_mock = _mock_psutil()
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry(
            {
                "gmail": {
                    "circuit_breaker": "open",
                    "recent_failures": 5,
                    "last_success": None,
                },
            }
        )

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["tars"]["status"] == "yellow"
        assert data["tars"]["services"]["gmail"]["status"] == "down"

    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_no_auth_required(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        """Health endpoint should work without authentication."""
        mock_pg.return_value = {"status": "connected", "latency_ms": 2}
        mock_redis.return_value = {"status": "connected", "latency_ms": 3}

        psutil_mock = _mock_psutil()
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry()

        app = _build_app()
        client = TestClient(app)
        # No auth headers
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    @patch("src.api.health.get_service_health_registry")
    @patch("src.api.health.psutil")
    @patch("src.api.health._check_redis")
    @patch("src.api.health._check_postgres")
    def test_system_resources_included(
        self,
        mock_pg,
        mock_redis,
        mock_psutil_mod,
        mock_registry_fn,
    ):
        mock_pg.return_value = {"status": "connected", "latency_ms": 2}
        mock_redis.return_value = {"status": "connected", "latency_ms": 3}

        psutil_mock = _mock_psutil(cpu=42.5, mem_percent=65.0)
        mock_psutil_mod.cpu_percent = psutil_mock.cpu_percent
        mock_psutil_mod.virtual_memory = psutil_mock.virtual_memory

        mock_registry_fn.return_value = _mock_registry()

        app = _build_app()
        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        node1 = response.json()["tars"]["node1"]
        assert node1["status"] == "running"
        assert node1["cpu_percent"] == 42.5
        assert node1["memory_percent"] == 65.0
        assert "uptime_seconds" in node1
