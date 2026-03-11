"""Tests for the Google Jobs via SerpAPI adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from integrations.job_boards.google_jobs import GoogleJobsAdapter


@pytest.fixture
def adapter():
    return GoogleJobsAdapter(serpapi_key="test-key-123")


@pytest.fixture
def adapter_no_key():
    return GoogleJobsAdapter()


@pytest.fixture
def mock_serpapi_response():
    """Sample SerpAPI google_jobs response."""
    return {
        "jobs_results": [
            {
                "job_id": "abc123",
                "title": "Senior Software Engineer",
                "company_name": "Google",
                "location": "Mountain View, CA",
                "description": "Join our team to build search infrastructure.",
                "extensions": ["Full-time", "$150,000-$200,000 a year"],
                "detected_extensions": {"salary": []},
                "apply_options": [
                    {"link": "https://careers.google.com/jobs/123", "title": "Google Careers"},
                ],
                "share_link": "https://www.google.com/search?q=jobs&id=abc123",
            },
            {
                "job_id": "def456",
                "title": "ML Engineer",
                "company_name": "OpenAI",
                "location": "San Francisco, CA",
                "description": "Work on cutting-edge AI models.",
                "extensions": ["Full-time"],
                "detected_extensions": {},
                "apply_options": [],
                "share_link": "https://www.google.com/search?q=jobs&id=def456",
            },
        ],
    }


@pytest.mark.asyncio
async def test_search_returns_results(adapter, mock_serpapi_response):
    mock_resp = httpx.Response(200, json=mock_serpapi_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"query": "software engineer"})

    assert len(results) == 2
    assert results[0]["title"] == "Senior Software Engineer"
    assert results[0]["company"] == "Google"
    assert results[0]["id"] == "gjobs-abc123"


@pytest.mark.asyncio
async def test_search_extracts_apply_url(adapter, mock_serpapi_response):
    mock_resp = httpx.Response(200, json=mock_serpapi_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"query": "software"})

    # First result has apply_options
    assert results[0]["url"] == "https://careers.google.com/jobs/123"
    # Second result falls back to share_link
    assert "google.com/search" in results[1]["url"]


@pytest.mark.asyncio
async def test_search_extracts_salary_from_extensions(adapter, mock_serpapi_response):
    mock_resp = httpx.Response(200, json=mock_serpapi_response, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"query": "software"})

    assert "$150,000" in results[0]["salary"]


@pytest.mark.asyncio
async def test_search_skips_without_api_key(adapter_no_key):
    results = await adapter_no_key.search({"query": "test"})
    assert results == []


@pytest.mark.asyncio
async def test_search_handles_http_error(adapter):
    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("timeout"),
    ):
        results = await adapter.search({"query": "test"})

    assert results == []


@pytest.mark.asyncio
async def test_search_handles_empty_response(adapter):
    mock_resp = httpx.Response(200, json={"jobs_results": []}, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await adapter.search({"query": "nonexistent role"})

    assert results == []


@pytest.mark.asyncio
async def test_search_sends_correct_params(adapter):
    mock_resp = httpx.Response(200, json={"jobs_results": []}, request=httpx.Request("GET", "https://test"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        await adapter.search({
            "query": "backend engineer",
            "location": "New York",
            "date_posted": "3days",
            "page": 2,
        })

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert params["engine"] == "google_jobs"
        assert params["q"] == "backend engineer"
        assert params["location"] == "New York"
        assert params["chips"] == "date_posted:3days"
        assert params["start"] == 20


def test_normalize(adapter):
    raw = {
        "id": "gjobs-test1",
        "title": "Engineer",
        "company": "TestCo",
        "location": "Remote",
        "salary": "$100k",
        "description": "Build things",
        "url": "https://example.com",
    }
    normalized = adapter.normalize(raw)
    assert normalized["source"] == "google_jobs"
    assert normalized["external_id"] == "gjobs-test1"
