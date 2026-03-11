"""Tests for the Greenhouse ATS adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from integrations.job_boards.greenhouse import GreenhouseAdapter, _matches_keywords, _strip_html


@pytest.fixture
def adapter():
    return GreenhouseAdapter()


@pytest.fixture
def mock_greenhouse_response():
    """Sample Greenhouse API response."""
    return {
        "jobs": [
            {
                "id": 12345,
                "title": "Software Engineer, Backend",
                "location": {"name": "San Francisco, CA"},
                "content": "<p>We are looking for a <strong>backend engineer</strong>.</p>",
                "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/12345",
                "updated_at": "2026-03-09T10:00:00Z",
            },
            {
                "id": 12346,
                "title": "Office Manager",
                "location": {"name": "New York, NY"},
                "content": "<p>Manage the office.</p>",
                "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/12346",
                "updated_at": "2026-03-09T10:00:00Z",
            },
            {
                "id": 12347,
                "title": "ML Research Scientist",
                "location": {"name": "Remote"},
                "content": "<p>Research in <em>machine learning</em> and AI.</p>",
                "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/12347",
                "updated_at": "2026-03-09T10:00:00Z",
            },
        ],
    }


@pytest.mark.asyncio
async def test_search_filters_by_keywords(adapter, mock_greenhouse_response):
    mock_resp = httpx.Response(200, json=mock_greenhouse_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"companies": ["anthropic"]})

    # "Office Manager" should be filtered out
    titles = [r["title"] for r in results]
    assert "Software Engineer, Backend" in titles
    assert "ML Research Scientist" in titles
    assert "Office Manager" not in titles


@pytest.mark.asyncio
async def test_search_strips_html_from_description(adapter, mock_greenhouse_response):
    mock_resp = httpx.Response(200, json=mock_greenhouse_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"companies": ["anthropic"]})

    backend_job = next(r for r in results if "Backend" in r["title"])
    assert "<p>" not in backend_job["description"]
    assert "<strong>" not in backend_job["description"]
    assert "backend engineer" in backend_job["description"]


@pytest.mark.asyncio
async def test_search_handles_404(adapter):
    mock_resp = httpx.Response(404, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"companies": ["nonexistent-company"]})

    assert results == []


@pytest.mark.asyncio
async def test_search_handles_http_error(adapter):
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("connection refused"),
    ):
        results = await adapter.search({"companies": ["anthropic"]})

    assert results == []


@pytest.mark.asyncio
async def test_search_formats_company_name(adapter, mock_greenhouse_response):
    mock_resp = httpx.Response(200, json=mock_greenhouse_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"companies": ["anthropic"]})

    assert all(r["company"] == "Anthropic" for r in results)


@pytest.mark.asyncio
async def test_search_generates_correct_external_ids(adapter, mock_greenhouse_response):
    mock_resp = httpx.Response(200, json=mock_greenhouse_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"companies": ["anthropic"]})

    ids = [r["id"] for r in results]
    assert "gh-anthropic-12345" in ids
    assert "gh-anthropic-12347" in ids


def test_matches_keywords_title():
    assert _matches_keywords("Software Engineer", "", ["software engineer"])
    assert _matches_keywords("ML Research", "", ["ml", "research"])
    assert not _matches_keywords("Office Manager", "manage things", ["software", "ml"])


def test_matches_keywords_description():
    assert _matches_keywords("Generic Title", "Looking for a python developer", ["python"])


def test_strip_html():
    assert "Hello world" in _strip_html("<p>Hello <strong>world</strong></p>")
    assert "<" not in _strip_html("<div>test</div>")


def test_normalize(adapter):
    raw = {
        "id": "gh-test-1",
        "title": "Engineer",
        "company": "Test Co",
        "location": "Remote",
        "salary": "",
        "description": "Build things",
        "url": "https://example.com",
    }
    normalized = adapter.normalize(raw)
    assert normalized["source"] == "greenhouse"
    assert normalized["external_id"] == "gh-test-1"
