"""Grafana and Loki API client for T.A.R.S. system monitoring.

Queries Grafana dashboards for alert status and Loki for log searches
(LogQL).  Gracefully returns empty/degraded results when Grafana/Loki
are not configured (URLs empty).

Includes circuit breaker integration for health reporting.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from utils.resilience import get_service_health_registry

log = structlog.get_logger()


class GrafanaClient:
    """Async Grafana + Loki client for system health monitoring.

    If *grafana_url* or *loki_url* are empty strings, the corresponding
    methods return empty results instead of raising — allowing the health
    monitor to run even when Grafana/Loki are not yet deployed.

    Includes circuit breaker integration for health reporting.
    """

    def __init__(
        self,
        grafana_url: str,
        grafana_api_key: str,
        loki_url: str,
    ) -> None:
        self._grafana_url = grafana_url.rstrip("/") if grafana_url else ""
        self._loki_url = loki_url.rstrip("/") if loki_url else ""

        headers: dict[str, str] = {"Accept": "application/json"}
        if grafana_api_key:
            headers["Authorization"] = f"Bearer {grafana_api_key}"

        self._grafana_http = httpx.AsyncClient(timeout=10.0, headers=headers)
        self._loki_http = httpx.AsyncClient(timeout=10.0)

        registry = get_service_health_registry()
        self._grafana_cb = registry.get_or_create("grafana")
        self._loki_cb = registry.get_or_create("loki")

    # ------------------------------------------------------------------
    # Grafana
    # ------------------------------------------------------------------

    @property
    def grafana_configured(self) -> bool:
        return bool(self._grafana_url)

    @property
    def loki_configured(self) -> bool:
        return bool(self._loki_url)

    async def health_check(self) -> dict[str, Any]:
        """Check Grafana server health (``GET /api/health``).

        Returns::

            {"status": "ok", "database": "ok", "version": "10.x"}

        or ``{"status": "unconfigured"}`` if the URL is empty.
        """
        if not self._grafana_url:
            return {"status": "unconfigured"}

        try:
            resp = await self._grafana_http.get(f"{self._grafana_url}/api/health")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            self._grafana_cb.record_success()
            log.debug("grafana_health_ok", data=data)
            return data
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            self._grafana_cb.record_failure()
            log.error("grafana_health_failed", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def get_alerts(self) -> list[dict[str, Any]]:
        """Fetch firing and pending alerts from Grafana Alerting.

        Returns a list of alert dicts::

            [{"name": "High CPU", "state": "firing", "labels": {...},
              "active_at": "...", "value": "..."}, ...]
        """
        if not self._grafana_url:
            return []

        try:
            resp = await self._grafana_http.get(
                f"{self._grafana_url}/api/v1/provisioning/alert-rules",
            )
            resp.raise_for_status()
            rules: list[dict[str, Any]] = resp.json()

            alerts: list[dict[str, Any]] = []
            for rule in rules:
                alerts.append({
                    "name": rule.get("title", ""),
                    "state": rule.get("provenance", "unknown"),
                    "labels": rule.get("labels", {}),
                    "folder": rule.get("folderUID", ""),
                    "uid": rule.get("uid", ""),
                })

            self._grafana_cb.record_success()
            log.debug("grafana_alerts_fetched", count=len(alerts))
            return alerts
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            self._grafana_cb.record_failure()
            log.error("grafana_alerts_failed", error=str(exc))
            return []

    async def get_dashboard_status(
        self, dashboard_uid: str,
    ) -> dict[str, Any]:
        """Fetch a specific dashboard by UID.

        Returns::

            {"title": "T.A.R.S. Overview", "uid": "abc123",
             "panels": 12, "tags": [...]}
        """
        if not self._grafana_url:
            return {"status": "unconfigured"}

        try:
            resp = await self._grafana_http.get(
                f"{self._grafana_url}/api/dashboards/uid/{dashboard_uid}",
            )
            resp.raise_for_status()
            data = resp.json()
            dashboard = data.get("dashboard", {})

            return {
                "title": dashboard.get("title", ""),
                "uid": dashboard.get("uid", dashboard_uid),
                "panels": len(dashboard.get("panels", [])),
                "tags": dashboard.get("tags", []),
            }
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            log.error("grafana_dashboard_failed", uid=dashboard_uid, error=str(exc))
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Loki (LogQL)
    # ------------------------------------------------------------------

    async def query_logs(
        self,
        logql: str,
        *,
        limit: int = 100,
        since: str = "1h",
    ) -> list[dict[str, Any]]:
        """Run a LogQL query against Loki.

        Args:
            logql: LogQL query string, e.g.
                ``'{job="tars-backend"} |= "error"'``.
            limit: Maximum number of log entries to return.
            since: Time range, e.g. ``"1h"``, ``"30m"``, ``"24h"``.

        Returns a list of log entry dicts::

            [{"timestamp": "2026-03-10T08:00:00Z",
              "line": "...", "labels": {"job": "tars-backend"}}, ...]
        """
        if not self._loki_url:
            return []

        try:
            resp = await self._loki_http.get(
                f"{self._loki_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "limit": str(limit),
                    "since": since,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("data", {}).get("result", [])

            entries: list[dict[str, Any]] = []
            for stream in results:
                labels = stream.get("stream", {})
                for ts, line in stream.get("values", []):
                    entries.append({
                        "timestamp": ts,
                        "line": line,
                        "labels": labels,
                    })

            self._loki_cb.record_success()
            log.debug("loki_query_ok", query=logql[:100], entries=len(entries))
            return entries
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            self._loki_cb.record_failure()
            log.error("loki_query_failed", query=logql[:100], error=str(exc))
            return []

    async def count_errors(
        self,
        job: str = "tars-backend",
        since: str = "5m",
    ) -> int:
        """Count error-level log lines for a given job in the recent window."""
        query = '{job="' + job + '"} |= "ERROR"'
        entries = await self.query_logs(
            query,
            limit=1000,
            since=since,
        )
        return len(entries)

    async def get_recent_errors(
        self,
        job: str = "tars-backend",
        limit: int = 20,
        since: str = "1h",
    ) -> list[dict[str, Any]]:
        """Get recent error log lines for diagnostic context."""
        query = '{job="' + job + '"} |= "ERROR"'
        return await self.query_logs(
            query,
            limit=limit,
            since=since,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Shut down HTTP clients."""
        await self._grafana_http.aclose()
        await self._loki_http.aclose()
