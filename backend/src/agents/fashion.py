"""Fashion & Outfit agent — wardrobe cataloging, outfit suggestions, shopping advice.

Uses Gemini Vision for photo analysis and Gemini Pro for outfit composition.
Weather + calendar context drives formality and layering decisions.
Wardrobe images are stored locally on Node 2 only (HC-11).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update

from agents.base import AgentContext, AgentResult, BaseAgent

log = structlog.get_logger()

_CATALOG_SYSTEM_PROMPT = (
    "You are a fashion analysis assistant. Analyze clothing items from photos. "
    "Return a JSON object with these fields: "
    "item_type (top/bottom/outerwear/footwear/accessory/full_body), "
    "sub_type (e.g. t-shirt, jeans, blazer, sneakers, watch, dress), "
    "color (primary color), "
    "pattern (solid/striped/plaid/floral/graphic/other), "
    "seasons (list from: spring, summer, fall, winter), "
    "formality (casual/smart_casual/business_casual/formal), "
    "brand (if visible, else null), "
    "description (one sentence). "
    "Respond with ONLY valid JSON, no markdown."
)

_SUGGEST_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant for Tasin. "
    "You help with daily outfit suggestions based on wardrobe, weather, and schedule. "
    "Be practical, concise, and consider comfort alongside style."
)

_SHOPPING_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal style advisor for Tasin. "
    "Analyze wardrobe gaps and suggest purchases. Be practical and budget-conscious."
)


class FashionAgent(BaseAgent):
    """Fashion agent — outfit suggestions, wardrobe cataloging, shopping advice.

    Routes to sub-actions based on ``context.config["action"]``:
    - ``suggest``: Daily outfit suggestion (default)
    - ``catalog``: Catalog a new item from a photo
    - ``shopping_advisor``: Wardrobe gap analysis
    """

    agent_type = "fashion"

    def __init__(self) -> None:
        from config import get_settings
        from integrations.caldav_client import CalDAVClient
        from integrations.weather_client import WeatherClient
        from models.gemini_client import GeminiClient

        settings = get_settings()

        self._gemini = GeminiClient(api_key=settings.gemini_api_key)
        self._weather = WeatherClient(
            api_key=settings.openweathermap_api_key,
            default_location=settings.default_location,
        )
        self._caldav = CalDAVClient(
            username=settings.icloud_caldav_user,
            password=settings.icloud_caldav_password,
        )

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def execute(self, context: AgentContext) -> AgentResult:
        action = context.config.get("action", "suggest")

        if action == "catalog":
            return await self._catalog_item(context)
        elif action == "shopping_advisor":
            return await self._shopping_advisor(context)
        else:
            return await self._suggest_outfit(context)

    # ------------------------------------------------------------------
    # Outfit suggestion
    # ------------------------------------------------------------------

    async def _suggest_outfit(self, context: AgentContext) -> AgentResult:
        """Daily outfit suggestion using weather, calendar, and wardrobe data."""
        import asyncio

        today = date.today()

        # 1. Fetch weather + calendar + wardrobe in parallel
        weather_task = self._fetch_weather()
        calendar_task = self._fetch_today_events()
        wardrobe_task = self._fetch_active_wardrobe()
        recent_task = self._fetch_recently_worn(days=7)

        weather, events, wardrobe_items, recent_worn = await asyncio.gather(
            weather_task, calendar_task, wardrobe_task, recent_task,
            return_exceptions=True,
        )

        # Graceful degradation for each source
        if isinstance(weather, Exception):
            log.warning("fashion_weather_failed", error=str(weather))
            weather = {}
        if isinstance(events, Exception):
            log.warning("fashion_calendar_failed", error=str(events))
            events = []
        if isinstance(wardrobe_items, Exception):
            log.warning("fashion_wardrobe_failed", error=str(wardrobe_items))
            wardrobe_items = []
        if isinstance(recent_worn, Exception):
            log.warning("fashion_recent_worn_failed", error=str(recent_worn))
            recent_worn = []

        if not wardrobe_items:
            return AgentResult(
                success=True,
                text="Your wardrobe is empty. Upload some items first using the wardrobe catalog feature.",
                data={"weather": weather, "events": events},
            )

        # 2. Determine formality needs from calendar
        formality = _determine_formality(events)

        # 3. Determine season from weather
        season = _determine_season(weather)

        # 4. Build wardrobe catalog for the prompt
        catalog = _build_wardrobe_catalog(wardrobe_items, recent_worn)

        # 5. Build prompt and call Gemini Pro
        prompt = (
            f"Suggest an outfit for Tasin today ({today.strftime('%A, %B %d')}).\n\n"
            f"Weather: {json.dumps(weather, default=str)}\n\n"
            f"Today's schedule:\n{_format_events(events)}\n\n"
            f"Formality needed: {formality}\n"
            f"Season: {season}\n\n"
            f"Available wardrobe items (recently worn items marked with *):\n"
            f"{catalog}\n\n"
            "Respond with a JSON object containing:\n"
            '- "items": list of item IDs to wear (from the catalog above)\n'
            '- "suggestion": a brief natural-language description of the outfit\n'
            '- "reasoning": why this outfit works for today\n'
            "Respond with ONLY valid JSON, no markdown."
        )

        try:
            response = await self._gemini.generate(
                prompt=prompt,
                model="gemini-2.0-pro",
                system_instruction=_SUGGEST_SYSTEM_PROMPT,
                temperature=0.7,
                response_format="json",
            )

            suggestion = json.loads(response.text)

            log.info(
                "fashion_suggestion_generated",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
                items_suggested=len(suggestion.get("items", [])),
            )

        except (json.JSONDecodeError, Exception) as exc:
            log.error("fashion_suggestion_failed", error=str(exc), exc_info=True)
            return AgentResult(
                success=False,
                text="I had trouble generating an outfit suggestion. Please try again.",
                error=str(exc),
            )

        # 6. Store suggestion in wardrobe_outfits table
        outfit_id = await self._store_outfit(
            today, suggestion, weather, events,
        )

        # 7. Resolve item details for the response
        suggested_items = _resolve_suggested_items(
            suggestion.get("items", []), wardrobe_items,
        )

        return AgentResult(
            success=True,
            text=suggestion.get("suggestion", "Here's your outfit for today."),
            content_type="card",
            cards=[{
                "type": "outfit",
                "outfit_id": str(outfit_id),
                "date": today.isoformat(),
                "items": suggested_items,
                "reasoning": suggestion.get("reasoning", ""),
                "weather": weather,
            }],
            data={
                "outfit_id": str(outfit_id),
                "suggestion": suggestion,
                "weather": weather,
                "formality": formality,
                "season": season,
            },
        )

    # ------------------------------------------------------------------
    # Wardrobe cataloging
    # ------------------------------------------------------------------

    async def _catalog_item(self, context: AgentContext) -> AgentResult:
        """Catalog a new wardrobe item from a photo using Gemini Vision."""
        if not context.attachments:
            return AgentResult(
                success=False,
                text="Please attach a photo of the clothing item to catalog.",
                error="no_attachment",
            )

        attachment = context.attachments[0]
        image_data = attachment.get("data")
        if not image_data:
            return AgentResult(
                success=False,
                text="The attached image appears to be empty.",
                error="empty_attachment",
            )

        # Decode base64 image data
        import base64
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return AgentResult(
                success=False,
                text="Could not decode the image. Please try again with a JPEG or PNG.",
                error="decode_failed",
            )

        # 1. Analyze image with Gemini Vision
        try:
            response = await self._gemini.generate_with_vision(
                prompt="Analyze this clothing item. Return the analysis as JSON.",
                images=[image_bytes],
                model="gemini-2.0-flash",
                system_instruction=_CATALOG_SYSTEM_PROMPT,
            )

            analysis = json.loads(response.text)

            log.info(
                "fashion_catalog_analyzed",
                model=response.model,
                item_type=analysis.get("item_type"),
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )

        except json.JSONDecodeError:
            log.error("fashion_catalog_json_failed", raw=response.text[:200])
            return AgentResult(
                success=False,
                text="I couldn't parse the image analysis. Please try again.",
                error="json_parse_failed",
            )
        except Exception as exc:
            log.error("fashion_catalog_failed", error=str(exc), exc_info=True)
            return AgentResult(
                success=False,
                text="Image analysis failed. Please try again.",
                error=str(exc),
            )

        # 2. Compute image hash for dedup
        import hashlib
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        # 3. Store wardrobe item in DB
        item_id = await self._store_wardrobe_item(analysis, image_hash)

        # 4. Dispatch image save to Node 2 via Redis job
        image_path = f"/data/wardrobe/{item_id}.jpg"
        await self._dispatch_image_save(item_id, image_bytes, image_path)

        # 5. Update image_path in DB
        await self._update_item_image_path(item_id, image_path)

        description = analysis.get("description", "Item cataloged.")

        return AgentResult(
            success=True,
            text=f"Cataloged: {description}",
            content_type="card",
            cards=[{
                "type": "wardrobe_item",
                "item_id": str(item_id),
                "analysis": analysis,
            }],
            data={
                "wardrobe_item_id": str(item_id),
                "analysis": analysis,
                "image_path": image_path,
            },
        )

    # ------------------------------------------------------------------
    # Shopping advisor
    # ------------------------------------------------------------------

    async def _shopping_advisor(self, context: AgentContext) -> AgentResult:
        """Wardrobe gap analysis for shopping recommendations."""
        wardrobe_items = await self._fetch_active_wardrobe()

        if not wardrobe_items:
            return AgentResult(
                success=True,
                text=(
                    "Your wardrobe is empty — start by cataloging your current clothes, "
                    "then I can identify gaps and suggest purchases."
                ),
            )

        # Build wardrobe summary by category
        summary = _build_wardrobe_summary(wardrobe_items)
        season = _current_season()

        user_query = context.user_message or "general wardrobe gap analysis"

        prompt = (
            f"Analyze this wardrobe for gaps and suggest purchases.\n\n"
            f"User's request: {user_query}\n\n"
            f"Current season: {season}\n\n"
            f"Wardrobe inventory:\n{json.dumps(summary, indent=2)}\n\n"
            f"Total items: {len(wardrobe_items)}\n\n"
            "Provide:\n"
            "1. Identified gaps (missing item types, limited color palette, season coverage)\n"
            "2. Top 3-5 purchase recommendations with reasoning\n"
            "3. Priority ranking (essential vs nice-to-have)\n\n"
            "Be practical and budget-conscious. Tasin is a college student."
        )

        try:
            response = await self._gemini.generate(
                prompt=prompt,
                model="gemini-2.0-pro",
                system_instruction=_SHOPPING_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=2048,
            )

            log.info(
                "fashion_shopping_advice",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )

            return AgentResult(
                success=True,
                text=response.text.strip(),
                data={
                    "wardrobe_summary": summary,
                    "total_items": len(wardrobe_items),
                    "season": season,
                },
            )

        except Exception as exc:
            log.error("fashion_shopping_failed", error=str(exc), exc_info=True)
            return AgentResult(
                success=False,
                text="I had trouble analyzing your wardrobe. Please try again.",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    async def _fetch_weather(self) -> dict[str, Any]:
        """Fetch current weather and daily summary."""
        import asyncio

        current, summary = await asyncio.gather(
            self._weather.get_current(),
            self._weather.get_daily_summary(),
        )
        return {**current, **summary}

    async def _fetch_today_events(self) -> list[dict[str, Any]]:
        """Fetch today's calendar events."""
        return await self._caldav.get_today_events()

    async def _fetch_active_wardrobe(self) -> list[dict[str, Any]]:
        """Fetch all active wardrobe items from the DB."""
        from db.models import WardrobeItem
        from db.session import get_db_session

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    WardrobeItem.id,
                    WardrobeItem.item_type,
                    WardrobeItem.sub_type,
                    WardrobeItem.color,
                    WardrobeItem.pattern,
                    WardrobeItem.seasons,
                    WardrobeItem.formality,
                    WardrobeItem.brand,
                    WardrobeItem.image_path,
                    WardrobeItem.last_worn,
                    WardrobeItem.wear_count,
                )
                .where(WardrobeItem.active == True)  # noqa: E712
                .order_by(WardrobeItem.item_type, WardrobeItem.color)
            )
            rows = result.all()

        return [
            {
                "id": str(row.id),
                "item_type": row.item_type,
                "sub_type": row.sub_type,
                "color": row.color,
                "pattern": row.pattern,
                "seasons": row.seasons,
                "formality": row.formality,
                "brand": row.brand,
                "image_path": row.image_path,
                "last_worn": row.last_worn.isoformat() if row.last_worn else None,
                "wear_count": row.wear_count,
            }
            for row in rows
        ]

    async def _fetch_recently_worn(self, days: int = 7) -> list[str]:
        """Fetch item IDs worn in the last N days."""
        from db.models import WardrobeOutfit
        from db.session import get_db_session

        since = date.today() - timedelta(days=days)

        async with get_db_session() as session:
            result = await session.execute(
                select(WardrobeOutfit.items)
                .where(
                    WardrobeOutfit.outfit_date >= since,
                    WardrobeOutfit.was_worn == True,  # noqa: E712
                )
            )
            rows = result.all()

        # Flatten all item IDs from recent outfits
        worn_ids: list[str] = []
        for (items,) in rows:
            if isinstance(items, list):
                worn_ids.extend(str(i) for i in items)
        return worn_ids

    # ------------------------------------------------------------------
    # Database storage helpers
    # ------------------------------------------------------------------

    async def _store_outfit(
        self,
        outfit_date: date,
        suggestion: dict[str, Any],
        weather: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> uuid.UUID:
        """Store an outfit suggestion in the wardrobe_outfits table."""
        from db.models import WardrobeOutfit
        from db.session import get_db_session

        outfit_id = uuid.uuid4()

        async with get_db_session() as session:
            outfit = WardrobeOutfit(
                id=outfit_id,
                outfit_date=outfit_date,
                items=suggestion.get("items", []),
                weather_context=weather,
                calendar_context={"events": events},
                reasoning=suggestion.get("reasoning"),
            )
            session.add(outfit)

        log.info("fashion_outfit_stored", outfit_id=str(outfit_id), date=outfit_date.isoformat())
        return outfit_id

    async def _store_wardrobe_item(
        self,
        analysis: dict[str, Any],
        image_hash: str,
    ) -> uuid.UUID:
        """Store a cataloged wardrobe item in the DB."""
        from db.models import WardrobeItem
        from db.session import get_db_session

        item_id = uuid.uuid4()

        async with get_db_session() as session:
            item = WardrobeItem(
                id=item_id,
                item_type=analysis.get("item_type", "unknown"),
                sub_type=analysis.get("sub_type"),
                color=analysis.get("color", "unknown"),
                pattern=analysis.get("pattern", "solid"),
                seasons=analysis.get("seasons", []),
                formality=analysis.get("formality", "casual"),
                brand=analysis.get("brand"),
                image_path="",  # updated after image dispatch
                image_hash=image_hash,
                gemini_raw=analysis,
            )
            session.add(item)

        log.info(
            "fashion_item_stored",
            item_id=str(item_id),
            item_type=analysis.get("item_type"),
            color=analysis.get("color"),
        )
        return item_id

    async def _update_item_image_path(
        self, item_id: uuid.UUID, image_path: str,
    ) -> None:
        """Update the image_path for a wardrobe item after image dispatch."""
        from db.models import WardrobeItem
        from db.session import get_db_session

        async with get_db_session() as session:
            await session.execute(
                update(WardrobeItem)
                .where(WardrobeItem.id == item_id)
                .values(image_path=image_path)
            )

    async def _dispatch_image_save(
        self,
        item_id: uuid.UUID,
        image_bytes: bytes,
        image_path: str,
    ) -> None:
        """Dispatch an image save job to Node 2 via Redis.

        If Redis is unavailable, logs a warning but does not fail the
        cataloging operation (HC-09 graceful degradation).
        """
        try:
            import redis.asyncio as aioredis
            from config import get_settings

            settings = get_settings()
            r = aioredis.from_url(settings.redis_url)

            job_payload = json.dumps({
                "job_type": "save_wardrobe_image",
                "item_id": str(item_id),
                "image_path": image_path,
            })
            # Store image bytes separately with TTL
            await r.setex(f"wardrobe_image:{item_id}", 3600, image_bytes)
            await r.lpush("tars:jobs", job_payload)
            await r.aclose()

            log.info("fashion_image_dispatched", item_id=str(item_id), path=image_path)

        except Exception as exc:
            log.warning(
                "fashion_image_dispatch_failed",
                item_id=str(item_id),
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _determine_formality(events: list[dict[str, Any]]) -> str:
    """Determine the formality level needed based on today's calendar."""
    if not events:
        return "casual"

    formal_keywords = {"interview", "meeting", "presentation", "dinner", "formal", "conference"}
    smart_casual_keywords = {"lunch", "class", "seminar", "office", "work", "career fair"}

    for event in events:
        title = (event.get("title") or "").lower()
        desc = (event.get("description") or "").lower()
        combined = f"{title} {desc}"

        if any(kw in combined for kw in formal_keywords):
            return "formal"
        if any(kw in combined for kw in smart_casual_keywords):
            return "smart_casual"

    return "casual"


def _determine_season(weather: dict[str, Any]) -> str:
    """Determine the effective season from weather data."""
    temp_c = weather.get("temp_c")
    if temp_c is None:
        return _current_season()

    if temp_c <= 5:
        return "winter"
    elif temp_c <= 15:
        return "fall"
    elif temp_c <= 25:
        return "spring"
    else:
        return "summer"


def _current_season() -> str:
    """Get the current calendar season."""
    month = date.today().month
    if month in (12, 1, 2):
        return "winter"
    elif month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    else:
        return "fall"


def _build_wardrobe_catalog(
    items: list[dict[str, Any]],
    recent_worn: list[str],
) -> str:
    """Build a text catalog of wardrobe items for the prompt."""
    lines: list[str] = []
    for item in items:
        item_id = item["id"]
        recently = "*" if item_id in recent_worn else ""
        brand_str = f" ({item['brand']})" if item.get("brand") else ""
        line = (
            f"- [{item_id}]{recently} {item['item_type']}/{item.get('sub_type', '?')}: "
            f"{item['color']} {item.get('pattern', 'solid')}{brand_str}, "
            f"formality={item['formality']}, "
            f"seasons={item.get('seasons', [])}, "
            f"worn {item.get('wear_count', 0)} times"
        )
        lines.append(line)
    return "\n".join(lines) if lines else "(empty wardrobe)"


def _format_events(events: list[dict[str, Any]]) -> str:
    """Format calendar events for the prompt."""
    if not events:
        return "(no events today)"

    lines: list[str] = []
    for event in events:
        time_str = event.get("start", "")
        title = event.get("title", "Untitled")
        location = event.get("location", "")
        loc_str = f" at {location}" if location else ""
        lines.append(f"- {time_str}: {title}{loc_str}")
    return "\n".join(lines)


def _resolve_suggested_items(
    item_ids: list[str],
    wardrobe_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve item IDs to full item details."""
    id_map = {item["id"]: item for item in wardrobe_items}
    resolved: list[dict[str, Any]] = []
    for item_id in item_ids:
        item_id_str = str(item_id)
        if item_id_str in id_map:
            resolved.append(id_map[item_id_str])
    return resolved


def _build_wardrobe_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary of the wardrobe by category for gap analysis."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    colors: set[str] = set()
    formalities: dict[str, int] = {}
    seasons_coverage: dict[str, int] = {}

    for item in items:
        item_type = item.get("item_type", "unknown")
        by_type.setdefault(item_type, []).append({
            "sub_type": item.get("sub_type"),
            "color": item.get("color"),
            "formality": item.get("formality"),
        })

        colors.add(item.get("color", "unknown"))

        formality = item.get("formality", "casual")
        formalities[formality] = formalities.get(formality, 0) + 1

        for s in item.get("seasons", []):
            seasons_coverage[s] = seasons_coverage.get(s, 0) + 1

    return {
        "by_type": {k: {"count": len(v), "items": v} for k, v in by_type.items()},
        "color_palette": sorted(colors),
        "formality_distribution": formalities,
        "seasons_coverage": seasons_coverage,
    }
