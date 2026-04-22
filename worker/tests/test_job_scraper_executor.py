"""Tests for the job scraper executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.executors.job_scraper_executor import JobScraperExecutor, _deduplicate, _dedup_key


@pytest.fixture
def executor():
    return JobScraperExecutor()


@pytest.fixture
def base_payload():
    return {
        "search_criteria": {"keywords": "software engineer", "location": "San Francisco"},
        "sources": ["jobspy"],
        "max_results": 50,
        "serpapi_key": "",
    }


@pytest.mark.asyncio
async def test_execute_jobspy_source(executor, base_payload):
    with patch(
        "src.executors.job_scraper_executor.JobScraperExecutor._scrape_jobspy",
        new_callable=AsyncMock,
        return_value=[
            {
                "source": "jobspy",
                "external_id": "jobspy-j1",
                "title": "Software Engineer",
                "company": "Acme",
                "location": "SF, CA",
                "salary_range": "$150,000-$200,000 yearly",
                "description": "Build stuff",
                "url": "https://acme.com/1",
            }
        ],
    ):
        result = await executor.execute("test-job-1", base_payload)

    assert result["status"] == "completed"
    assert result["success"] is True
    assert len(result["listings"]) == 1
    assert result["listings"][0]["title"] == "Software Engineer"


@pytest.mark.asyncio
async def test_execute_multiple_sources(executor):
    payload = {
        "search_criteria": {"keywords": "engineer"},
        "sources": ["jobspy", "greenhouse"],
        "max_results": 100,
    }

    jobspy_result = [{"source": "jobspy", "title": "Engineer A", "company": "Co1", "external_id": "a"}]
    greenhouse_result = [{"source": "greenhouse", "title": "Engineer B", "company": "Co2", "external_id": "b"}]

    with (
        patch.object(executor, "_scrape_jobspy", new_callable=AsyncMock, return_value=jobspy_result),
        patch.object(executor, "_scrape_greenhouse", new_callable=AsyncMock, return_value=greenhouse_result),
    ):
        result = await executor.execute("test-job-2", payload)

    assert result["status"] == "completed"
    assert result["total_deduped"] == 2


@pytest.mark.asyncio
async def test_execute_deduplicates_across_sources(executor):
    payload = {
        "search_criteria": {"keywords": "engineer"},
        "sources": ["jobspy", "greenhouse"],
        "max_results": 100,
    }

    # Same job from two sources
    jobspy_result = [{"source": "jobspy", "title": "Software Engineer", "company": "Acme Corp", "external_id": "a"}]
    greenhouse_result = [
        {"source": "greenhouse", "title": "Software Engineer", "company": "Acme Corp", "external_id": "b"}
    ]

    with (
        patch.object(executor, "_scrape_jobspy", new_callable=AsyncMock, return_value=jobspy_result),
        patch.object(executor, "_scrape_greenhouse", new_callable=AsyncMock, return_value=greenhouse_result),
    ):
        result = await executor.execute("test-job-3", payload)

    assert result["total_raw"] == 2
    assert result["total_deduped"] == 1


@pytest.mark.asyncio
async def test_execute_handles_source_failure(executor):
    payload = {
        "search_criteria": {"keywords": "engineer"},
        "sources": ["jobspy", "greenhouse"],
        "max_results": 100,
    }

    greenhouse_result = [{"source": "greenhouse", "title": "Engineer", "company": "Co", "external_id": "x"}]

    with (
        patch.object(executor, "_scrape_jobspy", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        patch.object(executor, "_scrape_greenhouse", new_callable=AsyncMock, return_value=greenhouse_result),
    ):
        result = await executor.execute("test-job-4", payload)

    # Should still complete with partial results
    assert result["status"] == "completed"
    assert len(result["listings"]) == 1


@pytest.mark.asyncio
async def test_execute_respects_max_results(executor):
    payload = {
        "search_criteria": {"keywords": "engineer"},
        "sources": ["jobspy"],
        "max_results": 2,
    }

    many_results = [
        {"source": "jobspy", "title": f"Job {i}", "company": f"Co {i}", "external_id": f"id-{i}"} for i in range(10)
    ]

    with patch.object(executor, "_scrape_jobspy", new_callable=AsyncMock, return_value=many_results):
        result = await executor.execute("test-job-5", payload)

    assert len(result["listings"]) == 2


def test_deduplicate():
    listings = [
        {"title": "Software Engineer", "company": "Acme Corp"},
        {"title": "Software Engineer", "company": "Acme Corp"},
        {"title": "ML Engineer", "company": "Beta Inc"},
    ]
    deduped = _deduplicate(listings)
    assert len(deduped) == 2


def test_dedup_key_normalizes():
    assert _dedup_key("Software Engineer", "Acme Corp") == _dedup_key("software engineer", "ACME CORP")
    assert _dedup_key("Engineer", "Acme Inc.") == _dedup_key("Engineer", "Acme")
    assert _dedup_key("Engineer", "Alpha") != _dedup_key("Engineer", "Beta")
