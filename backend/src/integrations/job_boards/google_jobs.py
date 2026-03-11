"""Google Jobs adapter — searches via SerpAPI with engine=google_jobs.

Reuses the same SERPAPI_KEY used by the LinkedIn adapter. Unlike the LinkedIn
adapter which filters by employer, this adapter searches Google Jobs broadly
for all employers.
"""

from __future__ import annotations

import httpx
import structlog

from integrations.job_boards.base import JobBoardAdapter

log = structlog.get_logger(__name__)

_SERPAPI_BASE = "https://serpapi.com/search.json"


class GoogleJobsAdapter(JobBoardAdapter):
    """Google Jobs search via SerpAPI.

    Requires a valid SERPAPI_KEY. Returns empty results if not configured.
    """

    source_name = "google_jobs"

    def __init__(self, serpapi_key: str = "") -> None:
        self._api_key = serpapi_key

    async def search(self, criteria: dict) -> list[dict]:
        """Search Google Jobs via SerpAPI.

        Supported criteria keys:
            query (str): Job title / keywords.
            location (str): Location string.
            date_posted (str): Date filter chip — "today", "3days", "week", "month".
            page (int): Page number (0-indexed).
        """
        if not self._api_key:
            log.warning("google_jobs_search_skipped", reason="no SERPAPI_KEY configured")
            return []

        query = criteria.get("query", "software engineer")
        location = criteria.get("location", "")
        date_posted = criteria.get("date_posted", "week")

        chips = f"date_posted:{date_posted}" if date_posted else ""

        params: dict = {
            "engine": "google_jobs",
            "q": query,
            "start": criteria.get("page", 0) * 10,
            "api_key": self._api_key,
        }
        if location:
            params["location"] = location
        if chips:
            params["chips"] = chips

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(_SERPAPI_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error("google_jobs_search_failed", error=str(exc))
            return []

        results: list[dict] = []
        for job in data.get("jobs_results", []):
            job_id = job.get("job_id", "")
            results.append({
                "id": f"gjobs-{job_id}" if job_id else f"gjobs-{job.get('title', '')[:30]}",
                "title": job.get("title", ""),
                "company": job.get("company_name", ""),
                "location": job.get("location", ""),
                "salary": _extract_salary(job),
                "description": job.get("description", ""),
                "url": _extract_url(job),
            })

        log.info("google_jobs_search_completed", returned=len(results), query=query)
        return results


def _extract_salary(job: dict) -> str:
    """Extract salary info from SerpAPI job result extensions."""
    for ext in job.get("detected_extensions", {}).get("salary", []):
        if isinstance(ext, str):
            return ext
    # Fallback: check extensions list for salary-like strings
    for ext in job.get("extensions", []):
        if isinstance(ext, str) and "$" in ext:
            return ext
    return ""


def _extract_url(job: dict) -> str:
    """Extract the best apply URL from the job result."""
    # SerpAPI provides apply_options with links
    apply_options = job.get("apply_options", [])
    if apply_options and isinstance(apply_options, list):
        first = apply_options[0]
        if isinstance(first, dict) and first.get("link"):
            return first["link"]
    # Fallback to share link or related links
    return job.get("share_link", job.get("link", ""))
