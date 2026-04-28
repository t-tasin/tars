"""Research agent — deep research via Claude Code with Brave Search MCP.

Always routes to Claude Code (deep comprehension and multi-source synthesis
needed).  Claude uses the Brave Search MCP server to search the web
autonomously, cross-references findings with the user's known context
(job search, calendar, recent conversations), and returns a structured
research brief.

Read-only — no side effects, no approval required (tier 1).
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from shared.constants import AutonomyClass
from sqlalchemy import select

from agents.base import AgentContext, AgentResult, BaseAgent
from models.claude_spawner import ClaudeCodeSpawner, ClaudeSpawnError

log = structlog.get_logger()

_SYSTEM_PROMPT = """\
You are T.A.R.S., a personal AI research assistant for Tasin.

Your task is to perform deep research on a topic using web search.  You have \
access to the Brave Search MCP tool — use it to find authoritative, recent \
sources.  Cross-reference multiple sources before drawing conclusions.

## Output format

Return ONLY valid JSON matching this schema:

{
  "title": "Short descriptive title for this research brief",
  "summary": "2-4 sentence executive summary of the key findings",
  "sections": [
    {
      "heading": "Section title",
      "content": "Detailed findings in markdown (use bullet points, bold, links)",
      "sources": ["source name or URL"]
    }
  ],
  "key_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3"],
  "confidence": "high | medium | low",
  "confidence_note": "Brief note on why this confidence level (e.g. limited sources, conflicting info)"
}

## Research guidelines

- Search for multiple perspectives — never rely on a single source.
- Prefer recent information (within the last 12 months) when recency matters.
- Clearly distinguish facts from opinions.
- If sources conflict, note the disagreement.
- Include concrete numbers, dates, and specifics — avoid vague statements.
- If the topic relates to Tasin's context (provided below), connect findings \
to his situation.
"""

# Research categories that benefit from different search strategies.
_CATEGORY_HINTS: dict[str, str] = {
    "career": "Focus on industry trends, salary data, skill requirements, and job market outlook.",
    "technology": "Focus on technical comparisons, benchmarks, ecosystem maturity, and adoption trends.",
    "academic": "Focus on peer-reviewed sources, research papers, and expert opinions.",
    "finance": "Focus on market data, regulatory info, and risk/reward analysis. NEVER recommend specific investments.",
    "health": "Focus on evidence-based information from medical/scientific sources. Include disclaimers.",
    "travel": "Focus on logistics, costs, safety, reviews, and seasonal considerations.",
    "general": "Provide a thorough, balanced overview of the topic.",
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "career": ["job", "career", "salary", "interview", "resume", "hiring", "industry", "role", "position"],
    "technology": ["software", "framework", "language", "tool", "library", "api", "database", "cloud", "tech"],
    "academic": ["research", "paper", "study", "university", "professor", "thesis", "academic", "journal"],
    "finance": ["invest", "stock", "market", "crypto", "saving", "budget", "economic", "financial"],
    "health": ["health", "fitness", "nutrition", "medical", "exercise", "diet", "wellness", "sleep"],
    "travel": ["travel", "flight", "hotel", "trip", "destination", "visa", "vacation"],
}


class ResearchAgent(BaseAgent):
    """Deep research using Claude Code with Brave Search MCP.

    Spawns Claude Code with the ``research`` MCP profile, giving it
    access to Brave Search for web research.  Returns structured
    research briefs with sourced sections and confidence ratings.

    Read-only — tier 1, no approval required.
    """

    agent_type = "research"
    autonomy_class = AutonomyClass.READ

    def __init__(self) -> None:
        self._claude = ClaudeCodeSpawner()

    async def execute(self, context: AgentContext) -> AgentResult:
        """Perform deep research on the user's topic."""
        user_message = context.user_message

        if not user_message.strip():
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="What would you like me to research? Please provide a topic or question.",
                error="empty_query",
            )

        # 1. Classify the research category
        category = _classify_category(user_message)
        category_guidance = _CATEGORY_HINTS.get(category, _CATEGORY_HINTS["general"])

        # 2. Gather user context for cross-referencing
        user_context = await self._build_user_context(context)

        # 3. Build the Claude prompt
        prompt = self._build_prompt(
            user_request=user_message,
            category=category,
            category_guidance=category_guidance,
            user_context=user_context,
        )

        # 4. Spawn Claude Code with brave-search MCP
        try:
            result = await self._claude.execute(
                prompt=prompt,
                mcp_profile="research",
                timeout=180,
                max_turns=5,
            )
        except ClaudeSpawnError:
            log.error("research_claude_spawn_failed", exc_info=True)
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="I can't perform research right now — Claude is unavailable. Please try again shortly.",
                error="claude_unavailable",
            )

        if not result.success or not result.text.strip():
            log.error(
                "research_failed",
                error=result.error,
                duration_ms=result.duration_ms,
            )
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="I had trouble researching that topic. Could you try rephrasing your question?",
                error=result.error or "empty_response",
            )

        # 5. Parse the structured JSON response
        research = _parse_research_json(result.text)

        if research is None:
            # Fallback: return raw text if JSON parsing fails
            log.warning(
                "research_json_parse_failed",
                raw_len=len(result.text),
                duration_ms=result.duration_ms,
            )
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=True,
                text=result.text.strip(),
                content_type="text",
                data={
                    "model": "claude_code",
                    "category": category,
                    "duration_ms": result.duration_ms,
                    "cost_usd": result.cost_usd,
                },
            )

        # 6. Build display text and section cards
        title = research.get("title", "Research Brief")
        summary = research.get("summary", "")
        sections = research.get("sections", [])
        takeaways = research.get("key_takeaways", [])
        confidence = research.get("confidence", "medium")
        confidence_note = research.get("confidence_note", "")

        display_text = f"## {title}\n\n{summary}"
        if takeaways:
            display_text += "\n\n**Key Takeaways:**\n"
            display_text += "\n".join(f"- {t}" for t in takeaways)
        if confidence_note:
            display_text += f"\n\n*Confidence: {confidence} — {confidence_note}*"

        cards = _build_section_cards(sections)

        log.info(
            "research_completed",
            title=title,
            category=category,
            sections=len(sections),
            takeaways=len(takeaways),
            confidence=confidence,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )

        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=display_text,
            content_type="card" if cards else "text",
            cards=cards,
            data={
                "model": "claude_code",
                "category": category,
                "title": title,
                "confidence": confidence,
                "sections_count": len(sections),
                "duration_ms": result.duration_ms,
                "cost_usd": result.cost_usd,
                "raw_research": research,
            },
        )

    # ------------------------------------------------------------------
    # User context for cross-referencing
    # ------------------------------------------------------------------

    async def _build_user_context(self, context: AgentContext) -> str:
        """Gather relevant user context from the DB for cross-referencing.

        Pulls recent conversations, active job searches, and calendar
        context so Claude can connect research findings to Tasin's
        situation.
        """
        parts: list[str] = []

        # Pull recent conversation topics for relevance
        recent_topics = await self._fetch_recent_topics()
        if recent_topics:
            parts.append("Recent topics Tasin has been discussing:")
            for topic in recent_topics:
                parts.append(f"  - {topic}")

        # Pull active job search context if relevant
        active_jobs = await self._fetch_active_job_context()
        if active_jobs:
            parts.append("Active job search context:")
            for job in active_jobs:
                parts.append(f"  - {job}")

        # Include any config-provided context
        extra_context = context.config.get("research_context", "")
        if extra_context:
            parts.append(f"Additional context: {extra_context}")

        return "\n".join(parts)

    async def _fetch_recent_topics(self, limit: int = 5) -> list[str]:
        """Fetch recent conversation topics from message content."""
        from db.models import Message
        from db.session import get_db_session

        try:
            async with get_db_session() as session:
                result = await session.execute(
                    select(Message.content)
                    .where(Message.role == "user")
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
                rows = result.scalars().all()
            return [content[:100] for content in rows if content]
        except Exception:
            log.debug("research_fetch_topics_failed", exc_info=True)
            return []

    async def _fetch_active_job_context(self, limit: int = 5) -> list[str]:
        """Fetch active job search context for cross-referencing."""
        from shared.constants import JobStatus

        from db.models import JobListing
        from db.session import get_db_session

        try:
            async with get_db_session() as session:
                result = await session.execute(
                    select(
                        JobListing.title,
                        JobListing.company,
                    )
                    .where(
                        JobListing.status.in_(
                            [
                                JobStatus.SAVED,
                                JobStatus.APPLIED,
                                JobStatus.INTERVIEW,
                            ]
                        )
                    )
                    .order_by(JobListing.created_at.desc())
                    .limit(limit)
                )
                rows = result.all()
            return [f"{row.title} at {row.company}" for row in rows if row.title]
        except Exception:
            log.debug("research_fetch_jobs_failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        user_request: str,
        category: str,
        category_guidance: str,
        user_context: str,
    ) -> str:
        """Build the full prompt for Claude Code."""
        parts: list[str] = [_SYSTEM_PROMPT]

        parts.append(f"## Research category: {category}")
        parts.append(category_guidance)
        parts.append("")

        parts.append(f"## Research request\n{user_request}\n")

        if user_context:
            parts.append(f"## Tasin's current context\n{user_context}\n")
            parts.append(
                "Cross-reference your findings with the above context where relevant. "
                "For example, if Tasin is job-searching in a specific field, connect "
                "industry research to his job search.\n"
            )

        parts.append(
            "## Execution\n"
            "Use the Brave Search MCP tool to search for authoritative sources. "
            "Perform at least 2-3 searches with different query angles to ensure "
            "comprehensive coverage. Then synthesize your findings into the JSON "
            "format specified above."
        )

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _classify_category(message: str) -> str:
    """Classify the research query into a category for targeted guidance."""
    msg_lower = message.lower()

    scores: dict[str, int] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > 0:
            scores[category] = score

    if not scores:
        return "general"

    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _parse_research_json(text: str) -> dict[str, Any] | None:
    """Extract and parse JSON from Claude's response.

    Handles cases where JSON is wrapped in markdown code fences.
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` fences
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _build_section_cards(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert research sections into display cards."""
    cards: list[dict[str, Any]] = []
    for section in sections:
        card: dict[str, Any] = {
            "heading": section.get("heading", ""),
            "content": section.get("content", ""),
            "sources": section.get("sources", []),
        }
        if card["heading"] or card["content"]:
            cards.append(card)
    return cards
