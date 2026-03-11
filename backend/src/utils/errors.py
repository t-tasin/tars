"""Custom exception hierarchy for T.A.R.S.

All T.A.R.S.-specific exceptions inherit from :class:`TARSError`.
This allows unified error handling at the orchestrator level while
preserving specific error types for targeted recovery.
"""

from __future__ import annotations


class TARSError(Exception):
    """Base exception for all T.A.R.S. errors."""

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# AI model errors
# ---------------------------------------------------------------------------


class AIModelUnavailableError(TARSError):
    """An AI model (Claude or Gemini) is unavailable.

    Raised when an AI model fails after exhausting retries, allowing the
    orchestrator to trigger the fallback chain (HC-09).
    """

    def __init__(
        self,
        message: str,
        *,
        model: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details={**(details or {}), "model": model})
        self.model = model


class ClaudeUnavailableError(AIModelUnavailableError):
    """Claude Code is unavailable (binary missing, timeout, or repeated failures)."""

    def __init__(self, message: str = "Claude Code is unavailable", **kwargs) -> None:
        super().__init__(message, model="claude_code", **kwargs)


class GeminiUnavailableError(AIModelUnavailableError):
    """Gemini API is unavailable (rate-limited, server error, or timeout)."""

    def __init__(self, message: str = "Gemini API is unavailable", **kwargs) -> None:
        super().__init__(message, model="gemini", **kwargs)


# ---------------------------------------------------------------------------
# Integration errors
# ---------------------------------------------------------------------------


class IntegrationError(TARSError):
    """An external integration (CalDAV, Gmail, GitHub, etc.) failed.

    Carries the service name for targeted circuit-breaking and health reporting.
    """

    def __init__(
        self,
        message: str,
        *,
        service: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details={**(details or {}), "service": service})
        self.service = service


class IntegrationTimeoutError(IntegrationError):
    """An integration timed out."""


class IntegrationAuthError(IntegrationError):
    """An integration's credentials are invalid or expired."""


# ---------------------------------------------------------------------------
# Approval errors (re-exported from approval_manager for convenience)
# ---------------------------------------------------------------------------


class ApprovalError(TARSError):
    """Base class for approval-related errors."""


class ApprovalNotFoundError(ApprovalError):
    """The requested approval does not exist."""


class ApprovalExpiredError(ApprovalError):
    """The approval has passed its expiry window."""


class ApprovalAlreadyDecidedError(ApprovalError):
    """The approval has already been approved, rejected, or expired."""


# ---------------------------------------------------------------------------
# Config / startup errors
# ---------------------------------------------------------------------------


class ConfigError(TARSError):
    """A required configuration value is missing or invalid."""


class DatabaseError(TARSError):
    """A database operation failed (connection, query, migration)."""
