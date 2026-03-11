"""Tests for the custom exception hierarchy and resilience utilities."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from utils.errors import (
    AIModelUnavailableError,
    ApprovalAlreadyDecidedError,
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ClaudeUnavailableError,
    ConfigError,
    DatabaseError,
    GeminiUnavailableError,
    IntegrationAuthError,
    IntegrationError,
    IntegrationTimeoutError,
    TARSError,
)
from utils.resilience import (
    CircuitBreakerOpenError,
    CircuitBreakerState,
    ServiceHealthRegistry,
    get_service_health_registry,
    retry_with_backoff,
)


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_tars_error_is_exception(self) -> None:
        assert issubclass(TARSError, Exception)

    def test_tars_error_carries_details(self) -> None:
        err = TARSError("oops", details={"key": "val"})
        assert str(err) == "oops"
        assert err.details == {"key": "val"}

    def test_tars_error_default_details(self) -> None:
        err = TARSError("oops")
        assert err.details == {}

    def test_ai_model_unavailable_inherits_tars(self) -> None:
        assert issubclass(AIModelUnavailableError, TARSError)

    def test_ai_model_unavailable_carries_model(self) -> None:
        err = AIModelUnavailableError("down", model="gemini")
        assert err.model == "gemini"
        assert err.details["model"] == "gemini"

    def test_claude_unavailable(self) -> None:
        err = ClaudeUnavailableError()
        assert err.model == "claude_code"
        assert isinstance(err, AIModelUnavailableError)

    def test_gemini_unavailable(self) -> None:
        err = GeminiUnavailableError()
        assert err.model == "gemini"
        assert isinstance(err, AIModelUnavailableError)

    def test_integration_error_carries_service(self) -> None:
        err = IntegrationError("timeout", service="weather")
        assert err.service == "weather"
        assert isinstance(err, TARSError)

    def test_integration_timeout_inherits(self) -> None:
        err = IntegrationTimeoutError("slow", service="github")
        assert isinstance(err, IntegrationError)

    def test_integration_auth_inherits(self) -> None:
        err = IntegrationAuthError("bad token", service="gmail")
        assert isinstance(err, IntegrationError)

    def test_approval_errors_inherit_tars(self) -> None:
        assert issubclass(ApprovalNotFoundError, ApprovalError)
        assert issubclass(ApprovalExpiredError, ApprovalError)
        assert issubclass(ApprovalAlreadyDecidedError, ApprovalError)
        assert issubclass(ApprovalError, TARSError)

    def test_config_error(self) -> None:
        err = ConfigError("missing key")
        assert isinstance(err, TARSError)

    def test_database_error(self) -> None:
        err = DatabaseError("connection refused")
        assert isinstance(err, TARSError)


# ---------------------------------------------------------------------------
# Circuit breaker tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        cb = CircuitBreakerState(service="test")
        assert cb.status == "closed"
        assert not cb.is_open

    def test_records_success(self) -> None:
        cb = CircuitBreakerState(service="test")
        cb.record_success()
        assert cb.last_success is not None
        assert cb.failure_count == 0

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreakerState(service="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.status == "closed"
        cb.record_failure()
        assert cb.status == "open"
        assert cb.is_open

    def test_half_open_after_cooldown(self) -> None:
        cb = CircuitBreakerState(service="test", failure_threshold=2, cooldown_s=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.status == "open"
        time.sleep(0.02)
        assert cb.status == "half_open"
        assert not cb.is_open  # half-open allows calls

    def test_success_resets_breaker(self) -> None:
        cb = CircuitBreakerState(service="test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.status == "open"
        cb.record_success()
        assert cb.status == "closed"
        assert cb.failure_count == 0

    def test_failures_outside_window_are_evicted(self) -> None:
        cb = CircuitBreakerState(
            service="test",
            failure_threshold=3,
            failure_window_s=0.01,
        )
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        # Old failures should be evicted
        assert cb.failure_count == 0

    def test_last_success_none_initially(self) -> None:
        cb = CircuitBreakerState(service="test")
        assert cb.last_success is None


# ---------------------------------------------------------------------------
# Service health registry tests
# ---------------------------------------------------------------------------


class TestServiceHealthRegistry:
    def test_get_or_create(self) -> None:
        registry = ServiceHealthRegistry()
        cb1 = registry.get_or_create("weather")
        cb2 = registry.get_or_create("weather")
        assert cb1 is cb2

    def test_get_all_statuses(self) -> None:
        registry = ServiceHealthRegistry()
        cb = registry.get_or_create("test_svc")
        cb.record_success()
        statuses = registry.get_all_statuses()
        assert "test_svc" in statuses
        assert statuses["test_svc"]["circuit_breaker"] == "closed"
        assert statuses["test_svc"]["last_success"] is not None

    def test_get_status_healthy(self) -> None:
        registry = ServiceHealthRegistry()
        registry.get_or_create("svc")
        assert registry.get_status("svc") == "healthy"

    def test_get_status_down(self) -> None:
        registry = ServiceHealthRegistry()
        cb = registry.get_or_create("svc", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert registry.get_status("svc") == "down"

    def test_get_status_unknown(self) -> None:
        registry = ServiceHealthRegistry()
        assert registry.get_status("nonexistent") == "unknown"

    def test_singleton(self) -> None:
        r1 = get_service_health_registry()
        r2 = get_service_health_registry()
        assert r1 is r2


# ---------------------------------------------------------------------------
# Retry with backoff tests
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self) -> None:
        call_count = 0

        @retry_with_backoff(max_retries=3, backoff_seconds=(0.01,))
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        call_count = 0

        @retry_with_backoff(
            max_retries=3,
            backoff_seconds=(0.01, 0.01),
            retryable_exceptions=(ValueError,),
        )
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        @retry_with_backoff(
            max_retries=2,
            backoff_seconds=(0.01,),
            retryable_exceptions=(ValueError,),
        )
        async def fn() -> str:
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            await fn()

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self) -> None:
        call_count = 0

        @retry_with_backoff(
            max_retries=3,
            backoff_seconds=(0.01,),
            retryable_exceptions=(ValueError,),
        )
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await fn()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_when_open(self) -> None:
        registry = get_service_health_registry()
        cb = registry.get_or_create(
            "test_block_svc",
            failure_threshold=1,
            cooldown_s=60,
        )
        cb.record_failure()  # opens the breaker

        @retry_with_backoff(
            max_retries=3,
            backoff_seconds=(0.01,),
            service="test_block_svc",
        )
        async def fn() -> str:
            return "ok"

        with pytest.raises(CircuitBreakerOpenError):
            await fn()

    @pytest.mark.asyncio
    async def test_records_success_on_breaker(self) -> None:
        registry = get_service_health_registry()
        svc_name = "test_success_svc"
        cb = registry.get_or_create(svc_name)

        @retry_with_backoff(max_retries=1, service=svc_name)
        async def fn() -> str:
            return "ok"

        await fn()
        assert cb.last_success is not None
