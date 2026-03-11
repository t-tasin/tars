"""Job scraper executor — runs heavy job scraping operations on Node 2.

Receives scraping requests from Node 1 via Redis, executes them in-process
using httpx and python-jobspy, deduplicates results, and publishes back
via Redis pub/sub.

Runs in-process (no Docker sandboxing) because job scraping only uses
trusted libraries and needs network access.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
import structlog

from .base import BaseExecutor

log = structlog.get_logger("tars.worker.executor.job_scraper")

_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
_SERPAPI_BASE = "https://serpapi.com/search.json"
_SCRAPE_TIMEOUT = 300  # 5 minutes max


class JobScraperExecutor(BaseExecutor):
    """Executes job scraping tasks dispatched from Node 1.

    Runs JobSpy, Greenhouse polling, and Google Jobs SerpAPI calls
    in-process, deduplicates results, and returns them.
    """

    EXECUTOR_TYPE = "job_scraper"

    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a job scraping task.

        Expected payload keys:
            search_criteria (dict): Keywords, location, salary_range, experience_level.
            sources (list[str]): Which adapters to use ("jobspy", "greenhouse", "google_jobs").
            max_results (int): Maximum total results to return (default 100).
            serpapi_key (str): SerpAPI key for Google Jobs (optional).
            greenhouse_companies (list[str]): Company slugs for Greenhouse (optional).
        """
        search_criteria = payload.get("search_criteria", {})
        sources = payload.get("sources", ["jobspy", "greenhouse", "google_jobs"])
        max_results = payload.get("max_results", 100)
        serpapi_key = payload.get("serpapi_key", "")
        greenhouse_companies = payload.get("greenhouse_companies")

        start = time.monotonic()
        log.info(
            "job_scraper_start",
            job_id=job_id,
            sources=sources,
            query=search_criteria.get("keywords", ""),
        )

        # Run all requested sources in parallel
        tasks: list[asyncio.Task[list[dict]]] = []

        if "jobspy" in sources:
            tasks.append(asyncio.create_task(
                self._scrape_jobspy(search_criteria),
                name="jobspy",
            ))

        if "greenhouse" in sources:
            tasks.append(asyncio.create_task(
                self._scrape_greenhouse(search_criteria, greenhouse_companies),
                name="greenhouse",
            ))

        if "google_jobs" in sources and serpapi_key:
            tasks.append(asyncio.create_task(
                self._scrape_google_jobs(search_criteria, serpapi_key),
                name="google_jobs",
            ))

        # Gather with timeout
        all_results: list[dict] = []
        try:
            source_results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_SCRAPE_TIMEOUT,
            )
            for i, result in enumerate(source_results):
                if isinstance(result, Exception):
                    log.error(
                        "job_scraper_source_failed",
                        job_id=job_id,
                        source=tasks[i].get_name(),
                        error=str(result),
                    )
                else:
                    all_results.extend(result)
        except asyncio.TimeoutError:
            log.error("job_scraper_timeout", job_id=job_id, timeout=_SCRAPE_TIMEOUT)
            # Cancel remaining tasks
            for task in tasks:
                task.cancel()

        # Deduplicate
        deduped = _deduplicate(all_results)

        # Truncate to max_results
        deduped = deduped[:max_results]

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "job_scraper_completed",
            job_id=job_id,
            total_raw=len(all_results),
            deduped=len(deduped),
            duration_ms=duration_ms,
        )

        return {
            "status": "completed",
            "success": True,
            "job_id": job_id,
            "listings": deduped,
            "total_raw": len(all_results),
            "total_deduped": len(deduped),
            "duration_ms": duration_ms,
        }

    # ------------------------------------------------------------------
    # Source scrapers
    # ------------------------------------------------------------------

    async def _scrape_jobspy(self, criteria: dict) -> list[dict]:
        """Run JobSpy in a thread (synchronous library)."""
        try:
            from jobspy import scrape_jobs
        except ImportError:
            log.error("jobspy_not_installed")
            return []

        keywords = criteria.get("keywords", "software engineer")
        location = criteria.get("location", "")

        kwargs: dict[str, Any] = {
            "site_name": ["indeed", "glassdoor", "zip_recruiter"],
            "search_term": keywords,
            "results_wanted": 30,
            "hours_old": 72,
        }
        if location:
            kwargs["location"] = location

        try:
            df = await asyncio.to_thread(scrape_jobs, **kwargs)
        except Exception:
            log.error("jobspy_scrape_failed", exc_info=True)
            return []

        if df is None or (hasattr(df, "empty") and df.empty):
            log.info("jobspy_scrape_done", count=0)
            return []

        results: list[dict] = []
        for _, row in df.iterrows():
            results.append({
                "source": "jobspy",
                "external_id": f"jobspy-{row.get('id', '')}",
                "title": str(row.get("title", "")),
                "company": str(row.get("company_name", row.get("company", ""))),
                "location": str(row.get("location", "")),
                "salary_range": _format_salary(row.get("min_amount"), row.get("max_amount"), row.get("interval", "")),
                "description": str(row.get("description", "")),
                "url": str(row.get("job_url", row.get("link", ""))),
            })

        log.info("jobspy_scrape_done", count=len(results))
        return results

    async def _scrape_greenhouse(
        self,
        criteria: dict,
        companies: list[str] | None,
    ) -> list[dict]:
        """Poll Greenhouse boards for matching jobs."""
        if companies is None:
            companies = [
                "anthropic", "openai", "google", "stripe", "notion", "figma",
                "vercel", "linear", "databricks", "scale", "cohere", "mistral",
                "meta", "apple", "netflix", "airbnb", "coinbase", "rippling",
                "retool", "supabase",
            ]

        keywords = [
            kw.strip().lower()
            for kw in criteria.get("keywords", "software engineer,ml,ai,backend,python").split(",")
            if kw.strip()
        ]
        results: list[dict] = []

        async with httpx.AsyncClient(timeout=15) as client:
            for i, slug in enumerate(companies):
                if i > 0:
                    await asyncio.sleep(0.5)
                try:
                    resp = await client.get(
                        f"{_GREENHOUSE_API}/{slug}/jobs",
                        params={"content": "true"},
                    )
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError:
                    log.error("greenhouse_fetch_failed", company=slug, exc_info=True)
                    continue

                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    description = _strip_html(job.get("content", ""))
                    text = f"{title} {description}".lower()
                    if not any(kw in text for kw in keywords):
                        continue

                    location_name = ""
                    if job.get("location"):
                        location_name = job["location"].get("name", "")

                    results.append({
                        "source": "greenhouse",
                        "external_id": f"gh-{slug}-{job.get('id', '')}",
                        "title": title,
                        "company": slug.replace("-", " ").title(),
                        "location": location_name,
                        "salary_range": "",
                        "description": description[:3000],
                        "url": job.get("absolute_url", ""),
                    })

        log.info("greenhouse_scrape_done", count=len(results))
        return results

    async def _scrape_google_jobs(self, criteria: dict, serpapi_key: str) -> list[dict]:
        """Search Google Jobs via SerpAPI."""
        query = criteria.get("keywords", "software engineer")
        location = criteria.get("location", "")

        params: dict = {
            "engine": "google_jobs",
            "q": query,
            "chips": "date_posted:week",
            "api_key": serpapi_key,
        }
        if location:
            params["location"] = location

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(_SERPAPI_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError:
            log.error("google_jobs_scrape_failed", exc_info=True)
            return []

        results: list[dict] = []
        for job in data.get("jobs_results", []):
            job_id = job.get("job_id", "")
            results.append({
                "source": "google_jobs",
                "external_id": f"gjobs-{job_id}" if job_id else "",
                "title": job.get("title", ""),
                "company": job.get("company_name", ""),
                "location": job.get("location", ""),
                "salary_range": "",
                "description": job.get("description", ""),
                "url": job.get("share_link", ""),
            })

        log.info("google_jobs_scrape_done", count=len(results))
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deduplicate(listings: list[dict]) -> list[dict]:
    """Remove duplicates by normalized title + company."""
    seen: set[str] = set()
    unique: list[dict] = []
    for listing in listings:
        key = _dedup_key(listing.get("title", ""), listing.get("company", ""))
        if key not in seen:
            seen.add(key)
            unique.append(listing)
    return unique


def _dedup_key(title: str, company: str) -> str:
    """Normalized key for deduplication."""
    def clean(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"\b(inc|llc|ltd|corp|co)\b\.?", "", s)
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    return f"{clean(title)}|{clean(company)}"


def _format_salary(min_amt: Any, max_amt: Any, interval: str = "") -> str:
    """Format salary from min/max amounts."""
    try:
        if min_amt and max_amt:
            return f"${int(min_amt):,}-${int(max_amt):,} {interval}".strip()
        if min_amt:
            return f"${int(min_amt):,}+ {interval}".strip()
        if max_amt:
            return f"Up to ${int(max_amt):,} {interval}".strip()
    except (ValueError, TypeError):
        pass
    return ""


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()
