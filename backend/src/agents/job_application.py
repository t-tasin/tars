"""Job Application Agent — handles the Apply flow when user taps Apply.

Orchestrates:
1. Classify apply method
2. For manual jobs → mark NOT_APPLIED, notify user
3. For auto-apply → draft cover letter (Claude), create approval, submit on approval

Every auto-apply goes through the approval system (HC-01).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from shared.constants import JobStatus

from agents.base import AgentContext, AgentResult, BaseAgent

log = structlog.get_logger()


class JobApplicationAgent(BaseAgent):
    """Handles job application flow triggered by Apply button."""

    agent_type = "job_application"

    async def execute(self, context: AgentContext) -> AgentResult:
        """Execute the apply flow for a specific job."""
        job_id = context.config.get("job_id")
        if not job_id:
            return AgentResult(success=False, text="No job ID provided.", error="no_job_id")

        from sqlalchemy import select

        from db.models import JobListing
        from db.session import get_db_session
        from integrations.job_applicator import classify_apply_method

        async with get_db_session() as session:
            result = await session.execute(select(JobListing).where(JobListing.id == UUID(job_id)))
            job = result.scalar_one_or_none()

        if job is None:
            return AgentResult(success=False, text=f"Job {job_id} not found.", error="not_found")

        apply_method = job.apply_method or classify_apply_method(
            {
                "url": job.url,
                "source": job.source,
            }
        )

        job_dict = {
            "id": str(job.id),
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "source": job.source,
            "description": job.description,
            "external_id": job.external_id,
            "salary_range": job.salary_range,
            "location": job.location,
            "notion_page_id": job.notion_page_id,
        }

        if apply_method == "manual":
            return await self._handle_manual(job_dict)
        else:
            return await self._handle_auto_apply(job_dict, apply_method, context)

    async def _handle_manual(self, job: dict[str, Any]) -> AgentResult:
        """Manual apply: mark as NOT_APPLIED, link to Notion."""
        from sqlalchemy import update

        from db.models import JobListing
        from db.session import get_db_session

        async with get_db_session() as session:
            await session.execute(
                update(JobListing).where(JobListing.id == UUID(job["id"])).values(status=JobStatus.NOT_APPLIED)
            )

        # Update Notion status
        if job.get("notion_page_id"):
            try:
                from config import get_settings
                from integrations.notion_client import NotionClient

                settings = get_settings()
                notion = NotionClient(token=settings.notion_token)
                await notion.update_job_status_notion(job["notion_page_id"], "Not Applied")
                await notion.close()
            except Exception:
                log.warning("notion_manual_update_failed", exc_info=True)

        text = f"Listed for manual apply: {job['title']} at {job['company']}\nLink: {job['url']}"

        log.info(
            "job_manual_apply",
            job_id=job["id"],
            title=job["title"],
            company=job["company"],
        )

        # No side effects → no approval needed
        return AgentResult(
            success=True,
            text=text,
            data={"job_id": job["id"], "apply_method": "manual"},
        )

    async def _handle_auto_apply(
        self,
        job: dict[str, Any],
        apply_method: str,
        context: AgentContext,
    ) -> AgentResult:
        """Auto-apply: draft cover letter with Claude, create approval."""
        from db.repositories.config import ConfigRepository
        from db.session import get_db_session

        # 1. Pull Q&A bank
        async with get_db_session() as session:
            config_repo = ConfigRepository(session)
            qa_bank = await config_repo.get_namespace("job_application")

        # 2. Draft cover letter with Claude
        cover_letter = await self._draft_cover_letter(job, qa_bank)

        # 3. Update DB status to APPLYING
        async with get_db_session() as session:
            from sqlalchemy import update

            from db.models import JobListing

            await session.execute(
                update(JobListing).where(JobListing.id == UUID(job["id"])).values(status=JobStatus.APPLYING)
            )

        # 4. Return approval-required result (HC-01)
        return AgentResult(
            success=True,
            text=f"Ready to apply to {job['title']} at {job['company']}. Please review:",
            content_type="approval",
            has_side_effects=True,
            action_type="apply_job",
            approval_title=f"Apply to {job['title']} at {job['company']}",
            preview={
                "job_id": job["id"],
                "job_title": job["title"],
                "company": job["company"],
                "apply_method": apply_method,
                "cover_letter": cover_letter,
                "resume": qa_bank.get("resume_path", "resume.pdf"),
                "url": job["url"],
            },
            data={
                "job_id": job["id"],
                "apply_method": apply_method,
                "qa_bank": qa_bank,
            },
        )

    async def _draft_cover_letter(
        self,
        job: dict[str, Any],
        qa_bank: dict[str, Any],
    ) -> str:
        """Draft a cover letter using Claude Code."""
        from models.claude_spawner import ClaudeCodeSpawner, ClaudeSpawnError

        prompt = (
            "You are drafting a cover letter for a job application on behalf of the user.\n\n"
            f"## Applicant Info\n"
            f"Name: {qa_bank.get('full_name', '')}\n"
            f"Current Title: {qa_bank.get('current_title', '')}\n"
            f"Experience: {qa_bank.get('years_of_experience', '')} years\n"
            f"Education: {qa_bank.get('education', '')}\n"
            f"Skills: {', '.join(qa_bank.get('skills', []))}\n"
            f"Highlights: {qa_bank.get('cover_letter_highlights', '')}\n"
            f"Research Interests: {qa_bank.get('research_interests', '')}\n\n"
            f"## Job\n"
            f"Title: {job.get('title', '')}\n"
            f"Company: {job.get('company', '')}\n"
            f"Description:\n{(job.get('description', '') or '')[:2000]}\n\n"
            f"## Instructions\n"
            f"Write a concise, compelling cover letter (3-4 paragraphs). "
            f"Be specific about why this role is a great fit. "
            f"Reference relevant experience and skills. "
            f"Do NOT use placeholder brackets. "
            f"Sign off as {qa_bank.get('full_name', 'the applicant')}."
        )

        try:
            spawner = ClaudeCodeSpawner()
            result = await spawner.execute(
                prompt=prompt,
                mcp_profile="communication",
                timeout=90,
                max_turns=3,
            )

            if result.success and result.text.strip():
                log.info(
                    "cover_letter_drafted",
                    job_title=job.get("title"),
                    company=job.get("company"),
                    duration_ms=result.duration_ms,
                )
                return result.text.strip()

        except ClaudeSpawnError:
            log.error("cover_letter_claude_failed", exc_info=True)
        except Exception:
            log.error("cover_letter_draft_failed", exc_info=True)

        # Fallback: no cover letter
        log.warning("cover_letter_fallback", job_title=job.get("title"))
        return ""


# ---------------------------------------------------------------------------
# Post-approval execution
# ---------------------------------------------------------------------------


async def execute_approved_application(approval: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved job application.

    Called by the orchestrator after the user approves the application.
    Submits via the appropriate ATS API or email, then updates DB + Notion.
    """
    from sqlalchemy import select, update

    from db.models import AuditLog, JobApplication, JobListing
    from db.session import get_db_session
    from integrations.job_applicator import JobApplicator

    preview = approval.get("preview_payload", {})
    job_id = preview.get("job_id")
    apply_method = preview.get("apply_method", "manual")
    cover_letter = preview.get("cover_letter", "")

    if not job_id:
        return {"success": False, "message": "No job ID in approval payload"}

    # Fetch job
    async with get_db_session() as session:
        result = await session.execute(select(JobListing).where(JobListing.id == UUID(job_id)))
        job = result.scalar_one_or_none()

    if job is None:
        return {"success": False, "message": f"Job {job_id} not found"}

    job_dict = {
        "id": str(job.id),
        "title": job.title,
        "company": job.company,
        "url": job.url,
        "source": job.source,
        "external_id": job.external_id,
        "apply_email": None,  # populated if email apply
    }

    # Use edited payload if user modified the cover letter
    edited = approval.get("edited_payload")
    if edited:
        cover_letter = edited.get("cover_letter", cover_letter)

    from db.repositories.config import ConfigRepository

    async with get_db_session() as session:
        config_repo = ConfigRepository(session)
        qa_bank = await config_repo.get_namespace("job_application")

    application = {
        "cover_letter": cover_letter,
        "resume_path": qa_bank.get("resume_path", ""),
        "qa_bank": qa_bank,
    }

    # Submit
    applicator = JobApplicator()
    submit_result = await applicator.apply(job_dict, application, apply_method)

    now = datetime.now(UTC)

    if submit_result.get("success"):
        new_status = JobStatus.APPLIED
        notion_status = "Applied"
    else:
        new_status = JobStatus.NOT_APPLIED
        notion_status = "Not Applied"

    # Update DB
    async with get_db_session() as session:
        await session.execute(
            update(JobListing)
            .where(JobListing.id == UUID(job_id))
            .values(
                status=new_status,
                applied_at=now if submit_result.get("success") else None,
            )
        )

        # Create application record
        app_record = JobApplication(
            job_listing_id=UUID(job_id),
            cover_letter=cover_letter,
            resume_version=qa_bank.get("resume_path", ""),
            status="submitted" if submit_result.get("success") else "failed",
            approval_id=UUID(approval["id"]) if approval.get("id") else None,
            notes=submit_result.get("message", ""),
        )
        session.add(app_record)

        # HC-08: audit log
        session.add(
            AuditLog(
                action_type="job_application_submitted",
                actor="tars",
                target=f"{job.title} at {job.company}",
                details={
                    "job_id": job_id,
                    "method": apply_method,
                    "success": submit_result.get("success"),
                    "message": submit_result.get("message", ""),
                },
            )
        )

    # Update Notion
    if job.notion_page_id:
        try:
            from config import get_settings
            from integrations.notion_client import NotionClient

            settings = get_settings()
            notion = NotionClient(token=settings.notion_token)
            date_applied = now.strftime("%Y-%m-%d") if submit_result.get("success") else None
            await notion.update_job_status_notion(job.notion_page_id, notion_status, date_applied)
            await notion.close()
        except Exception:
            log.warning("notion_apply_update_failed", exc_info=True)

    # Notify via Telegram
    try:
        from integrations.notification_service import get_notification_service

        notifier = get_notification_service()

        if submit_result.get("success"):
            await notifier.telegram.send_message(
                text=(f"\u2705 <b>Applied!</b>\n{job.title} at {job.company}\nMethod: {apply_method}"),
            )
        else:
            await notifier.telegram.send_message(
                text=(
                    f"\u26a0\ufe0f <b>Auto-apply failed</b>\n"
                    f"{job.title} at {job.company}\n"
                    f"{submit_result.get('message', '')}\n"
                    f"Listed for manual apply: {job.url}"
                ),
            )
    except Exception:
        log.warning("telegram_apply_notify_failed", exc_info=True)

    log.info(
        "job_application_completed",
        job_id=job_id,
        method=apply_method,
        success=submit_result.get("success"),
    )

    return submit_result
