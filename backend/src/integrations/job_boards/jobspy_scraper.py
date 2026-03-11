"""JobSpy adapter — scrapes Indeed, Glassdoor, and ZipRecruiter concurrently.

Uses the python-jobspy library which is synchronous, so all calls are
wrapped in ``asyncio.to_thread()`` to avoid blocking the event loop.

LinkedIn is excluded from JobSpy's site list because we have a dedicated
LinkedIn adapter via SerpAPI.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from integrations.job_boards.base import JobBoardAdapter

log = structlog.get_logger(__name__)

_DEFAULT_SITES: list[str] = ["indeed", "glassdoor", "zip_recruiter"]


class JobSpyScraper(JobBoardAdapter):
    """Multi-board job scraper via python-jobspy.

    Scrapes Indeed, Glassdoor, and ZipRecruiter concurrently.
    LinkedIn is intentionally excluded — we use a dedicated SerpAPI adapter.
    """

    source_name = "jobspy"

    async def search(self, criteria: dict) -> list[dict]:
        """Search multiple job boards via JobSpy.

        Supported criteria keys:
            query (str): Search term / job title keywords.
            location (str): Location string (e.g. "San Francisco, CA").
            site_names (list[str]): Override which sites to scrape.
            results_wanted (int): Max results per site (default 25).
            hours_old (int): Only show listings from the last N hours (default 72).
        """
        search_term = criteria.get("query", "software engineer")
        location = criteria.get("location", "")
        site_names = criteria.get("site_names", _DEFAULT_SITES)
        results_wanted = criteria.get("results_wanted", 25)
        hours_old = criteria.get("hours_old", 72)

        log.info(
            "jobspy_search_start",
            search_term=search_term,
            location=location,
            sites=site_names,
            results_wanted=results_wanted,
        )

        try:
            jobs_df = await asyncio.to_thread(
                _run_jobspy,
                search_term=search_term,
                location=location,
                site_names=site_names,
                results_wanted=results_wanted,
                hours_old=hours_old,
            )
        except Exception:
            log.error("jobspy_search_failed", exc_info=True)
            return []

        if jobs_df is None or (hasattr(jobs_df, "empty") and jobs_df.empty):
            log.info("jobspy_search_completed", returned=0, sites=site_names)
            return []

        results: list[dict] = []
        for _, row in jobs_df.iterrows():
            results.append({
                "id": f"jobspy-{row.get('id', '')}" if row.get("id") else f"jobspy-{row.get('title', '')[:30]}",
                "title": str(row.get("title", "")),
                "company": str(row.get("company_name", row.get("company", ""))),
                "location": str(row.get("location", "")),
                "salary": _extract_salary(row),
                "description": str(row.get("description", "")),
                "url": str(row.get("job_url", row.get("link", ""))),
                "site": str(row.get("site", "")),
            })

        log.info("jobspy_search_completed", returned=len(results), sites=site_names)
        return results

    def normalize(self, raw_job: dict) -> dict:
        base = super().normalize(raw_job)
        base["metadata"] = {"site": raw_job.get("site", "")}
        return base


def _run_jobspy(
    *,
    search_term: str,
    location: str,
    site_names: list[str],
    results_wanted: int,
    hours_old: int,
) -> Any:
    """Synchronous wrapper around jobspy.scrape_jobs(). Runs in a thread."""
    from jobspy import scrape_jobs

    kwargs: dict[str, Any] = {
        "site_name": site_names,
        "search_term": search_term,
        "results_wanted": results_wanted,
        "hours_old": hours_old,
    }
    if location:
        kwargs["location"] = location

    return scrape_jobs(**kwargs)


def _extract_salary(row: Any) -> str:
    """Build a salary string from JobSpy's min/max/interval columns."""
    min_amt = row.get("min_amount")
    max_amt = row.get("max_amount")
    interval = row.get("interval", "")

    if min_amt and max_amt:
        return f"${int(min_amt):,}-${int(max_amt):,} {interval}".strip()
    if min_amt:
        return f"${int(min_amt):,}+ {interval}".strip()
    if max_amt:
        return f"Up to ${int(max_amt):,} {interval}".strip()
    return ""
