"""Health check endpoint — no auth required.

Reports detailed per-service status including integration health
from the circuit breaker registry, AI model availability, and
infrastructure dependencies.
"""

from __future__ import annotations

import time
from typing import Any

import psutil
import structlog
from fastapi import APIRouter

from src.api.schemas import HealthResponse
from src.config import get_settings
from utils.resilience import get_service_health_registry

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["health"])

_START_TIME = time.monotonic()


def _uptime_seconds() -> int:
    return int(time.monotonic() - _START_TIME)


# ---------------------------------------------------------------------------
# Infrastructure checks
# ---------------------------------------------------------------------------


async def _check_postgres() -> dict[str, Any]:
    """Test the PostgreSQL connection pool."""
    start = time.monotonic()
    try:
        from sqlalchemy import text

        from src.db.session import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"status": "connected", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        log.warning("health_postgres_error", error=str(exc))
        return {"status": "disconnected", "latency_ms": latency_ms, "error": str(exc)}


async def _check_redis() -> dict[str, Any]:
    """Attempt a PING against the Redis instance on Node 2."""
    start = time.monotonic()
    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        try:
            await client.ping()
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"status": "connected", "latency_ms": latency_ms}
        finally:
            await client.aclose()
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        log.warning("health_redis_error", error=str(exc))
        return {"status": "disconnected", "latency_ms": latency_ms, "error": str(exc)}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return system health status for T.A.R.S. and all dependencies.

    Includes:
    - Infrastructure: PostgreSQL, Redis (with latency)
    - Integrations: circuit breaker status per service
    - AI models: availability derived from recent call success
    - System resources: CPU, memory, uptime
    """
    postgres_info = await _check_postgres()
    redis_info = await _check_redis()

    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()

    # Determine overall status from infrastructure
    postgres_status = postgres_info["status"]
    redis_status = redis_info["status"]

    overall = "green"
    if postgres_status == "disconnected":
        overall = "red"
    elif redis_status == "disconnected":
        overall = "yellow"

    # Integration services health from circuit breaker registry
    registry = get_service_health_registry()
    service_statuses = registry.get_all_statuses()

    # Check if any integration is down
    for _service, info in service_statuses.items():
        if info["circuit_breaker"] == "open" and overall == "green":
            overall = "yellow"

    # Build per-service detail
    services: dict[str, Any] = {}
    for svc_name, svc_info in service_statuses.items():
        cb_status = svc_info["circuit_breaker"]
        if cb_status == "closed":
            health = "healthy"
        elif cb_status == "half_open":
            health = "degraded"
        else:
            health = "down"

        services[svc_name] = {
            "status": health,
            "circuit_breaker": cb_status,
            "recent_failures": svc_info["recent_failures"],
            "last_success": svc_info["last_success"],
        }

    return HealthResponse(
        tars={
            "status": overall,
            "node1": {
                "status": "running",
                "uptime_seconds": _uptime_seconds(),
                "cpu_percent": cpu_percent,
                "memory_percent": mem.percent,
                "memory_used_mb": round(mem.used / (1024 * 1024)),
                "memory_total_mb": round(mem.total / (1024 * 1024)),
            },
            "infrastructure": {
                "postgres": postgres_info,
                "redis": redis_info,
            },
            "services": services,
        },
        atlasdesk={},
        ai_budget={},
    )
