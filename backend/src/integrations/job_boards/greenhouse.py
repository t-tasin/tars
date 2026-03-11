"""Greenhouse ATS adapter — polls public job boards of target companies.

Greenhouse exposes a free, unauthenticated JSON API at
``https://boards-api.greenhouse.io/v1/boards/{slug}/jobs``.
We maintain a configurable list of company slugs and poll each one,
filtering results by keyword relevance.
"""

from __future__ import annotations

import asyncio
import re
import time
from html.parser import HTMLParser
from typing import Any

import httpx
import structlog

from integrations.job_boards.base import JobBoardAdapter

log = structlog.get_logger(__name__)

_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"

_DEFAULT_COMPANIES: list[str] = [
    "anthropic", "openai", "google", "stripe", "notion", "figma",
    "vercel", "linear", "databricks", "scale", "cohere", "mistral",
    "meta", "apple", "netflix", "airbnb", "coinbase", "rippling",
    "retool", "supabase",
]

_DEFAULT_KEYWORDS: list[str] = [
    "software engineer", "ml", "ai", "machine learning", "research",
    "backend", "fullstack", "full stack", "python", "typescript",
    "data engineer", "platform engineer", "infrastructure",
]

_RATE_LIMIT_DELAY = 0.5  # seconds between company requests


class GreenhouseAdapter(JobBoardAdapter):
    """Greenhouse ATS public job board adapter.

    Polls a configurable list of companies and filters by keyword relevance.
    No authentication required.
    """

    source_name = "greenhouse"

    async def search(self, criteria: dict) -> list[dict]:
        """Search Greenhouse job boards for matching positions.

        Supported criteria keys:
            query (str): Unused directly — keyword filtering is applied post-fetch.
            companies (list[str]): Company slugs to poll (defaults to _DEFAULT_COMPANIES).
            keywords (list[str]): Keywords for relevance filtering (defaults to _DEFAULT_KEYWORDS).
        """
        companies = criteria.get("companies", _DEFAULT_COMPANIES)
        keywords = criteria.get("keywords", _DEFAULT_KEYWORDS)

        log.info("greenhouse_search_start", companies_count=len(companies))

        start = time.monotonic()
        all_jobs: list[dict] = []

        async with httpx.AsyncClient(timeout=15) as client:
            for i, slug in enumerate(companies):
                if i > 0:
                    await asyncio.sleep(_RATE_LIMIT_DELAY)

                jobs = await self._fetch_company(client, slug, keywords)
                all_jobs.extend(jobs)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "greenhouse_search_completed",
            companies_polled=len(companies),
            jobs_found=len(all_jobs),
            duration_ms=duration_ms,
        )
        return all_jobs

    async def _fetch_company(
        self,
        client: httpx.AsyncClient,
        slug: str,
        keywords: list[str],
    ) -> list[dict]:
        """Fetch and filter jobs from a single company's Greenhouse board."""
        url = f"{_GREENHOUSE_API}/{slug}/jobs"
        try:
            resp = await client.get(url, params={"content": "true"})
            if resp.status_code == 404:
                log.debug("greenhouse_board_not_found", company=slug)
                return []
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError:
            log.error("greenhouse_fetch_failed", company=slug, exc_info=True)
            return []

        jobs_data = data.get("jobs", [])
        results: list[dict] = []

        for job in jobs_data:
            title = job.get("title", "")
            content_html = job.get("content", "")
            description = _strip_html(content_html)

            if not _matches_keywords(title, description, keywords):
                continue

            location_name = ""
            if job.get("location"):
                location_name = job["location"].get("name", "")

            results.append({
                "id": f"gh-{slug}-{job.get('id', '')}",
                "title": title,
                "company": slug.replace("-", " ").title(),
                "location": location_name,
                "salary": "",
                "description": description[:3000],
                "url": job.get("absolute_url", ""),
                "updated_at": job.get("updated_at", ""),
            })

        log.debug(
            "greenhouse_company_fetched",
            company=slug,
            total=len(jobs_data),
            matched=len(results),
        )
        return results


def _matches_keywords(title: str, description: str, keywords: list[str]) -> bool:
    """Check if the job title or description matches any keyword."""
    text = f"{title} {description}".lower()
    return any(kw.lower() in text for kw in keywords)


class _HTMLStripper(HTMLParser):
    """Minimal HTML-to-text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.get_text()
    return re.sub(r"\s+", " ", text).strip()
