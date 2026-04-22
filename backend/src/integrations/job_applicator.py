"""Job application submission via ATS APIs or email.

Supports Greenhouse (public API), Lever, Ashby, and email-based
applications.  Every submission must go through the approval system
first (HC-01) — this module only handles the mechanics of submitting
an already-approved application.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Apply-method classifier
# ---------------------------------------------------------------------------


def classify_apply_method(job: dict[str, Any]) -> str:
    """Classify how to apply for a job based on URL and source.

    Returns one of: api_greenhouse, api_lever, api_ashby, email, manual.
    """
    url = (job.get("url") or "").lower()
    source = (job.get("source") or "").lower()

    if "greenhouse.io" in url or source == "greenhouse":
        return "api_greenhouse"
    if "lever.co" in url:
        return "api_lever"
    if "ashbyhq.com" in url:
        return "api_ashby"
    if job.get("apply_email"):
        return "email"
    return "manual"


# ---------------------------------------------------------------------------
# Applicator
# ---------------------------------------------------------------------------


class JobApplicator:
    """Submits job applications via ATS public APIs or email.

    All methods assume the application has already been approved (HC-01).
    The caller is responsible for creating the approval and waiting for
    the user's decision before calling :meth:`apply`.
    """

    def __init__(
        self,
        gmail_client: Any | None = None,
    ) -> None:
        self._gmail = gmail_client

    async def apply(
        self,
        job: dict[str, Any],
        application: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        """Route to the appropriate apply method.

        Parameters
        ----------
        job:
            Job listing dict (title, company, url, etc.).
        application:
            Application payload (cover_letter, resume_path, qa_bank, etc.).
        method:
            One of api_greenhouse, api_lever, api_ashby, email, manual.

        Returns
        -------
        dict with ``success: bool`` and ``message: str``.
        """
        dispatch = {
            "api_greenhouse": self._apply_greenhouse,
            "api_lever": self._apply_lever,
            "api_ashby": self._apply_ashby,
            "email": self._apply_email,
        }

        handler = dispatch.get(method)
        if handler is None:
            return {"success": False, "message": f"Cannot auto-apply with method: {method}"}

        try:
            result = await handler(job, application)
            log.info(
                "job_application_submitted",
                method=method,
                company=job.get("company"),
                title=job.get("title"),
                success=result.get("success"),
            )
            return result
        except Exception as exc:
            log.error(
                "job_application_failed",
                method=method,
                company=job.get("company"),
                error=str(exc),
                exc_info=True,
            )
            return {"success": False, "message": f"Application failed: {exc}"}

    # ------------------------------------------------------------------
    # Greenhouse — public application API
    # ------------------------------------------------------------------

    async def _apply_greenhouse(
        self,
        job: dict[str, Any],
        application: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit via Greenhouse public apply API.

        The Greenhouse public job board API accepts multipart form data
        at ``POST https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}``.
        """
        external_id = job.get("external_id", "")
        qa = application.get("qa_bank", {})

        # Parse slug and job_id from external_id (format: gh-{slug}-{job_id})
        parts = external_id.split("-", 2) if external_id.startswith("gh-") else []
        if len(parts) < 3:
            return {"success": False, "message": "Cannot parse Greenhouse job ID from external_id"}

        slug = parts[1]
        job_id = parts[2]
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"

        # Build form data
        form_data: dict[str, Any] = {
            "first_name": qa.get("full_name", "").split()[0] if qa.get("full_name") else "",
            "last_name": " ".join(qa.get("full_name", "").split()[1:]) if qa.get("full_name") else "",
            "email": qa.get("email", ""),
            "phone": qa.get("phone", ""),
            "location": qa.get("location", ""),
            "linkedin_profile_url": qa.get("linkedin_url", ""),
            "website_url": qa.get("portfolio_url", ""),
        }

        if application.get("cover_letter"):
            form_data["cover_letter"] = application["cover_letter"]

        # Build multipart request
        async with httpx.AsyncClient(timeout=30) as client:
            files: dict[str, Any] = {}

            # Attach resume if available
            resume_path = application.get("resume_path") or qa.get("resume_path", "")
            if resume_path and Path(resume_path).exists():
                resume_bytes = await asyncio.to_thread(Path(resume_path).read_bytes)
                files["resume"] = ("resume.pdf", resume_bytes, "application/pdf")

            resp = await client.post(api_url, data=form_data, files=files or None)

        if resp.status_code in (200, 201):
            log.info("greenhouse_apply_success", slug=slug, job_id=job_id)
            return {"success": True, "message": f"Applied via Greenhouse ({slug})"}

        log.error(
            "greenhouse_apply_http_error",
            status=resp.status_code,
            body=resp.text[:500],
        )
        return {
            "success": False,
            "message": f"Greenhouse API returned {resp.status_code}: {resp.text[:200]}",
        }

    # ------------------------------------------------------------------
    # Lever — stub
    # ------------------------------------------------------------------

    async def _apply_lever(
        self,
        job: dict[str, Any],
        application: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit via Lever public apply API.

        Lever's posting apply endpoint:
        POST https://api.lever.co/v0/postings/{company}/{posting_id}
        """
        log.warning("lever_apply_not_implemented", url=job.get("url"))
        return {"success": False, "message": "Lever auto-apply not yet implemented. Listed for manual apply."}

    # ------------------------------------------------------------------
    # Ashby — stub
    # ------------------------------------------------------------------

    async def _apply_ashby(
        self,
        job: dict[str, Any],
        application: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit via Ashby public apply API."""
        log.warning("ashby_apply_not_implemented", url=job.get("url"))
        return {"success": False, "message": "Ashby auto-apply not yet implemented. Listed for manual apply."}

    # ------------------------------------------------------------------
    # Email — via Gmail client
    # ------------------------------------------------------------------

    async def _apply_email(
        self,
        job: dict[str, Any],
        application: dict[str, Any],
    ) -> dict[str, Any]:
        """Send application email via Gmail with resume attachment."""
        if self._gmail is None:
            return {"success": False, "message": "Gmail client not available for email applications"}

        apply_email = job.get("apply_email", "")
        if not apply_email:
            return {"success": False, "message": "No apply email address found for this job"}

        qa = application.get("qa_bank", {})
        cover_letter = application.get("cover_letter", "")
        company = job.get("company", "Unknown")
        title = job.get("title", "Position")

        subject = f"Application: {title} — {qa.get('full_name', 'Applicant')}"

        body = cover_letter or f"Please find my application for the {title} position at {company}."
        body += f"\n\nBest regards,\n{qa.get('full_name', '')}"

        # Build email with attachment
        resume_path = application.get("resume_path") or qa.get("resume_path", "")

        if resume_path and Path(resume_path).exists():
            result = await self._gmail.send_email_with_attachment(
                to=apply_email,
                subject=subject,
                body=body,
                attachment_path=resume_path,
                attachment_name="resume.pdf",
            )
        else:
            result = await self._gmail.send_email(
                to=apply_email,
                subject=subject,
                body=body,
            )

        return {"success": True, "message": f"Application email sent to {apply_email}", **result}
