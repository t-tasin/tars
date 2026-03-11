"""Health data sync API — accepts HealthKit data from the iOS app."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import HealthDataSyncRequest
from src.db.models import HealthData
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["health-data"])


@router.post("/health-data/sync")
async def sync_health_data(
    request: HealthDataSyncRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Sync HealthKit data from iOS app. Upserts into health_data table.

    Each data point is matched by (data_type, recorded_date, source) for upsert.
    """
    synced = 0

    for point in request.data:
        recorded_date = datetime.fromisoformat(point.date).date()

        # Check for existing record (same type + date + source)
        existing = await db.execute(
            select(HealthData.id).where(
                HealthData.data_type == point.type,
                HealthData.recorded_date == recorded_date,
                HealthData.source == "healthkit",
            )
        )
        row = existing.scalar_one_or_none()

        if row is not None:
            # Update existing record
            await db.execute(
                update(HealthData)
                .where(HealthData.id == row)
                .values(
                    value=point.value,
                    unit=point.unit,
                    metadata_=point.metadata,
                    synced_at=datetime.now(timezone.utc),
                )
            )
        else:
            # Insert new record
            db.add(HealthData(
                data_type=point.type,
                value=point.value,
                unit=point.unit,
                recorded_date=recorded_date,
                source="healthkit",
                metadata_=point.metadata,
            ))

        synced += 1

    log.info(
        "health_data_synced",
        total=len(request.data),
        synced=synced,
    )

    return {
        "synced": synced,
        "total": len(request.data),
    }
