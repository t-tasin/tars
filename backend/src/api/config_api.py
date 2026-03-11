"""Config endpoints — user preferences and system configuration."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import verify_api_key
from src.api.schemas import ConfigUpdateRequest
from src.db.models import AuditLog
from src.db.repositories.config import ConfigRepository
from src.dependencies import get_db

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["config"])


# ---------------------------------------------------------------------------
# Default config values — seeded on first run
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[tuple[str, str], Any] = {
    ("morning_briefing", "time"): "05:50",
    ("morning_briefing", "alarm_offset_minutes"): 10,
    ("morning_briefing", "voice_enabled"): True,
    ("morning_briefing", "sections"): [
        {"name": "weather", "priority": 1, "enabled": True, "include_outfit": True},
        {"name": "schedule", "priority": 2, "enabled": True, "include_commute": True},
        {"name": "email_digest", "priority": 3, "enabled": True, "filter": "urgent+actionable"},
        {"name": "tasks_due", "priority": 4, "enabled": True},
        {"name": "job_matches", "priority": 5, "enabled": True, "show_top_n": 3},
        {"name": "system_health", "priority": 6, "enabled": True},
        {"name": "health_summary", "priority": 8, "enabled": True},
        {"name": "finance_summary", "priority": 9, "enabled": True},
        {"name": "proactive_suggestions", "priority": 10, "enabled": True},
    ],
    ("email_contacts", "always_urgent"): [
        "*@stanford.edu",
        "sadigh@cs.stanford.edu",
        "pliang@cs.stanford.edu",
    ],
    ("email_contacts", "always_actionable"): [
        "*@github.com",
        "*@wooster.edu",
    ],
    ("email_contacts", "always_noise"): [
        "*@marketing.*",
        "*promo*",
    ],
    ("notifications", "quiet_hours_start"): "23:00",
    ("notifications", "quiet_hours_end"): "06:00",
    ("general", "location"): "Wooster, OH",
    ("general", "timezone"): "America/New_York",
    # Job application Q&A bank — user fills once, used for all applications
    ("job_application", "full_name"): "",
    ("job_application", "email"): "",
    ("job_application", "phone"): "",
    ("job_application", "location"): "Wooster, OH",
    ("job_application", "linkedin_url"): "",
    ("job_application", "github_url"): "",
    ("job_application", "portfolio_url"): "",
    ("job_application", "years_of_experience"): "",
    ("job_application", "current_title"): "",
    ("job_application", "current_company"): "",
    ("job_application", "education"): "",
    ("job_application", "skills"): [],
    ("job_application", "resume_path"): "",
    ("job_application", "work_authorization"): "",
    ("job_application", "visa_sponsorship_needed"): "",
    ("job_application", "salary_expectation"): "",
    ("job_application", "willing_to_relocate"): "",
    ("job_application", "start_date"): "",
    ("job_application", "why_looking"): "",
    ("job_application", "preferred_role_types"): [],
    ("job_application", "preferred_locations"): [],
    ("job_application", "excluded_companies"): [],
    ("job_application", "cover_letter_highlights"): "",
    ("job_application", "research_interests"): "",
}


# ---------------------------------------------------------------------------
# Seed helper — called on app startup
# ---------------------------------------------------------------------------

async def seed_default_config(session: AsyncSession) -> None:
    """Insert default config values if they don't exist."""
    repo = ConfigRepository(session)
    existing = await repo.get_all()

    inserted = 0
    for (namespace, key), value in DEFAULT_CONFIG.items():
        if namespace in existing and key in existing[namespace]:
            continue
        await repo.set(namespace, key, value, updated_by="system")
        inserted += 1

    if inserted:
        await session.commit()
        log.info("config_seeded", inserted=inserted)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_config(
    namespace: str | None = Query(default=None, description="Filter by namespace"),
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    """Return all config as a nested dict, optionally filtered by namespace."""
    repo = ConfigRepository(session)
    if namespace:
        return {namespace: await repo.get_namespace(namespace)}
    return await repo.get_all()


@router.put("/config")
async def update_config(
    body: ConfigUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _auth: dict[str, Any] = Depends(verify_api_key),
) -> dict[str, Any]:
    """Upsert a single config value. Logs change to audit_log (HC-08)."""
    repo = ConfigRepository(session)
    old_value = await repo.get(body.namespace, body.key)
    await repo.set(body.namespace, body.key, body.value, updated_by="api")

    # HC-08: audit log
    session.add(AuditLog(
        action_type="config_update",
        actor="api",
        target=f"{body.namespace}.{body.key}",
        details={
            "namespace": body.namespace,
            "key": body.key,
            "old_value": old_value,
            "new_value": body.value,
        },
    ))

    log.info(
        "config_updated",
        namespace=body.namespace,
        key=body.key,
        old_value=old_value,
        new_value=body.value,
    )

    return {
        "status": "ok",
        "namespace": body.namespace,
        "key": body.key,
        "value": body.value,
    }
