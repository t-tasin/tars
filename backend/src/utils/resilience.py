"""Resilience utilities: circuit breaker, retry with backoff, and service health tracker.

These are shared primitives used across integration clients and model
clients to implement HC-09 (graceful degradation).
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import structlog

log = structlog.get_logger()

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreakerState:
    """Tracks failure history for a single service."""

    service: str
    failure_threshold: int = 3
    failure_window_s: float = 300.0  # 5 minutes
    cooldown_s: float = 60.0  # 1 minute open before half-open

    # Internal state
    _failures: deque[float] = field(default_factory=deque)
    _opened_at: float | None = field(default=None, repr=False)
    _last_success_at: float | None = field(default=None, repr=False)

    @property
    def is_open(self) -> bool:
        """True when the circuit is open (blocking calls)."""
        if self._opened_at is None:
            return False
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.cooldown_s:
            # Transition to half-open: allow one attempt
            return False
        return True

    @property
    def last_success(self) -> datetime | None:
        """Wall-clock time of the last successful call."""
        if self._last_success_at is None:
            return None
        # Convert monotonic offset to wall-clock approximation
        offset = time.monotonic() - self._last_success_at
        return datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=offset)

    def record_success(self) -> None:
        """Record a successful call — resets the breaker."""
        self._failures.clear()
        self._opened_at = None
        self._last_success_at = time.monotonic()

    def record_failure(self) -> None:
        """Record a failure. Opens the circuit if threshold is exceeded."""
        now = time.monotonic()
        self._failures.append(now)

        # Evict failures outside the window
        cutoff = now - self.failure_window_s
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

        if len(self._failures) >= self.failure_threshold:
            self._opened_at = now
            log.warning(
                "circuit_breaker_opened",
                service=self.service,
                failures=len(self._failures),
                cooldown_s=self.cooldown_s,
            )

    @property
    def status(self) -> str:
        """Return "closed", "open", or "half_open"."""
        if self._opened_at is None:
            return "closed"
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.cooldown_s:
            return "half_open"
        return "open"

    @property
    def failure_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self.failure_window_s
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        return len(self._failures)


class CircuitBreakerOpenError(Exception):
    """Raised when a call is blocked by an open circuit breaker."""

    def __init__(self, service: str) -> None:
        super().__init__(f"Circuit breaker open for {service}")
        self.service = service


# ---------------------------------------------------------------------------
# Service health registry (singleton)
# ---------------------------------------------------------------------------


class ServiceHealthRegistry:
    """Global registry tracking circuit breaker state for all services.

    Used by the health endpoint to report per-service status.
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreakerState] = {}

    def get_or_create(
        self,
        service: str,
        *,
        failure_threshold: int = 3,
        failure_window_s: float = 300.0,
        cooldown_s: float = 60.0,
    ) -> CircuitBreakerState:
        if service not in self._breakers:
            self._breakers[service] = CircuitBreakerState(
                service=service,
                failure_threshold=failure_threshold,
                failure_window_s=failure_window_s,
                cooldown_s=cooldown_s,
            )
        return self._breakers[service]

    def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        """Return a dict of service → status info for the health endpoint."""
        result: dict[str, dict[str, Any]] = {}
        for name, cb in self._breakers.items():
            result[name] = {
                "circuit_breaker": cb.status,
                "recent_failures": cb.failure_count,
                "last_success": cb.last_success.isoformat() if cb.last_success else None,
            }
        return result

    def get_status(self, service: str) -> str:
        """Return the health status for a service: healthy, degraded, or down."""
        cb = self._breakers.get(service)
        if cb is None:
            return "unknown"
        status = cb.status
        if status == "closed":
            return "healthy"
        if status == "half_open":
            return "degraded"
        return "down"


_registry: ServiceHealthRegistry | None = None


def get_service_health_registry() -> ServiceHealthRegistry:
    """Return the global ServiceHealthRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ServiceHealthRegistry()
    return _registry


# ---------------------------------------------------------------------------
# Retry with exponential backoff decorator
# ---------------------------------------------------------------------------


def retry_with_backoff(
    *,
    max_retries: int = 3,
    backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0),
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    service: str | None = None,
) -> Callable[[F], F]:
    """Decorator: retry an async function with exponential backoff.

    If *service* is provided, integrates with the circuit breaker registry:
    - Checks the breaker before each call
    - Records success/failure on the breaker

    Args:
        max_retries: Total attempts (including the first).
        backoff_seconds: Delay before each retry (capped to length).
        retryable_exceptions: Exception types that trigger a retry.
        service: Optional service name for circuit breaker integration.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cb: CircuitBreakerState | None = None
            if service:
                registry = get_service_health_registry()
                cb = registry.get_or_create(service)

                if cb.is_open:
                    log.warning("circuit_breaker_blocked", service=service)
                    raise CircuitBreakerOpenError(service)

            last_exc: Exception | None = None

            for attempt in range(max_retries):
                try:
                    result = await func(*args, **kwargs)
                    if cb:
                        cb.record_success()
                    return result
                except retryable_exceptions as exc:
                    last_exc = exc

                    if cb:
                        cb.record_failure()

                    if attempt < max_retries - 1:
                        delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                        log.warning(
                            "retry_attempt",
                            service=service or func.__qualname__,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_s=delay,
                            error=str(exc),
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            "retry_exhausted",
                            service=service or func.__qualname__,
                            attempts=max_retries,
                            error=str(exc),
                        )
                        raise

            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
