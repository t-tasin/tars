"""Notion API client for task management and knowledge bases."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from utils.resilience import get_service_health_registry

log = structlog.get_logger()

_SERVICE_NAME = "notion"


class NotionClient:
    """Async Notion API client for querying databases and managing pages.

    Phase 2: read + write. Page creation is a side-effect action —
    callers must route through the approval queue (Tier 2) before invoking
    :meth:`create_page`.

    Includes circuit breaker integration for health reporting.
    """

    def __init__(self, token: str) -> None:
        from notion_client import AsyncClient

        self._client = AsyncClient(auth=token)
        self._cb = get_service_health_registry().get_or_create(_SERVICE_NAME)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_database(
        self,
        database_id: str,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Query a Notion database with optional filter and sorts.

        Returns a list of normalised page dicts::

            [{"id": "page-uuid", "url": "https://notion.so/...",
              "created_time": "...", "last_edited_time": "...",
              "properties": { ... }}, ...]
        """
        kwargs: dict[str, Any] = {
            "database_id": database_id,
            "page_size": page_size,
        }
        if filter is not None:
            kwargs["filter"] = filter
        if sorts is not None:
            kwargs["sorts"] = sorts

        try:
            response = await self._client.databases.query(**kwargs)
            pages = [_normalise_page(p) for p in response.get("results", [])]
            self._cb.record_success()
            log.info(
                "notion_database_queried",
                database_id=database_id,
                results=len(pages),
            )
            return pages
        except Exception as exc:
            self._cb.record_failure()
            log.error(
                "notion_query_failed",
                database_id=database_id,
                error=str(exc),
            )
            raise

    async def create_page(
        self,
        database_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a page in a Notion database.

        NOTE: This is a side-effect action — the caller must route
        through the approval queue before invoking this method.

        Returns the normalised page dict for the created page.
        """
        try:
            response = await self._client.pages.create(
                parent={"database_id": database_id},
                properties=properties,
            )
            page = _normalise_page(response)
            self._cb.record_success()
            log.info(
                "notion_page_created",
                database_id=database_id,
                page_id=page["id"],
            )
            return page
        except Exception as exc:
            self._cb.record_failure()
            log.error(
                "notion_create_failed",
                database_id=database_id,
                error=str(exc),
            )
            raise

    async def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Update page properties.

        Returns the normalised page dict for the updated page.
        """
        try:
            response = await self._client.pages.update(
                page_id=page_id,
                properties=properties,
            )
            page = _normalise_page(response)
            log.info("notion_page_updated", page_id=page_id)
            return page
        except Exception as exc:
            log.error(
                "notion_update_failed",
                page_id=page_id,
                error=str(exc),
            )
            raise

    async def get_tasks_due(
        self,
        database_id: str,
        due_date: date,
    ) -> list[dict[str, Any]]:
        """Get tasks due on a specific date.

        Queries the database for pages where a ``Due Date`` (or ``Due``)
        date property equals *due_date* and the task is not marked done.

        Returns normalised page dicts with an extra ``title`` key extracted
        from the Name/Title property for convenience.
        """
        date_str = due_date.isoformat()

        filter_payload: dict[str, Any] = {
            "and": [
                {
                    "property": "Due Date",
                    "date": {"equals": date_str},
                },
                {
                    "or": [
                        {
                            "property": "Status",
                            "status": {"does_not_equal": "Done"},
                        },
                        {
                            "property": "Status",
                            "status": {"is_empty": True},
                        },
                    ],
                },
            ],
        }

        try:
            pages = await self.query_database(
                database_id=database_id,
                filter=filter_payload,
                sorts=[{"property": "Due Date", "direction": "ascending"}],
            )
        except Exception:
            log.warning("notion_tasks_due_fallback", date=date_str)
            return []

        # Extract a convenience "title" from the properties
        for page in pages:
            page["title"] = _extract_title(page.get("properties", {}))

        log.info(
            "notion_tasks_due_fetched",
            date=date_str,
            count=len(pages),
        )
        return pages

    # ------------------------------------------------------------------
    # Job tracking
    # ------------------------------------------------------------------

    async def ensure_job_tracking_database(self, parent_page_id: str) -> str:
        """Create or find the T.A.R.S. Job Tracker database in Notion.

        Searches for an existing database titled "T.A.R.S. Job Tracker"
        under the parent page. Creates one if not found.

        Returns the database ID.
        """
        # Check if we already have a child database with this title
        try:
            children = await self._client.blocks.children.list(block_id=parent_page_id)
            for block in children.get("results", []):
                if block.get("type") == "child_database":
                    title_parts = block.get("child_database", {}).get("title", "")
                    if "Job Tracker" in title_parts:
                        db_id = block["id"]
                        log.info("notion_job_db_found", database_id=db_id)
                        return db_id
        except Exception:
            log.warning("notion_job_db_search_failed", exc_info=True)

        # Create the database
        properties: dict[str, Any] = {
            "Job Title": {"title": {}},
            "Company": {"rich_text": {}},
            "Location": {"rich_text": {}},
            "Source": {
                "select": {
                    "options": [
                        {"name": "linkedin", "color": "blue"},
                        {"name": "greenhouse", "color": "green"},
                        {"name": "google_jobs", "color": "red"},
                        {"name": "jobspy", "color": "orange"},
                        {"name": "yc_waas", "color": "yellow"},
                    ],
                },
            },
            "Match Score": {"number": {"format": "number"}},
            "Fit Score": {"number": {"format": "number"}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "New", "color": "default"},
                        {"name": "Applied", "color": "green"},
                        {"name": "Not Applied", "color": "orange"},
                        {"name": "Skipped", "color": "gray"},
                        {"name": "Expired", "color": "red"},
                    ],
                },
            },
            "Apply Method": {
                "select": {
                    "options": [
                        {"name": "api_greenhouse", "color": "green"},
                        {"name": "api_lever", "color": "blue"},
                        {"name": "api_ashby", "color": "purple"},
                        {"name": "email", "color": "orange"},
                        {"name": "manual", "color": "gray"},
                    ],
                },
            },
            "URL": {"url": {}},
            "Salary": {"rich_text": {}},
            "Date Found": {"date": {}},
            "Date Applied": {"date": {}},
            "AI Summary": {"rich_text": {}},
            "Fit Analysis": {"rich_text": {}},
            "Concerns": {"rich_text": {}},
            "Tags": {
                "multi_select": {
                    "options": [
                        {"name": "remote", "color": "blue"},
                        {"name": "ai-ml", "color": "purple"},
                        {"name": "backend", "color": "green"},
                        {"name": "fullstack", "color": "orange"},
                        {"name": "startup", "color": "yellow"},
                        {"name": "faang", "color": "red"},
                        {"name": "infrastructure", "color": "gray"},
                        {"name": "research", "color": "pink"},
                    ],
                },
            },
        }

        try:
            response = await self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": "T.A.R.S. Job Tracker"}}],
                properties=properties,
            )
            db_id = response["id"]
            self._cb.record_success()
            log.info("notion_job_db_created", database_id=db_id)
            return db_id
        except Exception as exc:
            self._cb.record_failure()
            log.error("notion_job_db_create_failed", error=str(exc))
            raise

    async def sync_job_to_notion(self, job: dict[str, Any], database_id: str) -> str:
        """Create or update a job listing in Notion. Dedup by URL.

        Returns the Notion page ID.
        """
        url = job.get("url", "")

        # Check for existing page with same URL
        if url:
            try:
                existing = await self.query_database(
                    database_id=database_id,
                    filter={"property": "URL", "url": {"equals": url}},
                )
                if existing:
                    page_id = existing[0]["id"]
                    await self._update_job_page(page_id, job)
                    return page_id
            except Exception:
                log.warning("notion_job_dedup_failed", url=url, exc_info=True)

        # Create new page
        properties = self._build_job_properties(job)
        page = await self.create_page(database_id, properties)

        log.info(
            "notion_job_synced",
            page_id=page["id"],
            title=job.get("title"),
            company=job.get("company"),
        )
        return page["id"]

    async def update_job_status_notion(
        self,
        page_id: str,
        status: str,
        date_applied: str | None = None,
    ) -> None:
        """Update job status in Notion (Applied, Not Applied, Skipped, etc.)."""
        properties: dict[str, Any] = {
            "Status": {"select": {"name": status}},
        }
        if date_applied:
            properties["Date Applied"] = {"date": {"start": date_applied}}

        await self.update_page(page_id, properties)
        log.info("notion_job_status_updated", page_id=page_id, status=status)

    def _build_job_properties(self, job: dict[str, Any]) -> dict[str, Any]:
        """Build Notion page properties from a job dict."""
        props: dict[str, Any] = {
            "Job Title": {"title": [{"text": {"content": job.get("title", "")[:100]}}]},
            "Company": {"rich_text": [{"text": {"content": job.get("company", "")[:100]}}]},
            "Status": {"select": {"name": "New"}},
        }

        if job.get("location"):
            props["Location"] = {"rich_text": [{"text": {"content": job["location"][:100]}}]}
        if job.get("source"):
            props["Source"] = {"select": {"name": job["source"]}}
        if job.get("match_score") is not None:
            props["Match Score"] = {"number": job["match_score"]}
            props["Fit Score"] = {"number": job["match_score"]}
        if job.get("apply_method"):
            props["Apply Method"] = {"select": {"name": job["apply_method"]}}
        if job.get("url"):
            props["URL"] = {"url": job["url"]}
        if job.get("salary_range"):
            props["Salary"] = {"rich_text": [{"text": {"content": job["salary_range"][:100]}}]}
        if job.get("scraped_at"):
            scraped = job["scraped_at"]
            date_str = scraped.isoformat() if hasattr(scraped, "isoformat") else str(scraped)
            props["Date Found"] = {"date": {"start": date_str[:10]}}

        # AI evaluation summary
        reasons = job.get("match_reasons", [])
        if reasons:
            summary = "; ".join(reasons)[:2000]
            props["AI Summary"] = {"rich_text": [{"text": {"content": summary}}]}

        if job.get("fit_analysis"):
            props["Fit Analysis"] = {"rich_text": [{"text": {"content": job["fit_analysis"][:2000]}}]}

        concerns = job.get("concerns", [])
        if concerns:
            concern_text = "; ".join(concerns)[:2000]
            props["Concerns"] = {"rich_text": [{"text": {"content": concern_text}}]}

        # Tags from job metadata
        tags = job.get("tags", [])
        if tags:
            props["Tags"] = {"multi_select": [{"name": t} for t in tags[:10]]}

        return props

    async def _update_job_page(self, page_id: str, job: dict[str, Any]) -> None:
        """Update an existing job page with fresh data."""
        props = self._build_job_properties(job)
        # Don't overwrite status on update
        props.pop("Status", None)
        await self.update_page(page_id, props)

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Retrieve helpers
    # ------------------------------------------------------------------

    async def get_page(self, page_id: str) -> dict[str, Any]:
        """Retrieve a single page by ID."""
        try:
            response = await self._client.pages.retrieve(page_id=page_id)
            return _normalise_page(response)
        except Exception as exc:
            log.error("notion_get_page_failed", page_id=page_id, error=str(exc))
            raise

    async def get_database(self, database_id: str) -> dict[str, Any]:
        """Retrieve database metadata (schema, title, etc.)."""
        try:
            response = await self._client.databases.retrieve(
                database_id=database_id,
            )
            return {
                "id": response.get("id", ""),
                "title": _extract_plain_text(response.get("title", [])),
                "properties": response.get("properties", {}),
                "url": response.get("url", ""),
            }
        except Exception as exc:
            log.error(
                "notion_get_database_failed",
                database_id=database_id,
                error=str(exc),
            )
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_page(page: dict[str, Any]) -> dict[str, Any]:
    """Extract a clean dict from a Notion page API response."""
    return {
        "id": page.get("id", ""),
        "url": page.get("url", ""),
        "created_time": page.get("created_time", ""),
        "last_edited_time": page.get("last_edited_time", ""),
        "properties": page.get("properties", {}),
    }


def _extract_title(properties: dict[str, Any]) -> str:
    """Extract the title string from a page's properties.

    Notion stores titles under a property of type ``"title"``.  The
    property name varies by database (commonly ``"Name"`` or ``"Title"``).
    """
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _extract_plain_text(prop.get("title", []))
    return ""


def _extract_plain_text(rich_text_array: list[dict[str, Any]]) -> str:
    """Concatenate plain_text segments from a Notion rich_text array."""
    return "".join(
        segment.get("plain_text", "")
        for segment in rich_text_array
        if isinstance(segment, dict)
    )
