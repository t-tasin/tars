from __future__ import annotations

import httpx
import structlog

from integrations.job_boards.base import JobBoardAdapter

log = structlog.get_logger(__name__)

# Algolia-powered search used by workatastartup.com
_ALGOLIA_APP_ID = "45BWZJ1SGC"
_ALGOLIA_SEARCH_KEY = "MjBjYjRiMzY0NzdhZWY0NjExY2NhZjYxMGIxYjc2MTAwNWFkNTkwNTc4NjgxYjU0YzFhYTI2MTk5OTllOTNiOHJlc3RyaWN0SW5kaWNlcz0lNUIlMjJZQ0NvbXBhbnlfcHJvZHVjdGlvbiUyMiUyQyUyMllDQ29tcGFueV9CeV9MYXVuY2hfRGF0ZV9wcm9kdWN0aW9uJTIyJTVEJnRhZ0ZpbHRlcnM9JTVCJTIyeWNfYmF0Y2hfYWxsJTIyJTVEJmFuYWx5dGljc1RhZ3M9JTVCJTIyeWNkYyUyMiU1RA=="
_ALGOLIA_URL = f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/YCCompany_production/query"


class YCWaaaSAdapter(JobBoardAdapter):
    """Y Combinator Work at a Startup (workatastartup.com) adapter.

    Uses the public Algolia search index that powers the YC company directory.
    """

    source_name = "yc_waaas"

    async def search(self, criteria: dict) -> list[dict]:
        """Search YC companies via Algolia.

        Supported criteria keys:
            query (str): Role keywords (e.g. "software engineer")
            location (str): Location filter text
            batch (str): YC batch filter (e.g. "W24", "S23")
            page (int): Page number (0-indexed, default 0)
            per_page (int): Results per page (default 20, max 50)
        """
        query = criteria.get("query", "")
        page = criteria.get("page", 0)
        per_page = min(criteria.get("per_page", 20), 50)

        filters: list[str] = []
        if criteria.get("batch"):
            filters.append(f"batch:{criteria['batch']}")
        if criteria.get("hiring", True):
            filters.append("hiring:true")

        params: dict = {
            "query": query,
            "page": page,
            "hitsPerPage": per_page,
        }
        if filters:
            params["facetFilters"] = [[f] for f in filters]

        headers = {
            "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
            "X-Algolia-API-Key": _ALGOLIA_SEARCH_KEY,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _ALGOLIA_URL,
                    json={"params": "", **params},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.error("yc_search_failed", error=str(exc))
            return []

        hits = data.get("hits", [])
        log.info(
            "yc_search_completed",
            query=query,
            total_hits=data.get("nbHits", 0),
            returned=len(hits),
        )

        location_filter = criteria.get("location", "").lower()
        results: list[dict] = []
        for hit in hits:
            jobs = hit.get("highlight_job", hit.get("jobs", []))
            if isinstance(jobs, str):
                jobs = [{"title": jobs}]
            if not jobs:
                jobs = [{}]

            company_name = hit.get("name", "")
            company_url = hit.get("website", hit.get("url", ""))
            company_location = hit.get("team_size_city", hit.get("city", ""))
            batch = hit.get("batch", "")

            for job in jobs:
                job_location = job.get("location", company_location)
                if location_filter and location_filter not in str(job_location).lower():
                    continue

                results.append(
                    {
                        "id": f"yc-{hit.get('objectID', '')}-{job.get('id', '')}",
                        "title": job.get("title", job.get("role", "Unknown Role")),
                        "company": company_name,
                        "location": job_location,
                        "salary": job.get("salary_range", ""),
                        "description": hit.get("one_liner", hit.get("long_description", "")),
                        "url": job.get("url", company_url),
                        "batch": batch,
                        "team_size": hit.get("team_size", 0),
                        "industry": hit.get("industry", ""),
                    }
                )

        return results

    def normalize(self, raw_job: dict) -> dict:
        base = super().normalize(raw_job)
        base["metadata"] = {
            "batch": raw_job.get("batch", ""),
            "team_size": raw_job.get("team_size", 0),
            "industry": raw_job.get("industry", ""),
        }
        return base
