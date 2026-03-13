"""Product research agent — researches and compares products via Gemini Pro.

Uses Gemini Pro with web search grounding to find product options,
compare specifications, pricing, and reviews, then returns structured
comparison cards for the user.

T.A.R.S. NEVER purchases anything (HC-04 spirit) — results include links
for the user to buy themselves.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant helping Tasin research products. "
    "You are thorough, objective, and concise. You compare products across price, "
    "quality, reviews, and value. You NEVER make purchases or recommend a single "
    "option without alternatives — always present at least 2-3 options so Tasin "
    "can decide. Include real product names, approximate prices, and where to buy."
)

_RESEARCH_PROMPT_TEMPLATE = """\
Research the following product request and return a JSON object with your findings.

## User's request
{user_request}

{budget_section}
{preferences_section}

## Instructions
Research this product category thoroughly. Find 3-5 good options across different \
price points and sources.

Return ONLY valid JSON in this exact format:
{{
  "summary": "Brief 1-2 sentence overview of the product category and key things to consider",
  "products": [
    {{
      "name": "Full product name with model/variant",
      "price": "$XX.XX",
      "rating": "X.X/5",
      "source": "Where to buy (e.g. Amazon, Best Buy, direct)",
      "pros": ["pro 1", "pro 2", "pro 3"],
      "cons": ["con 1", "con 2"],
      "url_hint": "Search query to find this product online",
      "best_for": "Who/what this option is best for"
    }}
  ],
  "recommendation": "Which option you'd suggest for Tasin and why (1-2 sentences)"
}}

Sort products by best overall value. Include budget, mid-range, and premium options \
when the category allows it.
"""


class ProductResearchAgent(BaseAgent):
    """Researches and compares products using Gemini Pro.

    Returns structured comparison cards. Never purchases anything —
    results include search hints for the user to find and buy themselves.
    """

    agent_type = "product_research"

    async def execute(self, context: AgentContext) -> AgentResult:
        """Research and compare products using Gemini Pro."""
        user_message = context.user_message

        # 1. Parse the query for budget/preference hints
        parsed = _parse_product_query(user_message)

        # 2. Build the research prompt
        budget_section = ""
        if parsed["budget"]:
            budget_section = f"## Budget\n{parsed['budget']}"

        preferences_section = ""
        if parsed["preferences"]:
            prefs = "\n".join(f"- {p}" for p in parsed["preferences"])
            preferences_section = f"## Preferences\n{prefs}"

        prompt = _RESEARCH_PROMPT_TEMPLATE.format(
            user_request=user_message,
            budget_section=budget_section,
            preferences_section=preferences_section,
        )

        # 3. Call Gemini Pro
        gemini: GeminiClient | None = context.config.get("gemini_client")
        if gemini is None:
            log.warning("product_research_no_gemini")
            return AgentResult(
                success=False,
                text="I can't research products right now — the AI model is unavailable. Please try again shortly.",
                error="gemini_unavailable",
            )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-pro",
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.4,
                max_output_tokens=4096,
                response_format="json",
            )
        except Exception:
            log.error("product_research_gemini_failed", exc_info=True)
            return AgentResult(
                success=False,
                text="I had trouble researching that product. Could you try again?",
                error="gemini_error",
            )

        # 4. Parse the JSON response
        try:
            research = json.loads(response.text)
        except json.JSONDecodeError:
            log.error(
                "product_research_parse_failed",
                raw_len=len(response.text),
            )
            # Fall back to returning raw text
            return AgentResult(
                success=True,
                text=response.text.strip(),
                content_type="text",
                data={
                    "model": response.model,
                    "tokens_input": response.tokens_input,
                    "tokens_output": response.tokens_output,
                    "duration_ms": response.duration_ms,
                },
            )

        # 5. Build comparison cards
        products = research.get("products", [])
        cards = _build_product_cards(products)
        summary = research.get("summary", "")
        recommendation = research.get("recommendation", "")

        display_text = summary
        if recommendation:
            display_text += f"\n\n**Recommendation:** {recommendation}"

        log.info(
            "product_research_completed",
            products_found=len(cards),
            model=response.model,
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            duration_ms=response.duration_ms,
        )

        return AgentResult(
            success=True,
            text=display_text,
            content_type="card",
            cards=cards,
            data={
                "model": response.model,
                "tokens_input": response.tokens_input,
                "tokens_output": response.tokens_output,
                "duration_ms": response.duration_ms,
                "raw_research": research,
            },
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_product_query(message: str) -> dict[str, Any]:
    """Extract budget and preference hints from the user's message."""
    budget = ""
    preferences: list[str] = []

    # Budget patterns: "under $100", "budget of $50", "$200-$300", "around $150"
    budget_match = re.search(
        r"(?:under|below|less than|max|budget(?:\s+of)?|around|about|up to)\s*\$?\s*(\d+[\d,.]*)",
        message,
        re.IGNORECASE,
    )
    if budget_match:
        budget = f"Under ${budget_match.group(1)}"

    range_match = re.search(r"\$(\d+[\d,.]*)\s*[-–to]+\s*\$?(\d+[\d,.]*)", message)
    if range_match:
        budget = f"${range_match.group(1)} - ${range_match.group(2)}"

    # Preference keywords
    pref_keywords = {
        "wireless": "Wireless connectivity",
        "bluetooth": "Bluetooth support",
        "portable": "Portability",
        "compact": "Compact size",
        "lightweight": "Lightweight",
        "durable": "Durability",
        "waterproof": "Waterproof/water-resistant",
        "noise cancel": "Noise cancellation",
        "long battery": "Long battery life",
        "fast charging": "Fast charging",
        "ergonomic": "Ergonomic design",
    }
    msg_lower = message.lower()
    for keyword, pref in pref_keywords.items():
        if keyword in msg_lower:
            preferences.append(pref)

    return {
        "budget": budget,
        "preferences": preferences,
    }


def _build_product_cards(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw product data into structured comparison cards."""
    cards: list[dict[str, Any]] = []
    for product in products:
        card: dict[str, Any] = {
            "name": product.get("name", "Unknown Product"),
            "price": product.get("price", "Price unavailable"),
            "rating": product.get("rating", "N/A"),
            "source": product.get("source", ""),
            "pros": product.get("pros", []),
            "cons": product.get("cons", []),
            "url_hint": product.get("url_hint", ""),
            "best_for": product.get("best_for", ""),
        }
        cards.append(card)
    return cards
