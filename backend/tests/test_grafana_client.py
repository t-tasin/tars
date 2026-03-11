"""Tests for the Grafana/Loki integration client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from integrations.grafana_client import GrafanaClient


# ---------------------------------------------------------------------------
# Unconfigured (graceful degradation)
# ---------------------------------------------------------------------------


class TestGrafanaUnconfigured:
    """When URLs are empty, every method returns empty/unconfigured."""

    def setup_method(self) -> None:
        self.client = GrafanaClient(
            grafana_url="",
            grafana_api_key="",
            loki_url="",
        )

    @pytest.mark.asyncio
    async def test_health_check_returns_unconfigured(self) -> None:
        result = await self.client.health_check()
        assert result["status"] == "unconfigured"

    @pytest.mark.asyncio
    async def test_get_alerts_returns_empty(self) -> None:
        result = await self.client.get_alerts()
        assert result == []

    @pytest.mark.asyncio
    async def test_query_logs_returns_empty(self) -> None:
        result = await self.client.query_logs('{job="tars"}')
        assert result == []

    @pytest.mark.asyncio
    async def test_count_errors_returns_zero(self) -> None:
        count = await self.client.count_errors()
        assert count == 0

    @pytest.mark.asyncio
    async def test_dashboard_returns_unconfigured(self) -> None:
        result = await self.client.get_dashboard_status("abc")
        assert result["status"] == "unconfigured"

    def test_configured_properties(self) -> None:
        assert self.client.grafana_configured is False
        assert self.client.loki_configured is False


# ---------------------------------------------------------------------------
# Configured (mocked HTTP)
# ---------------------------------------------------------------------------


class TestGrafanaConfigured:

    def setup_method(self) -> None:
        self.client = GrafanaClient(
            grafana_url="http://grafana:3000",
            grafana_api_key="test-key",
            loki_url="http://loki:3100",
        )

    def test_configured_properties(self) -> None:
        assert self.client.grafana_configured is True
        assert self.client.loki_configured is True

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        mock_resp = httpx.Response(
            200,
            json={"database": "ok", "version": "10.0"},
            request=httpx.Request("GET", "http://grafana:3000/api/health"),
        )

        self.client._grafana_http = AsyncMock()
        self.client._grafana_http.get = AsyncMock(return_value=mock_resp)

        result = await self.client.health_check()
        assert result["database"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        self.client._grafana_http = AsyncMock()
        self.client._grafana_http.get = AsyncMock(
            side_effect=httpx.ConnectError("refused"),
        )

        result = await self.client.health_check()
        assert result["status"] == "error"
        assert "refused" in result["error"]

    @pytest.mark.asyncio
    async def test_get_alerts_success(self) -> None:
        mock_resp = httpx.Response(
            200,
            json=[
                {"title": "High CPU", "provenance": "firing", "labels": {"severity": "critical"}, "folderUID": "f1", "uid": "r1"},
            ],
            request=httpx.Request("GET", "http://grafana:3000/api/v1/provisioning/alert-rules"),
        )

        self.client._grafana_http = AsyncMock()
        self.client._grafana_http.get = AsyncMock(return_value=mock_resp)

        alerts = await self.client.get_alerts()
        assert len(alerts) == 1
        assert alerts[0]["name"] == "High CPU"

    @pytest.mark.asyncio
    async def test_query_logs_success(self) -> None:
        mock_resp = httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "stream": {"job": "tars-backend"},
                            "values": [
                                ["1710000000000000000", "ERROR something broke"],
                                ["1710000001000000000", "ERROR another thing"],
                            ],
                        },
                    ],
                },
            },
            request=httpx.Request("GET", "http://loki:3100/loki/api/v1/query_range"),
        )

        self.client._loki_http = AsyncMock()
        self.client._loki_http.get = AsyncMock(return_value=mock_resp)

        entries = await self.client.query_logs('{job="tars-backend"} |= "ERROR"')
        assert len(entries) == 2
        assert "ERROR" in entries[0]["line"]
        assert entries[0]["labels"]["job"] == "tars-backend"

    @pytest.mark.asyncio
    async def test_count_errors_counts_entries(self) -> None:
        mock_resp = httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "stream": {"job": "tars-backend"},
                            "values": [["1", "ERROR a"], ["2", "ERROR b"], ["3", "ERROR c"]],
                        },
                    ],
                },
            },
            request=httpx.Request("GET", "http://loki:3100/loki/api/v1/query_range"),
        )

        self.client._loki_http = AsyncMock()
        self.client._loki_http.get = AsyncMock(return_value=mock_resp)

        count = await self.client.count_errors()
        assert count == 3

    @pytest.mark.asyncio
    async def test_query_logs_failure_returns_empty(self) -> None:
        self.client._loki_http = AsyncMock()
        self.client._loki_http.get = AsyncMock(
            side_effect=httpx.ConnectError("refused"),
        )

        entries = await self.client.query_logs('{job="tars"}')
        assert entries == []

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        self.client._grafana_http = AsyncMock()
        self.client._loki_http = AsyncMock()

        await self.client.close()

        self.client._grafana_http.aclose.assert_awaited_once()
        self.client._loki_http.aclose.assert_awaited_once()
