"""Tests for the HealthMonitorAgent."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.base import AgentContext
from agents.health_monitor import HealthMonitorAgent, _status_from_ms
from shared.constants import HealthStatus


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_context() -> AgentContext:
    return AgentContext(
        user_message="/health check",
        intent_type="health_monitor",
        source="scheduler",
    )


def _green(target: str) -> dict:
    return {
        "target": target,
        "status": HealthStatus.GREEN,
        "response_time_ms": 5,
        "details": {"connected": True},
    }


_NEW_CHECK_METHODS = (
    "_check_disk_usage",
    "_check_memory_usage",
    "_check_pg_pool",
    "_check_redis_memory",
    "_check_docker_containers",
)


def _patch_new_checks(stack: ExitStack, agent: HealthMonitorAgent) -> None:
    """Enter patches for the 5 new system checks, returning GREEN for each."""
    for name in _NEW_CHECK_METHODS:
        target = name.replace("_check_", "")
        stack.enter_context(patch.object(agent, name, return_value=_green(target)))


def _mock_settings() -> MagicMock:
    s = MagicMock()
    s.redis_url = "redis://localhost:6379/0"
    s.grafana_url = ""
    s.grafana_api_key = ""
    s.loki_url = ""
    return s


# ---------------------------------------------------------------------------
# _status_from_ms
# ---------------------------------------------------------------------------


class TestStatusFromMs:

    def test_green(self) -> None:
        assert _status_from_ms(50) == HealthStatus.GREEN

    def test_yellow(self) -> None:
        assert _status_from_ms(2500) == HealthStatus.YELLOW

    def test_red(self) -> None:
        assert _status_from_ms(6000) == HealthStatus.RED


# ---------------------------------------------------------------------------
# HealthMonitorAgent
# ---------------------------------------------------------------------------


class TestHealthMonitorAgent:

    def setup_method(self) -> None:
        self.agent = HealthMonitorAgent()

    def test_agent_type(self) -> None:
        assert self.agent.agent_type == "health_monitor"

    @pytest.mark.asyncio
    async def test_all_green(self) -> None:
        """When all checks pass, overall status is GREEN."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            mock_pg = stack.enter_context(patch.object(self.agent, "_check_postgres"))
            mock_redis = stack.enter_context(patch.object(self.agent, "_check_redis"))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            _patch_new_checks(stack, self.agent)

            mock_pg.return_value = _green("postgresql")
            mock_redis.return_value = _green("redis")

            result = await self.agent.execute(_make_context())

        assert result.success is True
        assert result.data["overall"] == HealthStatus.GREEN
        assert result.data["anomaly_count"] == 0
        assert "GREEN" in result.text

    @pytest.mark.asyncio
    async def test_red_triggers_alert_and_escalation(self) -> None:
        """When a check is RED, alerts fire and Claude is invoked."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            mock_pg = stack.enter_context(patch.object(self.agent, "_check_postgres"))
            mock_redis = stack.enter_context(patch.object(self.agent, "_check_redis"))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            mock_alert = stack.enter_context(patch.object(self.agent, "_send_alerts", new_callable=AsyncMock))
            mock_claude = stack.enter_context(patch.object(self.agent, "_escalate_to_claude", new_callable=AsyncMock))
            _patch_new_checks(stack, self.agent)

            mock_pg.return_value = _green("postgresql")
            mock_redis.return_value = {
                "target": "redis",
                "status": HealthStatus.RED,
                "response_time_ms": None,
                "details": {"connected": False, "error": "Connection refused"},
            }
            mock_claude.return_value = "Redis is unreachable. Check if the service is running on Node 1."

            result = await self.agent.execute(_make_context())

        assert result.success is True
        assert result.data["overall"] == HealthStatus.RED
        assert result.data["anomaly_count"] == 1
        assert result.data["diagnosis"] is not None
        assert "Redis" in result.data["diagnosis"]

        mock_alert.assert_awaited_once()
        mock_claude.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_yellow_triggers_alert_no_claude(self) -> None:
        """YELLOW anomalies trigger alerts but NOT Claude escalation."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            mock_pg = stack.enter_context(patch.object(self.agent, "_check_postgres"))
            mock_redis = stack.enter_context(patch.object(self.agent, "_check_redis"))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            mock_alert = stack.enter_context(patch.object(self.agent, "_send_alerts", new_callable=AsyncMock))
            mock_claude = stack.enter_context(patch.object(self.agent, "_escalate_to_claude", new_callable=AsyncMock))
            _patch_new_checks(stack, self.agent)

            mock_pg.return_value = {
                "target": "postgresql",
                "status": HealthStatus.YELLOW,
                "response_time_ms": 3000,
                "details": {"connected": True},
            }
            mock_redis.return_value = _green("redis")

            result = await self.agent.execute(_make_context())

        assert result.data["overall"] == HealthStatus.YELLOW
        assert result.data["anomaly_count"] == 1
        assert result.data["diagnosis"] is None

        mock_alert.assert_awaited_once()
        mock_claude.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_check_becomes_red(self) -> None:
        """If a check raises an exception, it becomes a RED entry."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            stack.enter_context(patch.object(self.agent, "_check_postgres", side_effect=RuntimeError("boom")))
            mock_redis = stack.enter_context(patch.object(self.agent, "_check_redis"))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            stack.enter_context(patch.object(self.agent, "_send_alerts", new_callable=AsyncMock))
            stack.enter_context(patch.object(self.agent, "_escalate_to_claude", new_callable=AsyncMock, return_value=None))
            _patch_new_checks(stack, self.agent)

            mock_redis.return_value = _green("redis")

            result = await self.agent.execute(_make_context())

        pg_check = next(c for c in result.data["checks"] if c["target"] == "postgresql")
        assert pg_check["status"] == HealthStatus.RED
        assert "boom" in pg_check["details"]["error"]

    @pytest.mark.asyncio
    async def test_unconfigured_grafana_loki_are_unknown(self) -> None:
        """Grafana/Loki return UNKNOWN when not configured — not RED."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            mock_pg = stack.enter_context(patch.object(self.agent, "_check_postgres"))
            mock_redis = stack.enter_context(patch.object(self.agent, "_check_redis"))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            _patch_new_checks(stack, self.agent)

            mock_pg.return_value = _green("postgresql")
            mock_redis.return_value = _green("redis")

            result = await self.agent.execute(_make_context())

        grafana_check = next(c for c in result.data["checks"] if c["target"] == "grafana")
        loki_check = next(c for c in result.data["checks"] if c["target"] == "loki_errors")
        assert grafana_check["status"] == HealthStatus.UNKNOWN
        assert loki_check["status"] == HealthStatus.UNKNOWN

        # UNKNOWN should not count as anomaly
        assert result.data["overall"] == HealthStatus.GREEN

    @pytest.mark.asyncio
    async def test_disk_usage_yellow_on_high_usage(self) -> None:
        """Disk usage check returns YELLOW when above threshold."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            stack.enter_context(patch.object(self.agent, "_check_postgres", return_value=_green("postgresql")))
            stack.enter_context(patch.object(self.agent, "_check_redis", return_value=_green("redis")))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            mock_alert = stack.enter_context(patch.object(self.agent, "_send_alerts", new_callable=AsyncMock))

            # Mock all new checks as green except disk
            for name in _NEW_CHECK_METHODS:
                if name == "_check_disk_usage":
                    continue
                target = name.replace("_check_", "")
                stack.enter_context(patch.object(self.agent, name, return_value=_green(target)))

            mock_disk = stack.enter_context(patch.object(self.agent, "_check_disk_usage"))
            mock_disk.return_value = {
                "target": "disk",
                "status": HealthStatus.YELLOW,
                "response_time_ms": None,
                "details": {"mounts": {"/": {"percent": 88}}, "max_percent": 88},
            }

            result = await self.agent.execute(_make_context())

        assert result.data["overall"] == HealthStatus.YELLOW
        disk_check = next(c for c in result.data["checks"] if c["target"] == "disk")
        assert disk_check["status"] == HealthStatus.YELLOW
        mock_alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_memory_usage_yellow_on_high_usage(self) -> None:
        """Memory usage check returns YELLOW when above threshold."""
        with ExitStack() as stack:
            stack.enter_context(patch("agents.health_monitor.get_settings", return_value=_mock_settings()))
            stack.enter_context(patch.object(self.agent, "_check_postgres", return_value=_green("postgresql")))
            stack.enter_context(patch.object(self.agent, "_check_redis", return_value=_green("redis")))
            stack.enter_context(patch.object(self.agent, "_store_results", new_callable=AsyncMock))
            mock_alert = stack.enter_context(patch.object(self.agent, "_send_alerts", new_callable=AsyncMock))

            # Mock all new checks as green except memory
            for name in _NEW_CHECK_METHODS:
                if name == "_check_memory_usage":
                    continue
                target = name.replace("_check_", "")
                stack.enter_context(patch.object(self.agent, name, return_value=_green(target)))

            mock_mem = stack.enter_context(patch.object(self.agent, "_check_memory_usage"))
            mock_mem.return_value = {
                "target": "memory",
                "status": HealthStatus.YELLOW,
                "response_time_ms": None,
                "details": {"percent": 92, "total_gb": 8, "used_gb": 7.4, "available_gb": 0.6},
            }

            result = await self.agent.execute(_make_context())

        assert result.data["overall"] == HealthStatus.YELLOW
        mem_check = next(c for c in result.data["checks"] if c["target"] == "memory")
        assert mem_check["status"] == HealthStatus.YELLOW
        mock_alert.assert_awaited_once()
