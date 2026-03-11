"""Contacts sync API — accepts contacts from the iOS app."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import ContactsSyncRequest
from src.db.models import Contact
from src.db.repositories.contacts import ContactRepository
from src.dependencies import get_db, verify_auth

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["contacts"])


@router.post("/contacts/sync")
async def sync_contacts(
    request: ContactsSyncRequest,
    _auth: Annotated[dict[str, Any], Depends(verify_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Sync contacts from iOS app. Upserts by apple_contact_id.

    If ``full_sync`` is True, contacts not in the request are soft-deactivated
    (their ``source`` is set to ``"apple_contacts_removed"``).
    """
    repo = ContactRepository(db)
    upserted = 0
    seen_ids: list[str] = []

    for c in request.contacts:
        seen_ids.append(c.apple_contact_id)
        await repo.upsert(
            apple_contact_id=c.apple_contact_id,
            full_name=c.full_name,
            email_addresses=c.email_addresses,
            phone_numbers=c.phone_numbers,
            organization=c.organization,
            job_title=c.job_title,
            relationship_type=c.relationship,
            source="apple_contacts",
        )
        upserted += 1

    # Full sync: mark removed contacts
    removed = 0
    if request.full_sync and seen_ids:
        result = await db.execute(
            update(Contact)
            .where(
                Contact.source == "apple_contacts",
                Contact.apple_contact_id.notin_(seen_ids),
            )
            .values(source="apple_contacts_removed")
            .execution_options(synchronize_session=False)
        )
        removed = result.rowcount or 0

    log.info(
        "contacts_synced",
        upserted=upserted,
        removed=removed,
        full_sync=request.full_sync,
    )

    return {
        "upserted": upserted,
        "removed": removed,
        "total": len(request.contacts),
    }
