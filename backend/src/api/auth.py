"""API authentication middleware for T.A.R.S.

Single-user system: API key + optional device token validation.
"""

from __future__ import annotations

import hmac
from typing import Any

import structlog
from fastapi import Header, HTTPException

from src.api.schemas import ErrorDetail, ErrorResponse
from src.config import get_settings

log = structlog.get_logger()


def _unauthorized(message: str) -> HTTPException:
    """Build a 401 HTTPException with an ErrorResponse body."""
    body = ErrorResponse(
        error=ErrorDetail(code="unauthorized", message=message),
    )
    return HTTPException(
        status_code=401,
        detail=body.model_dump(),
    )


async def verify_api_key(
    authorization: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI dependency that validates the API key and optional device token.

    Returns:
        ``{"api_key_valid": True, "device_token": <token-or-None>}``

    Raises:
        HTTPException 401 on missing/invalid credentials.
    """
    settings = get_settings()

    # --- API key ---
    if not authorization:
        log.warning("auth_missing_header")
        raise _unauthorized("Missing Authorization header")

    parts = authorization.split(" ", maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        log.warning("auth_malformed_header")
        raise _unauthorized("Authorization header must be 'Bearer <key>'")

    api_key = parts[1]
    if not hmac.compare_digest(api_key, settings.tars_api_key):
        log.warning("auth_invalid_api_key")
        raise _unauthorized("Invalid API key")

    # --- Device token (optional) ---
    allowed_raw = settings.allowed_device_tokens.strip()
    if allowed_raw:
        allowed_tokens = {t.strip() for t in allowed_raw.split(",") if t.strip()}
        if not x_device_token or x_device_token not in allowed_tokens:
            log.warning("auth_invalid_device_token", device_token=x_device_token)
            raise _unauthorized("Invalid or missing device token")

    return {"api_key_valid": True, "device_token": x_device_token}


def verify_ws_auth(token: str, device: str) -> bool:
    """Validate WebSocket connection credentials from query params.

    Args:
        token: API key passed as ``?token=`` query parameter.
        device: Device token passed as ``?device=`` query parameter.

    Returns:
        ``True`` if credentials are valid, ``False`` otherwise.
    """
    settings = get_settings()

    if not token or not hmac.compare_digest(token, settings.tars_api_key):
        return False

    allowed_raw = settings.allowed_device_tokens.strip()
    if allowed_raw:
        allowed_tokens = {t.strip() for t in allowed_raw.split(",") if t.strip()}
        if not device or device not in allowed_tokens:
            return False

    return True
