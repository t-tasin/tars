"""Tests for the JobSpy scraper adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.job_boards.jobspy_scraper import JobSpyScraper


@pytest.fixture
def adapter():
    return JobSpyScraper()


class MockDataFrame:
    """Lightweight mock for pandas DataFrame with iterrows()."""

    def __init__(self, records: list[dict]):
        self._records = records

    def iterrows(self):
        for i, record in enumerate(self._records):
            yield i, _DictRow(record)


class _DictRow(dict):
    """Dict subclass that supports .get() like a pandas Series."""

    pass


@pytest.fixture
def mock_df():
    """Build a mock DataFrame matching JobSpy output."""
    return MockDataFrame(
        [
            {
                "id": "abc123",
                "title": "Software Engineer",
                "company_name": "Acme Corp",
                "location": "San Francisco, CA",
                "min_amount": 150000,
                "max_amount": 200000,
                "interval": "yearly",
                "description": "Build cool stuff",
                "job_url": "https://acme.com/jobs/1",
                "site": "indeed",
            },
            {
                "id": "def456",
                "title": "ML Engineer",
                "company_name": "Beta Inc",
                "location": "Remote",
                "min_amount": None,
                "max_amount": None,
                "interval": "",
                "description": "Train models",
                "job_url": "https://beta.com/jobs/2",
                "site": "glassdoor",
            },
        ]
    )


@pytest.mark.asyncio
async def test_search_returns_normalized_results(adapter, mock_df):
    with patch(
        "integrations.job_boards.jobspy_scraper._run_jobspy",
        return_value=mock_df,
    ):
        results = await adapter.search({"query": "software engineer"})

    assert len(results) == 2
    assert results[0]["title"] == "Software Engineer"
    assert results[0]["company"] == "Acme Corp"
    assert results[0]["id"] == "jobspy-abc123"
    assert "$150,000-$200,000 yearly" in results[0]["salary"]


@pytest.mark.asyncio
async def test_search_handles_empty_salary(adapter, mock_df):
    with patch(
        "integrations.job_boards.jobspy_scraper._run_jobspy",
        return_value=mock_df,
    ):
        results = await adapter.search({"query": "ml"})

    assert results[1]["salary"] == ""


@pytest.mark.asyncio
async def test_search_handles_exception_gracefully(adapter):
    with patch(
        "integrations.job_boards.jobspy_scraper._run_jobspy",
        side_effect=RuntimeError("scrape failed"),
    ):
        results = await adapter.search({"query": "test"})

    assert results == []


@pytest.mark.asyncio
async def test_normalize_adds_metadata(adapter):
    raw = {
        "id": "x1",
        "title": "Engineer",
        "company": "Test",
        "location": "NYC",
        "salary": "",
        "description": "desc",
        "url": "https://example.com",
        "site": "indeed",
    }
    normalized = adapter.normalize(raw)
    assert normalized["source"] == "jobspy"
    assert normalized["metadata"]["site"] == "indeed"


@pytest.mark.asyncio
async def test_search_passes_criteria(adapter, mock_df):
    with patch(
        "integrations.job_boards.jobspy_scraper._run_jobspy",
        return_value=mock_df,
    ) as mock_run:
        await adapter.search(
            {
                "query": "backend engineer",
                "location": "New York",
                "results_wanted": 50,
                "hours_old": 24,
            }
        )

        mock_run.assert_called_once_with(
            search_term="backend engineer",
            location="New York",
            site_names=["indeed", "glassdoor", "zip_recruiter"],
            results_wanted=50,
            hours_old=24,
        )
