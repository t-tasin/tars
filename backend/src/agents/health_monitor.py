"""System health monitor agent for T.A.R.S.

Runs every 5 minutes via scheduler. Performs local health checks on
T.A.R.S. infrastructure (PostgreSQL, Redis, disk, memory, Docker
containers) and AtlasDesk services (via Grafana/Loki). Zero AI tokens
for routine checks — Claude is only invoked when an anomaly is
detected that requires diagnostic reasoning.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

import httpx
import psutil
import structlog
from shared.constants import HealthStatus
from sqlalchemy import text

from agents.base import AgentContext, AgentResult, BaseAgent
from config import get_settings
from db.session import get_db_session

log = structlog.get_logger("health_monitor")

# Thresholds for anomaly detection
_ERROR_COUNT_THRESHOLD = 10  # errors in 5m window → anomaly
_RESPONSE_TIME_WARN_MS = 2000  # response > 2s → yellow
_RESPONSE_TIME_CRIT_MS = 5000  # response > 5s → red
_DISK_WARN_PCT = 85  # alert at >85% disk usage
_MEMORY_WARN_PCT = 90  # alert at >90% memory usage
_REDIS_MEMORY_WARN_MB = 1800  # alert when Redis uses >1.8GB of 2GB limit


class HealthMonitorAgent(BaseAgent):
    """Check system health.  Local monitoring — zero AI tokens unless anomaly detected."""

    agent_type: str = "health_monitor"

    async def execute(self, context: AgentContext) -> AgentResult:
        """Run all health checks, log results, alert on anomalies."""
        settings = get_settings()
        start = time.monotonic()

        # Run all checks in parallel
        results = await asyncio.gather(
            self._check_postgres(),
            self._check_redis(settings.redis_url),
            self._check_grafana(settings),
            self._check_loki_errors(settings),
            self._check_disk_usage(),
            self._check_memory_usage(),
            self._check_pg_pool(),
            self._check_redis_memory(settings.redis_url),
            self._check_docker_containers(),
            return_exceptions=True,
        )

        checks: list[dict[str, Any]] = []
        check_names = [
            "postgresql",
            "redis",
            "grafana",
            "loki_errors",
            "disk",
            "memory",
            "pg_pool",
            "redis_memory",
            "docker",
        ]

        for name, result in zip(check_names, results):
            if isinstance(result, BaseException):
                checks.append(
                    {
                        "target": name,
                        "status": HealthStatus.RED,
                        "response_time_ms": None,
                        "details": {"error": str(result)},
                    }
                )
            else:
                checks.append(result)

        # Persist to system_health_log
        await self._store_results(checks)

        # Determine overall status
        statuses = [c["status"] for c in checks]
        if HealthStatus.RED in statuses:
            overall = HealthStatus.RED
        elif HealthStatus.YELLOW in statuses:
            overall = HealthStatus.YELLOW
        else:
            overall = HealthStatus.GREEN

        duration_ms = int((time.monotonic() - start) * 1000)

        # Alert if degraded
        anomalies = [c for c in checks if c["status"] in (HealthStatus.RED, HealthStatus.YELLOW)]

        if anomalies:
            await self._send_alerts(anomalies, overall)

        # Escalate to Claude only for RED anomalies that need diagnosis
        diagnosis = None
        red_checks = [c for c in checks if c["status"] == HealthStatus.RED]
        if red_checks:
            diagnosis = await self._escalate_to_claude(red_checks, settings)

        log.info(
            "health_check_completed",
            overall=overall,
            duration_ms=duration_ms,
            checks={c["target"]: c["status"] for c in checks},
            anomaly_count=len(anomalies),
        )

        # Build response text
        lines = [f"System Health: {overall.upper()}"]
        for c in checks:
            icon = {"green": "+", "yellow": "~", "red": "!", "unknown": "?"}.get(c["status"], "?")
            rt = f" ({c['response_time_ms']}ms)" if c.get("response_time_ms") else ""
            lines.append(f"  [{icon}] {c['target']}: {c['status']}{rt}")

        if diagnosis:
            lines.append(f"\nDiagnosis: {diagnosis}")

        return AgentResult(
            success=True,
            text="\n".join(lines),
            data={
                "overall": overall,
                "checks": checks,
                "anomaly_count": len(anomalies),
                "diagnosis": diagnosis,
                "duration_ms": duration_ms,
            },
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def _check_postgres(self) -> dict[str, Any]:
        """Check PostgreSQL connectivity and response time."""
        start = time.monotonic()
        try:
            async with get_db_session() as session:
                await session.execute(text("SELECT 1"))
            ms = int((time.monotonic() - start) * 1000)
            return {
                "target": "postgresql",
                "status": _status_from_ms(ms),
                "response_time_ms": ms,
                "details": {"connected": True},
            }
        except Exception as exc:
            ms = int((time.monotonic() - start) * 1000)
            log.error("health_check_postgres_failed", error=str(exc))
            return {
                "target": "postgresql",
                "status": HealthStatus.RED,
                "response_time_ms": ms,
                "details": {"connected": False, "error": str(exc)},
            }

    async def _check_redis(self, redis_url: str) -> dict[str, Any]:
        """Check Redis connectivity via a PING."""
        start = time.monotonic()
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(redis_url, socket_timeout=5)
            try:
                pong = await client.ping()
                ms = int((time.monotonic() - start) * 1000)
                info = await client.info(section="memory")
                return {
                    "target": "redis",
                    "status": _status_from_ms(ms) if pong else HealthStatus.RED,
                    "response_time_ms": ms,
                    "details": {
                        "connected": bool(pong),
                        "used_memory_human": info.get("used_memory_human", ""),
                        "used_memory_peak_human": info.get("used_memory_peak_human", ""),
                    },
                }
            finally:
                await client.aclose()
        except Exception as exc:
            ms = int((time.monotonic() - start) * 1000)
            log.error("health_check_redis_failed", error=str(exc))
            return {
                "target": "redis",
                "status": HealthStatus.RED,
                "response_time_ms": ms,
                "details": {"connected": False, "error": str(exc)},
            }

    async def _check_grafana(self, settings: Any) -> dict[str, Any]:
        """Check Grafana health if configured."""
        if not settings.grafana_url:
            return {
                "target": "grafana",
                "status": HealthStatus.UNKNOWN,
                "response_time_ms": None,
                "details": {"configured": False},
            }

        start = time.monotonic()
        try:
            from integrations.grafana_client import GrafanaClient

            client = GrafanaClient(
                grafana_url=settings.grafana_url,
                grafana_api_key=settings.grafana_api_key,
                loki_url="",
            )
            try:
                result = await client.health_check()
                ms = int((time.monotonic() - start) * 1000)
                ok = result.get("database") == "ok"
                return {
                    "target": "grafana",
                    "status": HealthStatus.GREEN if ok else HealthStatus.YELLOW,
                    "response_time_ms": ms,
                    "details": result,
                }
            finally:
                await client.close()
        except Exception as exc:
            ms = int((time.monotonic() - start) * 1000)
            log.error("health_check_grafana_failed", error=str(exc))
            return {
                "target": "grafana",
                "status": HealthStatus.RED,
                "response_time_ms": ms,
                "details": {"error": str(exc)},
            }

    async def _check_loki_errors(self, settings: Any) -> dict[str, Any]:
        """Check recent error count in Loki if configured."""
        if not settings.loki_url:
            return {
                "target": "loki_errors",
                "status": HealthStatus.UNKNOWN,
                "response_time_ms": None,
                "details": {"configured": False},
            }

        start = time.monotonic()
        try:
            from integrations.grafana_client import GrafanaClient

            client = GrafanaClient(
                grafana_url="",
                grafana_api_key="",
                loki_url=settings.loki_url,
            )
            try:
                error_count = await client.count_errors(
                    job="tars-backend",
                    since="5m",
                )
                ms = int((time.monotonic() - start) * 1000)

                if error_count >= _ERROR_COUNT_THRESHOLD:
                    status = HealthStatus.RED
                elif error_count > 0:
                    status = HealthStatus.YELLOW
                else:
                    status = HealthStatus.GREEN

                return {
                    "target": "loki_errors",
                    "status": status,
                    "response_time_ms": ms,
                    "details": {"error_count_5m": error_count},
                }
            finally:
                await client.close()
        except Exception as exc:
            ms = int((time.monotonic() - start) * 1000)
            log.error("health_check_loki_failed", error=str(exc))
            return {
                "target": "loki_errors",
                "status": HealthStatus.YELLOW,
                "response_time_ms": ms,
                "details": {"error": str(exc)},
            }

    # ------------------------------------------------------------------
    # System resource checks
    # ------------------------------------------------------------------

    async def _check_disk_usage(self) -> dict[str, Any]:
        """Check disk usage on key mount points."""

        def _get_disk():
            results = {}
            for path in ["/", "/data"]:
                try:
                    usage = shutil.disk_usage(path)
                    pct = round((usage.used / usage.total) * 100, 1)
                    results[path] = {
                        "total_gb": round(usage.total / (1024**3), 1),
                        "used_gb": round(usage.used / (1024**3), 1),
                        "free_gb": round(usage.free / (1024**3), 1),
                        "percent": pct,
                    }
                except OSError:
                    pass
            return results

        disk = await asyncio.to_thread(_get_disk)
        max_pct = max((d["percent"] for d in disk.values()), default=0)

        if max_pct > _DISK_WARN_PCT:
            status = HealthStatus.RED if max_pct > 95 else HealthStatus.YELLOW
        else:
            status = HealthStatus.GREEN

        return {
            "target": "disk",
            "status": status,
            "response_time_ms": None,
            "details": {"mounts": disk, "max_percent": max_pct},
        }

    async def _check_memory_usage(self) -> dict[str, Any]:
        """Check system memory usage."""

        def _get_memory():
            mem = psutil.virtual_memory()
            return {
                "total_gb": round(mem.total / (1024**3), 1),
                "used_gb": round(mem.used / (1024**3), 1),
                "available_gb": round(mem.available / (1024**3), 1),
                "percent": mem.percent,
            }

        mem = await asyncio.to_thread(_get_memory)

        if mem["percent"] > _MEMORY_WARN_PCT:
            status = HealthStatus.RED if mem["percent"] > 95 else HealthStatus.YELLOW
        else:
            status = HealthStatus.GREEN

        return {
            "target": "memory",
            "status": status,
            "response_time_ms": None,
            "details": mem,
        }

    async def _check_pg_pool(self) -> dict[str, Any]:
        """Check PostgreSQL connection pool statistics."""
        start = time.monotonic()
        try:
            async with get_db_session() as session:
                result = await session.execute(
                    text(
                        "SELECT numbackends, xact_commit, xact_rollback, conflicts, deadlocks "
                        "FROM pg_stat_database WHERE datname = current_database()"
                    )
                )
                row = result.one_or_none()
                ms = int((time.monotonic() - start) * 1000)

                if row is None:
                    return {
                        "target": "pg_pool",
                        "status": HealthStatus.YELLOW,
                        "response_time_ms": ms,
                        "details": {"error": "no stats available"},
                    }

                stats = {
                    "active_connections": row.numbackends,
                    "commits": row.xact_commit,
                    "rollbacks": row.xact_rollback,
                    "conflicts": row.conflicts,
                    "deadlocks": row.deadlocks,
                }

                # Alert if deadlocks detected or too many active connections
                if row.deadlocks > 0:
                    status = HealthStatus.YELLOW
                elif row.numbackends > 50:
                    status = HealthStatus.YELLOW
                else:
                    status = HealthStatus.GREEN

                return {
                    "target": "pg_pool",
                    "status": status,
                    "response_time_ms": ms,
                    "details": stats,
                }
        except Exception as exc:
            ms = int((time.monotonic() - start) * 1000)
            log.error("health_check_pg_pool_failed", error=str(exc))
            return {
                "target": "pg_pool",
                "status": HealthStatus.RED,
                "response_time_ms": ms,
                "details": {"error": str(exc)},
            }

    async def _check_redis_memory(self, redis_url: str) -> dict[str, Any]:
        """Check Redis memory usage against the 2GB limit."""
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(redis_url, socket_timeout=5)
            try:
                info = await client.info(section="memory")
                used_bytes = info.get("used_memory", 0)
                used_mb = round(used_bytes / (1024 * 1024), 1)
                max_memory = info.get("maxmemory", 0)
                max_mb = round(max_memory / (1024 * 1024), 1) if max_memory else None
                evicted = info.get("evicted_keys", 0)
                frag_ratio = info.get("mem_fragmentation_ratio", 0)

                if used_mb > _REDIS_MEMORY_WARN_MB:
                    status = HealthStatus.YELLOW
                elif evicted > 0:
                    status = HealthStatus.YELLOW
                else:
                    status = HealthStatus.GREEN

                return {
                    "target": "redis_memory",
                    "status": status,
                    "response_time_ms": None,
                    "details": {
                        "used_mb": used_mb,
                        "max_mb": max_mb,
                        "evicted_keys": evicted,
                        "fragmentation_ratio": frag_ratio,
                        "used_memory_human": info.get("used_memory_human", ""),
                    },
                }
            finally:
                await client.aclose()
        except Exception as exc:
            log.error("health_check_redis_memory_failed", error=str(exc))
            return {
                "target": "redis_memory",
                "status": HealthStatus.RED,
                "response_time_ms": None,
                "details": {"error": str(exc)},
            }

    async def _check_docker_containers(self) -> dict[str, Any]:
        """Check Docker container health status via the Docker socket."""
        try:
            async with httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds="/var/run/docker.sock"),
                timeout=5.0,
            ) as client:
                resp = await client.get("http://localhost/containers/json?all=true")
                resp.raise_for_status()
                containers = resp.json()

            container_statuses = {}
            unhealthy_count = 0
            for c in containers:
                name = (c.get("Names") or ["/unknown"])[0].lstrip("/")
                state = c.get("State", "unknown")
                health = c.get("Status", "")
                container_statuses[name] = {"state": state, "status": health}
                if state != "running":
                    unhealthy_count += 1

            if unhealthy_count > 0:
                status = HealthStatus.YELLOW
            else:
                status = HealthStatus.GREEN

            return {
                "target": "docker",
                "status": status,
                "response_time_ms": None,
                "details": {
                    "total": len(containers),
                    "unhealthy": unhealthy_count,
                    "containers": container_statuses,
                },
            }
        except Exception as exc:
            log.error("health_check_docker_failed", error=str(exc))
            return {
                "target": "docker",
                "status": HealthStatus.YELLOW,
                "response_time_ms": None,
                "details": {"error": str(exc), "note": "Docker socket may not be mounted"},
            }

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def _store_results(self, checks: list[dict[str, Any]]) -> None:
        """Persist check results to ``system_health_log`` table."""
        try:
            from db.models import SystemHealthLog

            async with get_db_session() as session:
                for check in checks:
                    entry = SystemHealthLog(
                        target=check["target"],
                        status=check["status"],
                        response_time_ms=check.get("response_time_ms"),
                        details=check.get("details"),
                    )
                    session.add(entry)

            log.debug("health_results_stored", count=len(checks))
        except Exception:
            log.exception("health_results_store_failed")

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def _send_alerts(
        self,
        anomalies: list[dict[str, Any]],
        overall: HealthStatus,
    ) -> None:
        """Send notifications for degraded services."""
        try:
            from integrations.notification_service import get_notification_service

            notifier = get_notification_service()

            targets = ", ".join(a["target"] for a in anomalies)
            severity = "critical" if overall == HealthStatus.RED else "warning"

            details_lines = []
            for a in anomalies:
                detail = a.get("details", {})
                error = detail.get("error", "")
                rt = f" ({a['response_time_ms']}ms)" if a.get("response_time_ms") else ""
                line = f"- {a['target']}: {a['status']}{rt}"
                if error:
                    line += f" — {error[:100]}"
                details_lines.append(line)

            await notifier.notify_alert(
                title=f"System Health: {overall.upper()}",
                body=f"Degraded services: {targets}\n\n" + "\n".join(details_lines),
                severity=severity,
            )
        except Exception:
            log.exception("health_alert_send_failed")

    # ------------------------------------------------------------------
    # Claude escalation (anomaly diagnosis)
    # ------------------------------------------------------------------

    async def _escalate_to_claude(
        self,
        red_checks: list[dict[str, Any]],
        settings: Any,
    ) -> str | None:
        """Spawn Claude Code to diagnose RED anomalies.

        Only invoked when there are RED-status checks.  Uses the
        ``diagnostics`` MCP profile so Claude can query PostgreSQL
        and search for related issues.
        """
        try:
            from models.claude_spawner import ClaudeCodeSpawner

            targets = ", ".join(c["target"] for c in red_checks)
            details = "\n".join(f"- {c['target']}: {c['status']} — {c.get('details', {})}" for c in red_checks)

            prompt = (
                "You are T.A.R.S. system diagnostics. The following infrastructure "
                "checks have failed (RED status):\n\n"
                f"{details}\n\n"
                "Diagnose the most likely root cause and suggest immediate remediation "
                "steps. Be concise (3-5 sentences). Use your MCP tools to check the "
                "PostgreSQL health logs if helpful."
            )

            spawner = ClaudeCodeSpawner()
            result = await spawner.execute(
                prompt,
                mcp_profile="diagnostics",
                timeout=60,
                max_turns=3,
            )

            if result.success and result.text:
                log.info(
                    "claude_diagnosis_completed",
                    targets=targets,
                    duration_ms=result.duration_ms,
                    cost_usd=result.cost_usd,
                )
                return result.text
            else:
                log.warning(
                    "claude_diagnosis_failed",
                    targets=targets,
                    error=result.error,
                )
                return None
        except Exception:
            log.exception("claude_escalation_failed")
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_from_ms(response_time_ms: int) -> HealthStatus:
    """Map response time to a health status."""
    if response_time_ms >= _RESPONSE_TIME_CRIT_MS:
        return HealthStatus.RED
    if response_time_ms >= _RESPONSE_TIME_WARN_MS:
        return HealthStatus.YELLOW
    return HealthStatus.GREEN
