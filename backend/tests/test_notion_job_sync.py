"""Tests for Notion job tracking: database creation, sync, dedup, and status updates."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch resilience module before importing NotionClient so the circuit
# breaker does not need a real registry.
_fake_cb = MagicMock()
_fake_cb.record_success = MagicMock()
_fake_cb.record_failure = MagicMock()

_fake_registry = MagicMock()
_fake_registry.get_or_create = MagicMock(return_value=_fake_cb)

with patch("utils.resilience.get_service_health_registry", return_value=_fake_registry):
    from integrations.notion_client import NotionClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notion_client() -> NotionClient:
    """Create a NotionClient with a fully-mocked internal AsyncClient."""
    with patch("integrations.notion_client.get_service_health_registry", return_value=_fake_registry):
        with patch("notion_client.AsyncClient"):
            client = NotionClient(token="test-token")
    # Replace the internal client with a fresh AsyncMock for each test
    client._client = AsyncMock()
    return client


def _page_response(page_id: str = "page-abc-123", **kwargs: Any) -> dict[str, Any]:
    """Simulate a Notion API page response."""
    base: dict[str, Any] = {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "created_time": "2026-03-10T00:00:00.000Z",
        "last_edited_time": "2026-03-10T00:00:00.000Z",
        "properties": {},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# ensure_job_tracking_database
# ---------------------------------------------------------------------------


class TestEnsureJobTrackingDatabase:
    """Test database discovery and creation."""

    @pytest.mark.asyncio
    async def test_ensure_job_tracking_database_found(self) -> None:
        """If a child database named 'Job Tracker' exists, return its ID."""
        client = _make_notion_client()

        client._client.blocks.children.list = AsyncMock(
            return_value={
                "results": [
                    {
                        "type": "child_database",
                        "id": "existing-db-id",
                        "child_database": {"title": "T.A.R.S. Job Tracker"},
                    },
                ],
            }
        )

        db_id = await client.ensure_job_tracking_database("parent-page-id")
        assert db_id == "existing-db-id"
        # Should NOT call databases.create
        client._client.databases.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_job_tracking_database_creates(self) -> None:
        """If no existing database found, create one and return the new ID."""
        client = _make_notion_client()

        # No matching child databases
        client._client.blocks.children.list = AsyncMock(return_value={"results": []})
        client._client.databases.create = AsyncMock(return_value={"id": "new-db-id"})

        db_id = await client.ensure_job_tracking_database("parent-page-id")
        assert db_id == "new-db-id"
        client._client.databases.create.assert_awaited_once()


# ---------------------------------------------------------------------------
# sync_job_to_notion
# ---------------------------------------------------------------------------


class TestSyncJobToNotion:
    """Test creating and deduplicating job pages."""

    @pytest.mark.asyncio
    async def test_sync_job_to_notion_new(self) -> None:
        """A job with no existing Notion page should create a new page."""
        client = _make_notion_client()

        # query_database returns empty (no existing page with this URL)
        client._client.databases.query = AsyncMock(return_value={"results": []})
        client._client.pages.create = AsyncMock(return_value=_page_response("new-page-id"))

        job: dict[str, Any] = {
            "title": "ML Engineer",
            "company": "DeepMind",
            "url": "https://deepmind.com/careers/123",
            "source": "greenhouse",
            "match_score": 90,
            "location": "London",
        }

        page_id = await client.sync_job_to_notion(job, "db-id-123")
        assert page_id == "new-page-id"
        client._client.pages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_job_to_notion_dedup(self) -> None:
        """A job whose URL already exists in Notion should update, not create."""
        client = _make_notion_client()

        # query_database returns an existing page
        existing_page = _page_response("existing-page-id")
        client._client.databases.query = AsyncMock(return_value={"results": [existing_page]})
        client._client.pages.update = AsyncMock(return_value=existing_page)

        job: dict[str, Any] = {
            "title": "ML Engineer",
            "company": "DeepMind",
            "url": "https://deepmind.com/careers/123",
            "source": "greenhouse",
            "match_score": 95,
        }

        page_id = await client.sync_job_to_notion(job, "db-id-123")
        assert page_id == "existing-page-id"
        # Should update, not create
        client._client.pages.update.assert_awaited_once()
        client._client.pages.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# update_job_status_notion
# ---------------------------------------------------------------------------


class TestUpdateJobStatusNotion:
    """Test status updates on existing Notion pages."""

    @pytest.mark.asyncio
    async def test_update_job_status_notion(self) -> None:
        """Verify update_page is called with the correct status property."""
        client = _make_notion_client()
        client._client.pages.update = AsyncMock(return_value=_page_response("page-xyz"))

        await client.update_job_status_notion("page-xyz", "Applied", date_applied="2026-03-10")

        client._client.pages.update.assert_awaited_once()
        call_kwargs = client._client.pages.update.call_args.kwargs
        props = call_kwargs.get("properties", {})
        assert props["Status"] == {"select": {"name": "Applied"}}
        assert props["Date Applied"] == {"date": {"start": "2026-03-10"}}


# ---------------------------------------------------------------------------
# _build_job_properties
# ---------------------------------------------------------------------------


class TestBuildJobProperties:
    """Test the Notion property builder."""

    def test_build_job_properties(self) -> None:
        """Verify all expected fields are built from a complete job dict."""
        client = _make_notion_client()

        job: dict[str, Any] = {
            "title": "Backend Engineer",
            "company": "Stripe",
            "location": "San Francisco",
            "source": "linkedin",
            "match_score": 88,
            "apply_method": "manual",
            "url": "https://stripe.com/jobs/123",
            "salary_range": "$180k-$220k",
            "match_reasons": ["Strong backend skills", "Payments experience"],
            "fit_analysis": "Great fit for your background.",
            "concerns": ["Requires relocation"],
            "tags": ["backend", "startup"],
        }

        props = client._build_job_properties(job)

        assert props["Job Title"]["title"][0]["text"]["content"] == "Backend Engineer"
        assert props["Company"]["rich_text"][0]["text"]["content"] == "Stripe"
        assert props["Location"]["rich_text"][0]["text"]["content"] == "San Francisco"
        assert props["Source"]["select"]["name"] == "linkedin"
        assert props["Match Score"]["number"] == 88
        assert props["Apply Method"]["select"]["name"] == "manual"
        assert props["URL"]["url"] == "https://stripe.com/jobs/123"
        assert props["Salary"]["rich_text"][0]["text"]["content"] == "$180k-$220k"
        assert "Strong backend skills" in props["AI Summary"]["rich_text"][0]["text"]["content"]
        assert "Great fit" in props["Fit Analysis"]["rich_text"][0]["text"]["content"]
        assert "relocation" in props["Concerns"]["rich_text"][0]["text"]["content"]
        assert len(props["Tags"]["multi_select"]) == 2
        assert props["Status"]["select"]["name"] == "New"
