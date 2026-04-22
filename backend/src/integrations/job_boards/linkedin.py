from __future__ import annotations

import httpx
import structlog

from integrations.job_boards.base import JobBoardAdapter

log = structlog.get_logger(__name__)

_SERPAPI_BASE = "https://serpapi.com/search.json"


class LinkedInAdapter(JobBoardAdapter):
    """LinkedIn job search via SerpAPI.

    Requires a valid SERPAPI_KEY. Returns empty results if not configured.
    """

    source_name = "linkedin"

    def __init__(self, serpapi_key: str = "") -> None:
        self._api_key = serpapi_key

    async def search(self, criteria: dict) -> list[dict]:
        """Search LinkedIn jobs via SerpAPI.

        Supported criteria keys:
            query (str): Job title / keywords
            location (str): Location string
            page (int): Page number (0-indexed)
        """
        if not self._api_key:
            log.warning("linkedin_search_skipped", reason="no SERPAPI_KEY configured")
            return []

        params = {
            "engine": "google_jobs",
            "q": criteria.get("query", "software engineer"),
            "location": criteria.get("location", ""),
            "chips": "employer:LinkedIn",
            "start": criteria.get("page", 0) * 10,
            "api_key": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(_SERPAPI_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error("linkedin_search_failed", error=str(exc))
            return []

        results: list[dict] = []
        for job in data.get("jobs_results", []):
            results.append(
                {
                    "id": f"linkedin-{job.get('job_id', '')}",
                    "title": job.get("title", ""),
                    "company": job.get("company_name", ""),
                    "location": job.get("location", ""),
                    "salary": job.get("salary", ""),
                    "description": job.get("description", ""),
                    "url": job.get("link", job.get("apply_link", "")),
                }
            )

        log.info("linkedin_search_completed", returned=len(results))
        return results
