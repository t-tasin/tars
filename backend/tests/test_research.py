"""Tests for the ResearchAgent."""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.base import AgentContext
from agents.research import (
    ResearchAgent,
    _build_section_cards,
    _classify_category,
    _parse_research_json,
)
from models.claude_spawner import ClaudeCodeResult, ClaudeSpawnError

# Access the fake db.session module injected by conftest
_fake_db_session_module = sys.modules["db.session"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(message: str, **config_overrides: Any) -> AgentContext:
    return AgentContext(
        user_message=message,
        intent_type="research",
        config=config_overrides,
    )


_SAMPLE_RESEARCH_JSON = {
    "title": "Python vs Rust for CLI Tools",
    "summary": "Both languages are viable for CLI tools. Rust offers better performance and smaller binaries, while Python provides faster development cycles.",
    "sections": [
        {
            "heading": "Performance",
            "content": "Rust compiles to native code with **zero-cost abstractions**.\n- Startup time: ~5ms\n- Memory: ~2MB baseline",
            "sources": ["benchmarksgame.org", "rust-lang.org"],
        },
        {
            "heading": "Developer Experience",
            "content": "Python has a larger ecosystem of libraries.\n- pip install for most needs\n- REPL for quick prototyping",
            "sources": ["python.org", "pypi.org"],
        },
    ],
    "key_takeaways": [
        "Rust is better for performance-critical CLI tools",
        "Python is better for rapid prototyping and scripting",
        "Consider Rust if distributing as a single binary matters",
    ],
    "confidence": "high",
    "confidence_note": "Well-documented comparison with many reliable sources",
}


def _make_claude_result(
    text: str | None = None,
    success: bool = True,
) -> ClaudeCodeResult:
    if text is None:
        text = json.dumps(_SAMPLE_RESEARCH_JSON)
    return ClaudeCodeResult(
        text=text,
        success=success,
        duration_ms=5000,
        cost_usd=0.05,
        session_id="test-session",
        num_turns=3,
        error=None if success else "timeout",
    )


class _FakeDbCtx:
    """Minimal async context manager wrapping an AsyncMock session."""

    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


def _install_mock_session(
    execute_results: list[MagicMock] | None = None,
) -> AsyncMock:
    """Install a mock get_db_session on the fake module and return the session."""
    mock_session = AsyncMock()
    if execute_results is not None:
        mock_session.execute = AsyncMock(side_effect=execute_results)
    else:
        result = MagicMock()
        result.first.return_value = None
        result.all.return_value = []
        result.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
        mock_session.execute = AsyncMock(return_value=result)

    _fake_db_session_module.get_db_session = lambda: _FakeDbCtx(mock_session)
    return mock_session


# ---------------------------------------------------------------------------
# Test: _classify_category
# ---------------------------------------------------------------------------


class TestClassifyCategory:
    def test_career_keywords(self) -> None:
        assert _classify_category("What is the job market like for ML engineers?") == "career"

    def test_technology_keywords(self) -> None:
        assert _classify_category("Compare React vs Vue framework for a new project") == "technology"

    def test_academic_keywords(self) -> None:
        assert _classify_category("Find recent research papers on transformer architectures") == "academic"

    def test_finance_keywords(self) -> None:
        assert _classify_category("What are current market trends for tech stocks?") == "finance"

    def test_health_keywords(self) -> None:
        assert _classify_category("What are the benefits of intermittent fasting for fitness?") == "health"

    def test_travel_keywords(self) -> None:
        assert _classify_category("Best time to travel to Japan and flight costs") == "travel"

    def test_general_fallback(self) -> None:
        assert _classify_category("Tell me about the history of chess") == "general"


# ---------------------------------------------------------------------------
# Test: _parse_research_json
# ---------------------------------------------------------------------------


class TestParseResearchJson:
    def test_direct_json(self) -> None:
        raw = json.dumps(_SAMPLE_RESEARCH_JSON)
        result = _parse_research_json(raw)
        assert result is not None
        assert result["title"] == "Python vs Rust for CLI Tools"
        assert len(result["sections"]) == 2

    def test_json_in_code_fence(self) -> None:
        raw = f"Here are my findings:\n```json\n{json.dumps(_SAMPLE_RESEARCH_JSON)}\n```\n"
        result = _parse_research_json(raw)
        assert result is not None
        assert result["title"] == "Python vs Rust for CLI Tools"

    def test_json_with_surrounding_text(self) -> None:
        raw = f"Some preamble text.\n{json.dumps(_SAMPLE_RESEARCH_JSON)}\nSome trailing text."
        result = _parse_research_json(raw)
        assert result is not None
        assert result["title"] == "Python vs Rust for CLI Tools"

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_research_json("This is not JSON at all") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_research_json("") is None


# ---------------------------------------------------------------------------
# Test: _build_section_cards
# ---------------------------------------------------------------------------


class TestBuildSectionCards:
    def test_builds_cards_from_sections(self) -> None:
        sections = _SAMPLE_RESEARCH_JSON["sections"]
        cards = _build_section_cards(sections)
        assert len(cards) == 2
        assert cards[0]["heading"] == "Performance"
        assert cards[1]["heading"] == "Developer Experience"
        assert "sources" in cards[0]

    def test_skips_empty_sections(self) -> None:
        sections = [{"heading": "", "content": "", "sources": []}]
        cards = _build_section_cards(sections)
        assert len(cards) == 0

    def test_handles_missing_keys(self) -> None:
        sections = [{"heading": "Test"}]
        cards = _build_section_cards(sections)
        assert len(cards) == 1
        assert cards[0]["content"] == ""
        assert cards[0]["sources"] == []


# ---------------------------------------------------------------------------
# Test: ResearchAgent.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResearchAgentExecute:
    async def test_successful_research(self) -> None:
        """Successful research returns structured brief with cards."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            result = await agent.execute(_make_context("Compare Python vs Rust for building CLI tools"))

        assert result.success is True
        assert result.has_side_effects is False
        assert "Python vs Rust" in result.text
        assert result.content_type == "card"
        assert len(result.cards) == 2
        assert result.data["model"] == "claude_code"
        assert result.data["confidence"] == "high"
        assert result.data["cost_usd"] == 0.05

    async def test_empty_query_returns_error(self) -> None:
        """Empty query returns a helpful error."""
        agent = ResearchAgent()
        result = await agent.execute(_make_context(""))

        assert result.success is False
        assert result.error == "empty_query"

    async def test_claude_unavailable_returns_error(self) -> None:
        """When Claude spawn fails, return graceful error."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(side_effect=ClaudeSpawnError("not found"))

            result = await agent.execute(_make_context("Research quantum computing applications"))

        assert result.success is False
        assert result.error == "claude_unavailable"

    async def test_claude_empty_response_returns_error(self) -> None:
        """When Claude returns empty text, return an error."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result(text="", success=True))

            result = await agent.execute(_make_context("Research the best databases for time-series data"))

        assert result.success is False
        assert result.error == "empty_response"

    async def test_claude_failure_returns_error(self) -> None:
        """When Claude returns success=False, return an error."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result(text="", success=False))

            result = await agent.execute(_make_context("Research latest AI trends"))

        assert result.success is False

    async def test_non_json_response_falls_back_to_text(self) -> None:
        """When Claude returns non-JSON, fall back to text content."""
        agent = ResearchAgent()
        _install_mock_session()

        plain_text = "Here are my research findings about quantum computing..."
        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result(text=plain_text))

            result = await agent.execute(_make_context("Research quantum computing"))

        assert result.success is True
        assert result.content_type == "text"
        assert result.text == plain_text

    async def test_research_is_read_only(self) -> None:
        """Research agent never requires approval (tier 1)."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            result = await agent.execute(_make_context("Research salary ranges for ML engineers"))

        assert result.has_side_effects is False
        assert result.action_type is None
        assert result.approval_title is None

    async def test_uses_research_mcp_profile(self) -> None:
        """Verify Claude is spawned with the 'research' MCP profile."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            await agent.execute(_make_context("Research best practices for FastAPI deployment"))

            mock_claude.execute.assert_called_once()
            call_kwargs = mock_claude.execute.call_args
            assert call_kwargs.kwargs["mcp_profile"] == "research"
            assert call_kwargs.kwargs["max_turns"] == 5

    async def test_career_category_detected(self) -> None:
        """Career-related queries should be classified as 'career'."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            result = await agent.execute(_make_context("What is the job market outlook for software engineers?"))

        assert result.success is True
        assert result.data["category"] == "career"

    async def test_cross_references_user_context(self) -> None:
        """Verify the prompt includes user context when available."""
        agent = ResearchAgent()

        # Set up DB to return recent messages and job listings
        topics_result = MagicMock()
        topics_result.scalars.return_value = MagicMock(
            all=MagicMock(return_value=["ML engineer salary research", "Interview prep"])
        )
        jobs_result = MagicMock()
        job_row = MagicMock()
        job_row.title = "ML Engineer"
        job_row.company = "Google"
        jobs_result.all.return_value = [job_row]

        _install_mock_session([topics_result, jobs_result])

        prompt_captured: list[str] = []

        async def capture_prompt(**kwargs: Any) -> ClaudeCodeResult:
            prompt_captured.append(kwargs.get("prompt", ""))
            return _make_claude_result()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(side_effect=capture_prompt)

            await agent.execute(_make_context("Research ML engineer compensation trends"))

        assert len(prompt_captured) == 1
        prompt = prompt_captured[0]
        assert "ML engineer salary research" in prompt
        assert "ML Engineer at Google" in prompt

    async def test_key_takeaways_in_display_text(self) -> None:
        """Key takeaways should appear in the display text."""
        agent = ResearchAgent()
        _install_mock_session()

        with MagicMock() as mock_claude:
            agent._claude = mock_claude
            mock_claude.execute = AsyncMock(return_value=_make_claude_result())

            result = await agent.execute(_make_context("Compare Python vs Rust"))

        assert "Key Takeaways" in result.text
        assert "Rust is better for performance-critical CLI tools" in result.text
