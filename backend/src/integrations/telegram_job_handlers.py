"""Telegram handlers for the job application pipeline.

Provides:
- Job digest formatting and delivery
- Inline keyboard callbacks (Apply / Skip / Details)
- /jobs command handlers

This module composes with the existing TelegramGateway (send-only)
and is wired into the scheduler after the 2 AM scan completes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from integrations.telegram_bot import TelegramGateway

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Digest formatting and delivery
# ---------------------------------------------------------------------------


async def send_job_digest(
    gateway: TelegramGateway,
    jobs: list[dict[str, Any]],
    scan_stats: dict[str, Any] | None = None,
) -> None:
    """Format and send the daily job digest to Telegram.

    Each job gets inline keyboard buttons: Apply / Skip / Details.
    Jobs should already be sorted by fit score descending.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if not jobs:
        await gateway.send_message(
            text=("<b>T.A.R.S. Daily Job Digest</b>\n\nNo new matches found today. The next scan runs at 2:00 AM."),
        )
        return

    today = datetime.now(UTC).strftime("%B %d, %Y")
    total = scan_stats.get("total_scraped", len(jobs)) if scan_stats else len(jobs)

    header = (
        f"<b>T.A.R.S. Daily Job Digest \u2014 {today}</b>\n"
        f"Found <b>{len(jobs)}</b> matches from {total} listings\n"
        f"All jobs tracked in Notion.\n"
        f"\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    )

    await gateway.send_message(text=header)

    # Send each job as a separate message with its own inline keyboard
    for i, job in enumerate(jobs[:15], 1):
        job_id = job.get("id", "")
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        location = job.get("location", "")
        salary = job.get("salary_range", "")
        score = job.get("match_score", 0)
        source = job.get("source", "")
        apply_method = job.get("apply_method", "manual")

        auto_label = "Auto-apply available" if apply_method != "manual" else "Manual apply only"

        lines = [f"<b>{i}. {title} \u2014 {company}</b>"]
        detail_parts = []
        if location:
            detail_parts.append(location)
        if salary:
            detail_parts.append(salary)
        if detail_parts:
            lines.append("   " + " | ".join(detail_parts))
        lines.append(f"   Match: <b>{score}%</b> | Source: {source}")
        lines.append(f"   {auto_label}")

        text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("\u2705 Apply", callback_data=f"job_apply_{job_id}"),
                    InlineKeyboardButton("\u23ed Skip", callback_data=f"job_skip_{job_id}"),
                    InlineKeyboardButton("\U0001f4cb Details", callback_data=f"job_details_{job_id}"),
                ],
            ]
        )

        await gateway.send_message(text=text, reply_markup=keyboard)

    log.info("job_digest_sent", job_count=len(jobs))


# ---------------------------------------------------------------------------
# Callback handling (called by orchestrator / bot polling)
# ---------------------------------------------------------------------------


async def handle_job_apply(job_id: str) -> dict[str, Any]:
    """Handle the Apply button tap. Returns action info for the orchestrator.

    This doesn't execute the application directly — it returns a dict
    describing the action needed, and the caller (orchestrator or Telegram
    handler) carries out the application flow.
    """
    from db.models import JobListing
    from db.session import get_db_session
    from integrations.job_applicator import classify_apply_method

    async with get_db_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(JobListing).where(JobListing.id == UUID(job_id)))
        job = result.scalar_one_or_none()

        if job is None:
            return {"success": False, "message": f"Job {job_id} not found"}

        apply_method = job.apply_method or classify_apply_method(
            {
                "url": job.url,
                "source": job.source,
            }
        )

        # Store apply method if not already set
        if not job.apply_method:
            job.apply_method = apply_method

        return {
            "success": True,
            "job_id": str(job.id),
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "source": job.source,
            "apply_method": apply_method,
            "description": job.description,
            "external_id": job.external_id,
            "notion_page_id": job.notion_page_id,
        }


async def handle_job_skip(job_id: str) -> dict[str, Any]:
    """Handle the Skip button tap. Marks job as skipped in DB + Notion."""
    from shared.constants import JobStatus

    from db.models import JobListing
    from db.session import get_db_session

    async with get_db_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(JobListing).where(JobListing.id == UUID(job_id)))
        job = result.scalar_one_or_none()

        if job is None:
            return {"success": False, "message": f"Job {job_id} not found"}

        job.status = JobStatus.SKIPPED

        # Update Notion if synced
        if job.notion_page_id:
            try:
                from config import get_settings
                from integrations.notion_client import NotionClient

                settings = get_settings()
                notion = NotionClient(token=settings.notion_token)
                await notion.update_job_status_notion(job.notion_page_id, "Skipped")
                await notion.close()
            except Exception:
                log.warning("notion_skip_update_failed", job_id=job_id, exc_info=True)

        log.info("job_skipped", job_id=job_id, title=job.title, company=job.company)
        return {
            "success": True,
            "message": f"Skipped: {job.title} at {job.company}",
        }


async def handle_job_details(job_id: str) -> dict[str, Any]:
    """Handle the Details button tap. Returns full evaluation info."""
    from db.models import JobListing
    from db.session import get_db_session

    async with get_db_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(JobListing).where(JobListing.id == UUID(job_id)))
        job = result.scalar_one_or_none()

        if job is None:
            return {"success": False, "message": f"Job {job_id} not found"}

        return {
            "success": True,
            "title": job.title,
            "company": job.company,
            "location": job.location or "",
            "salary_range": job.salary_range or "",
            "url": job.url,
            "match_score": job.match_score,
            "match_reasons": job.match_reasons or [],
            "concerns": job.concerns or [],
            "fit_analysis": job.fit_analysis or "",
            "description": (job.description or "")[:2000],
            "apply_method": job.apply_method or "manual",
            "source": job.source,
            "status": str(job.status),
        }


async def send_job_details_message(
    gateway: TelegramGateway,
    details: dict[str, Any],
) -> None:
    """Format and send a detailed job evaluation to Telegram."""
    title = details.get("title", "Unknown")
    company = details.get("company", "Unknown")
    location = details.get("location", "")
    salary = details.get("salary_range", "")
    score = details.get("match_score", 0)
    reasons = details.get("match_reasons", [])
    concerns = details.get("concerns", [])
    fit_analysis = details.get("fit_analysis", "")
    description = details.get("description", "")
    url = details.get("url", "")
    method = details.get("apply_method", "manual")

    lines = [
        f"<b>{title} \u2014 {company}</b>",
        "",
    ]
    if location:
        lines.append(f"\U0001f4cd {location}")
    if salary:
        lines.append(f"\U0001f4b0 {salary}")
    lines.append(f"\U0001f3af Match: <b>{score}%</b>")
    lines.append(f"\U0001f527 Apply method: {method}")
    lines.append("")

    if fit_analysis:
        lines.append(f"<b>Fit Analysis:</b>\n{fit_analysis}")
        lines.append("")

    if reasons:
        lines.append("<b>Why it matches:</b>")
        for r in reasons:
            lines.append(f"  \u2022 {r}")
        lines.append("")

    if concerns:
        lines.append("<b>Concerns:</b>")
        for c in concerns:
            lines.append(f"  \u26a0\ufe0f {c}")
        lines.append("")

    if description:
        lines.append(f"<b>Description:</b>\n{description[:1000]}")
        if len(description) > 1000:
            lines.append("...")
        lines.append("")

    if url:
        lines.append(f'\U0001f517 <a href="{url}">View posting</a>')

    text = "\n".join(lines)
    await gateway.send_message(text=text)


async def send_jobs_status(gateway: TelegramGateway) -> None:
    """Send pipeline stats: count by status."""
    from sqlalchemy import func, select

    from db.models import JobListing
    from db.session import get_db_session

    async with get_db_session() as session:
        result = await session.execute(select(JobListing.status, func.count(JobListing.id)).group_by(JobListing.status))
        counts = {str(row[0]): row[1] for row in result.all()}

    total = sum(counts.values())
    new = counts.get("new", 0)
    applied = counts.get("applied", 0)
    not_applied = counts.get("not_applied", 0)
    skipped = counts.get("skipped", 0)
    expired = counts.get("expired", 0)

    text = (
        "<b>T.A.R.S. Job Pipeline Status</b>\n\n"
        f"Total tracked: <b>{total}</b>\n"
        f"\U0001f195 New: {new}\n"
        f"\u2705 Applied: {applied}\n"
        f"\U0001f4cb Not Applied (manual): {not_applied}\n"
        f"\u23ed Skipped: {skipped}\n"
        f"\u23f0 Expired: {expired}\n"
    )
    await gateway.send_message(text=text)
