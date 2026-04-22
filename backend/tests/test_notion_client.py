"""Tests for NotionClient with mocked notion-client SDK calls."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from integrations.notion_client import (
    NotionClient,
    _extract_plain_text,
    _extract_title,
    _normalise_page,
)

# ---------------------------------------------------------------------------
# Fake Notion API responses
# ---------------------------------------------------------------------------


def _fake_page(
    page_id: str = "page-1",
    title: str = "Test Page",
    status: str = "In Progress",
    due_date: str | None = "2026-03-10",
    has_pr: bool = False,
) -> dict[str, Any]:
    """Build a fake Notion page response matching the API shape."""
    properties: dict[str, Any] = {
        "Name": {
            "type": "title",
            "title": [{"plain_text": title}],
        },
        "Status": {
            "type": "status",
            "status": {"name": status} if status else None,
        },
    }
    if due_date:
        properties["Due Date"] = {
            "type": "date",
            "date": {"start": due_date, "end": None},
        }
    if has_pr:
        properties["pull_request"] = {"type": "url", "url": "https://github.com/pr/1"}

    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "created_time": "2026-03-08T10:00:00.000Z",
        "last_edited_time": "2026-03-10T08:00:00.000Z",
        "properties": properties,
    }


_DATABASE_QUERY_RESPONSE = {
    "object": "list",
    "results": [
        _fake_page("page-1", "Write tests", "In Progress", "2026-03-10"),
        _fake_page("page-2", "Review PR", "Not Started", "2026-03-10"),
    ],
    "has_more": False,
    "next_cursor": None,
}

_DATABASE_QUERY_EMPTY = {
    "object": "list",
    "results": [],
    "has_more": False,
    "next_cursor": None,
}

_CREATED_PAGE_RESPONSE = _fake_page("page-new", "New Task", "Not Started", "2026-03-12")

_UPDATED_PAGE_RESPONSE = _fake_page("page-1", "Write tests", "Done", "2026-03-10")

_DATABASE_META_RESPONSE = {
    "id": "db-123",
    "title": [{"plain_text": "Tasks"}],
    "properties": {
        "Name": {"type": "title"},
        "Status": {"type": "status"},
        "Due Date": {"type": "date"},
    },
    "url": "https://notion.so/db-123",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(mock_sdk: AsyncMock | None = None) -> NotionClient:
    """Create a NotionClient with a mocked notion SDK AsyncClient."""
    from utils.resilience import get_service_health_registry

    client = object.__new__(NotionClient)
    client._client = mock_sdk or _mock_sdk()
    client._cb = get_service_health_registry().get_or_create("notion")
    return client


def _mock_sdk(
    query_response: dict | None = None,
    create_response: dict | None = None,
    update_response: dict | None = None,
    retrieve_page_response: dict | None = None,
    retrieve_db_response: dict | None = None,
) -> AsyncMock:
    """Build a mock notion_client.AsyncClient."""
    sdk = AsyncMock()
    sdk.databases = AsyncMock()
    sdk.databases.query = AsyncMock(
        return_value=query_response or _DATABASE_QUERY_RESPONSE,
    )
    sdk.databases.retrieve = AsyncMock(
        return_value=retrieve_db_response or _DATABASE_META_RESPONSE,
    )
    sdk.pages = AsyncMock()
    sdk.pages.create = AsyncMock(
        return_value=create_response or _CREATED_PAGE_RESPONSE,
    )
    sdk.pages.update = AsyncMock(
        return_value=update_response or _UPDATED_PAGE_RESPONSE,
    )
    sdk.pages.retrieve = AsyncMock(
        return_value=retrieve_page_response or _fake_page(),
    )
    sdk.aclose = AsyncMock()
    return sdk


# ---------------------------------------------------------------------------
# query_database()
# ---------------------------------------------------------------------------


class TestQueryDatabase:
    @pytest.mark.asyncio
    async def test_returns_normalised_pages(self) -> None:
        client = _make_client()
        result = await client.query_database("db-123")

        assert len(result) == 2
        assert result[0]["id"] == "page-1"
        assert result[0]["url"] == "https://notion.so/page-1"
        assert "properties" in result[0]

    @pytest.mark.asyncio
    async def test_passes_filter(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)
        test_filter = {"property": "Status", "status": {"equals": "Done"}}

        await client.query_database("db-123", filter=test_filter)

        call_kwargs = sdk.databases.query.call_args.kwargs
        assert call_kwargs["filter"] == test_filter

    @pytest.mark.asyncio
    async def test_passes_sorts(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)
        test_sorts = [{"property": "Due Date", "direction": "ascending"}]

        await client.query_database("db-123", sorts=test_sorts)

        call_kwargs = sdk.databases.query.call_args.kwargs
        assert call_kwargs["sorts"] == test_sorts

    @pytest.mark.asyncio
    async def test_empty_database(self) -> None:
        sdk = _mock_sdk(query_response=_DATABASE_QUERY_EMPTY)
        client = _make_client(sdk)

        result = await client.query_database("db-123")

        assert result == []

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        sdk = _mock_sdk()
        sdk.databases.query = AsyncMock(side_effect=RuntimeError("API error"))
        client = _make_client(sdk)

        with pytest.raises(RuntimeError, match="API error"):
            await client.query_database("db-123")


# ---------------------------------------------------------------------------
# create_page()
# ---------------------------------------------------------------------------


class TestCreatePage:
    @pytest.mark.asyncio
    async def test_creates_and_returns_normalised_page(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)
        properties = {
            "Name": {"title": [{"text": {"content": "New Task"}}]},
        }

        result = await client.create_page("db-123", properties)

        assert result["id"] == "page-new"
        sdk.pages.create.assert_awaited_once()
        call_kwargs = sdk.pages.create.call_args.kwargs
        assert call_kwargs["parent"] == {"database_id": "db-123"}
        assert call_kwargs["properties"] == properties

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        sdk = _mock_sdk()
        sdk.pages.create = AsyncMock(side_effect=RuntimeError("Validation error"))
        client = _make_client(sdk)

        with pytest.raises(RuntimeError, match="Validation error"):
            await client.create_page("db-123", {})


# ---------------------------------------------------------------------------
# update_page()
# ---------------------------------------------------------------------------


class TestUpdatePage:
    @pytest.mark.asyncio
    async def test_updates_and_returns_normalised_page(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)
        properties = {"Status": {"status": {"name": "Done"}}}

        result = await client.update_page("page-1", properties)

        assert result["id"] == "page-1"
        sdk.pages.update.assert_awaited_once()
        call_kwargs = sdk.pages.update.call_args.kwargs
        assert call_kwargs["page_id"] == "page-1"
        assert call_kwargs["properties"] == properties

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        sdk = _mock_sdk()
        sdk.pages.update = AsyncMock(side_effect=RuntimeError("Not found"))
        client = _make_client(sdk)

        with pytest.raises(RuntimeError, match="Not found"):
            await client.update_page("page-999", {})


# ---------------------------------------------------------------------------
# get_tasks_due()
# ---------------------------------------------------------------------------


class TestGetTasksDue:
    @pytest.mark.asyncio
    async def test_returns_tasks_with_titles(self) -> None:
        client = _make_client()
        result = await client.get_tasks_due("db-123", date(2026, 3, 10))

        assert len(result) == 2
        assert result[0]["title"] == "Write tests"
        assert result[1]["title"] == "Review PR"

    @pytest.mark.asyncio
    async def test_builds_correct_filter(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)

        await client.get_tasks_due("db-123", date(2026, 3, 10))

        call_kwargs = sdk.databases.query.call_args.kwargs
        filter_payload = call_kwargs["filter"]

        # Should have "and" with date equals + status not done
        assert "and" in filter_payload
        conditions = filter_payload["and"]
        assert len(conditions) == 2

        # Date condition
        date_cond = conditions[0]
        assert date_cond["property"] == "Due Date"
        assert date_cond["date"]["equals"] == "2026-03-10"

        # Status condition (or: not done / is empty)
        status_cond = conditions[1]
        assert "or" in status_cond

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_error(self) -> None:
        sdk = _mock_sdk()
        sdk.databases.query = AsyncMock(side_effect=RuntimeError("timeout"))
        client = _make_client(sdk)

        result = await client.get_tasks_due("db-123", date(2026, 3, 10))

        assert result == []

    @pytest.mark.asyncio
    async def test_no_tasks_due(self) -> None:
        sdk = _mock_sdk(query_response=_DATABASE_QUERY_EMPTY)
        client = _make_client(sdk)

        result = await client.get_tasks_due("db-123", date(2026, 3, 10))

        assert result == []


# ---------------------------------------------------------------------------
# get_page() / get_database()
# ---------------------------------------------------------------------------


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_get_page(self) -> None:
        client = _make_client()
        result = await client.get_page("page-1")

        assert result["id"] == "page-1"
        assert "properties" in result

    @pytest.mark.asyncio
    async def test_get_database(self) -> None:
        client = _make_client()
        result = await client.get_database("db-123")

        assert result["id"] == "db-123"
        assert result["title"] == "Tasks"
        assert "properties" in result


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        sdk = _mock_sdk()
        client = _make_client(sdk)

        await client.close()

        sdk.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalise_page(self) -> None:
        raw = _fake_page("p1", "Test")
        result = _normalise_page(raw)

        assert result["id"] == "p1"
        assert result["url"] == "https://notion.so/p1"
        assert "created_time" in result
        assert "last_edited_time" in result
        assert "properties" in result
        # Should NOT include raw API fields like "object"
        assert "object" not in result

    def test_extract_title_from_name_property(self) -> None:
        properties = {
            "Name": {
                "type": "title",
                "title": [{"plain_text": "My Task"}],
            },
        }
        assert _extract_title(properties) == "My Task"

    def test_extract_title_multi_segment(self) -> None:
        properties = {
            "Title": {
                "type": "title",
                "title": [
                    {"plain_text": "Hello "},
                    {"plain_text": "World"},
                ],
            },
        }
        assert _extract_title(properties) == "Hello World"

    def test_extract_title_missing(self) -> None:
        properties = {"Status": {"type": "status"}}
        assert _extract_title(properties) == ""

    def test_extract_plain_text(self) -> None:
        segments = [
            {"plain_text": "Part 1"},
            {"plain_text": " Part 2"},
        ]
        assert _extract_plain_text(segments) == "Part 1 Part 2"

    def test_extract_plain_text_empty(self) -> None:
        assert _extract_plain_text([]) == ""
