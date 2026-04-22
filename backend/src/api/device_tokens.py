"""Device token registration for APNs push notifications."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import verify_api_key
from src.db.models import DeviceToken
from src.dependencies import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["device_tokens"])


class RegisterDeviceTokenRequest(BaseModel):
    token: str
    device_name: str | None = None
    platform: str = "ios"


class RegisterDeviceTokenResponse(BaseModel):
    registered: bool
    message: str


@router.post(
    "/device-tokens",
    response_model=RegisterDeviceTokenResponse,
)
async def register_device_token(
    body: RegisterDeviceTokenRequest,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> RegisterDeviceTokenResponse:
    """Register or update an APNs device token.

    Called by the iOS app after receiving a device token from Apple.
    Upserts: if the token already exists, updates device_name and
    reactivates it; otherwise inserts a new row.
    """
    result = await session.execute(select(DeviceToken).where(DeviceToken.token == body.token))
    existing = result.scalar_one_or_none()

    if existing:
        existing.device_name = body.device_name or existing.device_name
        existing.platform = body.platform
        existing.active = True
        log.info("device_token_updated", token=body.token[:8] + "...")
        return RegisterDeviceTokenResponse(registered=True, message="Device token updated")

    token = DeviceToken(
        token=body.token,
        device_name=body.device_name,
        platform=body.platform,
    )
    session.add(token)
    log.info("device_token_registered", token=body.token[:8] + "...", platform=body.platform)
    return RegisterDeviceTokenResponse(registered=True, message="Device token registered")


@router.delete(
    "/device-tokens/{token}",
    response_model=RegisterDeviceTokenResponse,
)
async def unregister_device_token(
    token: str,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> RegisterDeviceTokenResponse:
    """Deactivate a device token (soft delete)."""
    await session.execute(update(DeviceToken).where(DeviceToken.token == token).values(active=False))
    log.info("device_token_deactivated", token=token[:8] + "...")
    return RegisterDeviceTokenResponse(registered=False, message="Device token deactivated")
