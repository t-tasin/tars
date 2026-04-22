"""Tests for the Job Search Agent — three-tier filtering pipeline."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.constants import JobStatus

from agents.base import AgentContext
from agents.job_search import JobSearchAgent, _parse_action
from models.gemini_client import GeminiResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_settings() -> MagicMock:
    s = MagicMock()
    s.serpapi_key = "test-key"
    return s


@pytest.fixture()
def agent() -> JobSearchAgent:
    with patch("config.get_settings", return_value=_mock_settings()):
        return JobSearchAgent()


@pytest.fixture()
def mock_gemini() -> AsyncMock:
    client = AsyncMock()
    return client


def _make_context(
    message: str = "/jobs",
    intent_action: str | None = None,
    gemini: Any = None,
    job_config: dict | None = None,
) -> AgentContext:
    config: dict[str, Any] = {}
    if intent_action:
        config["intent_action"] = intent_action
    if gemini:
        config["gemini_client"] = gemini
    if job_config:
        config["job_search"] = job_config
    return AgentContext(
        user_message=message,
        intent_type="job_search",
        config=config,
    )


def _gemini_response(text: str, model: str = "gemini-2.0-flash") -> GeminiResponse:
    return GeminiResponse(
        text=text,
        model=model,
        tokens_input=100,
        tokens_output=50,
        duration_ms=500,
        finish_reason="STOP",
    )


# ---------------------------------------------------------------------------
# _parse_action
# ---------------------------------------------------------------------------


class TestParseAction:
    def test_jobs_command(self) -> None:
        assert _parse_action("/jobs") == "show_digest"

    def test_scan_command(self) -> None:
        assert _parse_action("/jobs scan") == "full_scan"

    def test_search_keyword(self) -> None:
        assert _parse_action("search for jobs") == "full_scan"

    def test_find_jobs(self) -> None:
        assert _parse_action("find jobs") == "full_scan"

    def test_default_is_digest(self) -> None:
        assert _parse_action("random text") == "show_digest"


# ---------------------------------------------------------------------------
# Show digest
# ---------------------------------------------------------------------------


class TestShowDigest:
    @pytest.mark.asyncio
    async def test_no_matches_returns_message(self, agent: JobSearchAgent) -> None:
        """When no recent listings exist, show a friendly message."""
        ctx = _make_context("/jobs", intent_action="show_digest")

        with patch("db.session.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)

            @asynccontextmanager
            async def _session():
                yield mock_session

            mock_db.side_effect = _session

            result = await agent.execute(ctx)

        assert result.success is True
        assert "No new job matches" in result.text

    @pytest.mark.asyncio
    async def test_with_matches_returns_cards(self, agent: JobSearchAgent) -> None:
        """When listings exist, return cards with top match info."""
        test_id = uuid.uuid4()
        mock_rows = [
            MagicMock(
                id=test_id,
                title="Senior SWE",
                company="Acme Corp",
                location="Remote",
                match_score=85,
                match_reasons=["Python", "AI/ML"],
                concerns=[],
                source="linkedin",
                url="https://example.com/job/1",
                salary_range="$150k-$200k",
                status=JobStatus.NEW,
                scraped_at=datetime.now(UTC),
            ),
        ]

        ctx = _make_context("/jobs", intent_action="show_digest")

        with patch("db.session.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = mock_rows
            mock_session.execute = AsyncMock(return_value=mock_result)

            @asynccontextmanager
            async def _session():
                yield mock_session

            mock_db.side_effect = _session

            result = await agent.execute(ctx)

        assert result.success is True
        assert "Senior SWE" in result.text
        assert "Acme Corp" in result.text
        assert "85%" in result.text
        assert len(result.cards) == 1
        assert result.cards[0]["title"] == "Senior SWE"


# ---------------------------------------------------------------------------
# Flash screen batch
# ---------------------------------------------------------------------------


class TestFlashScreen:
    @pytest.mark.asyncio
    async def test_batch_scoring(self, agent: JobSearchAgent, mock_gemini: AsyncMock) -> None:
        """Flash screening should score and filter listings."""
        listings = [
            {"title": "Good Job", "company": "A", "location": "", "salary_range": "", "description": "Great fit"},
            {"title": "Bad Job", "company": "B", "location": "", "salary_range": "", "description": "Not a fit"},
        ]

        flash_response = json.dumps(
            [
                {"idx": 0, "score": 75},
                {"idx": 1, "score": 20},
            ]
        )
        mock_gemini.generate = AsyncMock(return_value=_gemini_response(flash_response))

        result = await agent._flash_screen(listings, {"profile_summary": "SWE"}, mock_gemini)

        # Only the listing scoring >= 40 should pass
        assert len(result) == 1
        assert result[0]["title"] == "Good Job"
        assert result[0]["_flash_score"] == 75

    @pytest.mark.asyncio
    async def test_no_gemini_passes_all(self, agent: JobSearchAgent) -> None:
        """Without Gemini, all listings pass through (HC-09)."""
        listings = [
            {"title": "Job 1", "company": "A"},
            {"title": "Job 2", "company": "B"},
        ]

        result = await agent._flash_screen(listings, {}, None)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_json_parse_failure_passes_all(self, agent: JobSearchAgent, mock_gemini: AsyncMock) -> None:
        """If Gemini returns invalid JSON, all listings pass (conservative)."""
        listings = [{"title": "Job 1", "company": "A", "description": "test", "salary_range": "", "location": ""}]
        mock_gemini.generate = AsyncMock(return_value=_gemini_response("not valid json"))

        result = await agent._flash_screen(listings, {"profile_summary": "SWE"}, mock_gemini)
        assert len(result) == 1
        assert result[0]["_flash_score"] == 50


# ---------------------------------------------------------------------------
# Pro evaluate
# ---------------------------------------------------------------------------


class TestProEvaluate:
    @pytest.mark.asyncio
    async def test_evaluates_listing(self, agent: JobSearchAgent, mock_gemini: AsyncMock) -> None:
        """Pro evaluation should set match_score, reasons, and concerns."""
        listings = [
            {
                "title": "ML Engineer",
                "company": "DeepTech",
                "location": "SF",
                "salary_range": "$180k",
                "description": "Build ML systems",
                "_flash_score": 70,
            },
        ]

        pro_response = json.dumps(
            {
                "match_score": 88,
                "match_reasons": ["Strong ML background", "Python expertise"],
                "concerns": ["Location preference"],
            }
        )
        mock_gemini.generate = AsyncMock(return_value=_gemini_response(pro_response, "gemini-2.0-pro"))

        result = await agent._pro_evaluate(listings, {"full_profile": "ML engineer"}, mock_gemini)

        assert len(result) == 1
        assert result[0]["match_score"] == 88
        assert "Strong ML background" in result[0]["match_reasons"]
        assert result[0]["pro_evaluated"] is True

    @pytest.mark.asyncio
    async def test_no_gemini_returns_unmodified(self, agent: JobSearchAgent) -> None:
        """Without Gemini, return listings as-is (HC-09)."""
        listings = [{"title": "Job", "_flash_score": 60}]

        result = await agent._pro_evaluate(listings, {}, None)
        assert len(result) == 1
        assert result[0]["title"] == "Job"

    @pytest.mark.asyncio
    async def test_parse_failure_uses_flash_score(self, agent: JobSearchAgent, mock_gemini: AsyncMock) -> None:
        """If Pro returns unparseable JSON, fall back to flash score."""
        listings = [
            {
                "title": "SWE",
                "company": "Co",
                "location": "",
                "salary_range": "",
                "description": "",
                "_flash_score": 55,
            },
        ]
        mock_gemini.generate = AsyncMock(return_value=_gemini_response("invalid", "gemini-2.0-pro"))

        result = await agent._pro_evaluate(listings, {"full_profile": "SWE"}, mock_gemini)

        assert result[0]["match_score"] == 55
        assert result[0]["pro_evaluated"] is True


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


class TestCollection:
    @pytest.mark.asyncio
    async def test_safe_search_returns_results(self, agent: JobSearchAgent) -> None:
        """_safe_search should return adapter results on success."""
        adapter = MagicMock()
        adapter.source_name = "test"
        adapter.search = AsyncMock(return_value=[{"id": "1", "title": "SWE"}])

        result = await agent._safe_search(adapter, {"query": "test"})
        assert len(result) == 1
        assert result[0]["title"] == "SWE"

    @pytest.mark.asyncio
    async def test_safe_search_handles_failure(self, agent: JobSearchAgent) -> None:
        """_safe_search should return empty list on adapter failure (HC-09)."""
        adapter = MagicMock()
        adapter.source_name = "broken"
        adapter.search = AsyncMock(side_effect=RuntimeError("API down"))

        result = await agent._safe_search(adapter, {"query": "test"})
        assert result == []


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestStorage:
    @pytest.mark.asyncio
    async def test_stores_listings(self, agent: JobSearchAgent) -> None:
        """Store results should insert into the DB."""
        listings = [
            {
                "source": "linkedin",
                "external_id": "abc-123",
                "title": "SWE",
                "company": "Acme",
                "location": "Remote",
                "salary_range": "$150k",
                "description": "Build things",
                "url": "https://example.com",
                "match_score": 85,
                "match_reasons": ["Python"],
                "concerns": [],
                "scraped_at": datetime.now(UTC),
            },
        ]

        with patch("db.session.get_db_session") as mock_db:
            mock_session = AsyncMock()

            @asynccontextmanager
            async def _session():
                yield mock_session

            mock_db.side_effect = _session

            await agent._store_results(listings, flash_only=False)

        mock_session.add.assert_called_once()


# ---------------------------------------------------------------------------
# Intent classifier integration
# ---------------------------------------------------------------------------


class TestIntentIntegration:
    def test_jobs_command_classified(self) -> None:
        from shared.constants import IntentType

        from orchestrator.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        intent = classifier.classify("/jobs", "ios")
        assert intent.agent == IntentType.JOB_SEARCH
        assert intent.action == "show_digest"

    def test_jobs_scan_classified(self) -> None:
        from shared.constants import IntentType

        from orchestrator.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        intent = classifier.classify("/jobs scan", "ios")
        assert intent.agent == IntentType.JOB_SEARCH
        assert intent.action == "full_scan"

    def test_keyword_job_classified(self) -> None:
        from shared.constants import IntentType

        from orchestrator.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        intent = classifier.classify("looking for a job", "telegram")
        assert intent.agent == IntentType.JOB_SEARCH
