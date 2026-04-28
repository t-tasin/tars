"""Morning briefing agent — fetches data, builds payload, composes narrative.

Phase 1: Fetches all data sources in parallel using zero AI tokens.
Phase 2: Builds structured briefing payload (Section 7.2.1 format).
Phase 3: Composes natural-language narrative via Gemini Pro.
Phase 4: Stores payload + narrative in the briefings table.

Individual fetch failures are handled gracefully (HC-09) —
a single integration being down never crashes the briefing.

Heavy imports (db.session, config, integration clients) are deferred to
``__init__`` / method bodies so the module can be imported without a live
database or environment variables (needed for testing).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from shared.constants import AutonomyClass, EmailTier, JobStatus, ModelName
from sqlalchemy import func, select

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient

log = structlog.get_logger()

# Default section ordering if no config is provided.
DEFAULT_SECTIONS: list[dict[str, Any]] = [
    {"name": "weather", "priority": 1, "enabled": True},
    {"name": "schedule", "priority": 2, "enabled": True},
    {"name": "email_digest", "priority": 3, "enabled": True},
    {"name": "tasks_due", "priority": 4, "enabled": True},
    {"name": "job_matches", "priority": 5, "enabled": True},
    {"name": "system_health", "priority": 6, "enabled": True},
    {"name": "health_summary", "priority": 8, "enabled": True},
    {"name": "finance_summary", "priority": 9, "enabled": True},
    {"name": "proactive_suggestions", "priority": 10, "enabled": True},
]

_NARRATIVE_SYSTEM_PROMPT = (
    "You are T.A.R.S., a personal AI assistant for Tasin. "
    "You are warm, concise, and efficient. You speak directly to Tasin in second person."
)

_FALLBACK_NARRATIVE = "Good morning, Tasin. I had trouble composing your briefing narrative today, but your data is ready for review. Have a great day!"

_BRIEFING_LOCAL_SYSTEM_PROMPT = (
    "You are T.A.R.S., Tasin's personal AI assistant. "
    "Always respond in English. "
    "Generate a concise morning briefing from the structured data in the [CONTEXT] block. "
    "Speak to Tasin in second person. Be warm, specific, and efficient. "
    "Start with 'Good morning, Tasin.' and end with an encouraging note. "
    "Under 400 words. No markdown."
)


class BriefingAgent(BaseAgent):
    """Morning briefing — data aggregation, payload structuring, AI composition, DB storage.

    Collects data from 9 sources in parallel (zero AI tokens), builds the
    structured payload matching Section 7.2.1, composes a natural-language
    narrative via Gemini Pro, and stores both in the ``briefings`` table.
    """

    agent_type = "briefing"
    autonomy_class = AutonomyClass.READ

    def __init__(self) -> None:
        from config import get_settings
        from integrations.caldav_client import CalDAVClient
        from integrations.gmail_client import GmailClient
        from integrations.notion_client import NotionClient
        from integrations.weather_client import WeatherClient

        settings = get_settings()

        self._caldav = CalDAVClient(
            username=settings.icloud_caldav_user,
            password=settings.icloud_caldav_password,
        )
        self._gmail_personal = GmailClient(
            credentials_json_b64=settings.gmail_personal_credentials,
            account_name="personal",
        )
        self._gmail_professional = GmailClient(
            credentials_json_b64=settings.gmail_professional_credentials,
            account_name="professional",
        )
        self._weather = WeatherClient(
            api_key=settings.openweathermap_api_key,
            default_location=settings.default_location,
        )
        self._notion = NotionClient(token=settings.notion_token)
        self._notion_tasks_db: str | None = None  # set from config at execute time

        from models.local_client import LocalClient

        self._local_client = LocalClient(timeout_s=180.0)

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def execute(self, context: AgentContext) -> AgentResult:
        """Generate the complete morning briefing.

        Steps:
            1. Fetch all data sources in parallel (zero AI tokens).
            2. Build structured payload (Section 7.2.1 format).
            3. Compose natural-language narrative via Gemini Pro.
            4. Store payload + narrative in the ``briefings`` table.

        Expects ``context.config`` to optionally contain:
        - ``gemini_client``: a :class:`GeminiClient` for narrative composition
        - ``sections``: list of section config dicts with name/priority/enabled
        - ``email_classifications``: pre-classified email tiers (from EmailClassifierAgent)
        """
        # 0. Pull Notion tasks DB ID from config if available
        self._notion_tasks_db = context.config.get("notion_tasks_database_id")

        # 1. Fetch all data
        raw_data = await self._fetch_all_data()

        # 2. Build structured payload (for DB storage + Gemini fallback)
        email_classifications = context.config.get("email_classifications", [])
        payload = self._build_payload(raw_data, email_classifications)

        # 3. Compose narrative: LOCAL_BRAIN first (P2.5-02), Gemini as fallback (HC-09)
        try:
            local_context = self._build_local_context(raw_data)
            narrative = await self._compose_with_local(local_context)
        except Exception:
            log.warning("briefing_local_compose_failed", fallback="gemini", exc_info=True)
            gemini: GeminiClient | None = context.config.get("gemini_client")
            sections_config = context.config.get("sections", DEFAULT_SECTIONS)
            narrative = await self._compose_narrative(payload, sections_config, gemini)

        # 4. Store in briefings table
        briefing_id = await self._store_briefing(payload, narrative)

        log.info(
            "briefing_generated",
            briefing_id=str(briefing_id),
            sections=len(payload),
            narrative_len=len(narrative),
        )

        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=narrative,
            content_type="briefing",
            data={
                "briefing_id": str(briefing_id),
                "payload": payload,
            },
        )

    # ------------------------------------------------------------------
    # Payload structuring (Section 7.2.1 format)
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        raw_data: dict[str, Any],
        email_classifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Structure raw fetched data into the briefing JSON format from Section 7.2.1.

        Args:
            raw_data: Dict with keys from _fetch_all_data (calendar, emails, weather, etc.).
            email_classifications: Pre-classified emails from EmailClassifierAgent.
        """
        today = date.today()

        # --- Greeting ---
        greeting = "Good morning, Tasin."

        # --- Weather ---
        weather_raw = raw_data.get("weather", {})
        weather_summary = weather_raw.get("summary", {})
        weather = {
            "summary": weather_summary.get("summary", "Weather data unavailable."),
            "needs_umbrella": weather_summary.get("needs_umbrella", False),
            "suggestion": weather_summary.get("suggestion", ""),
            "current": weather_raw.get("current", {}),
            "forecast": weather_raw.get("forecast", []),
        }

        # --- Outfit (placeholder — FashionAgent handles this) ---
        outfit = {
            "suggestion": None,
            "reasoning": None,
            "image_refs": [],
        }

        # --- Schedule ---
        calendar_raw = raw_data.get("calendar", {})
        today_events = calendar_raw.get("today", [])
        schedule = _build_schedule(today_events)

        # --- Leave home by / commute ---
        leave_home_by = _compute_leave_home_by(today_events)
        commute_note = _build_commute_note(today_events)

        # --- Email digest ---
        email_digest = _build_email_digest(
            raw_data.get("emails", {}),
            email_classifications or [],
        )

        # --- System health ---
        sys_health_raw = raw_data.get("system_health", {})
        system_health = {
            "status": sys_health_raw.get("status", "unknown"),
            "details": _summarize_system_health(sys_health_raw),
        }

        # --- Tasks due today ---
        tasks_raw = raw_data.get("tasks", [])
        tasks_due_today = [t.get("title", str(t)) for t in tasks_raw] if tasks_raw else []

        # --- Job matches ---
        jobs_raw = raw_data.get("jobs", {})
        listings = jobs_raw.get("listings", [])
        top_match = None
        if listings:
            top = listings[0]
            score = top.get("match_score")
            score_str = f" — {score}% match" if score else ""
            top_match = f"{top.get('title', '?')} @ {top.get('company', '?')}{score_str}"
        job_matches = {
            "new_today": jobs_raw.get("new_today", 0),
            "top_match": top_match,
        }

        # --- Health ---
        health_raw = raw_data.get("health", {})
        health = _build_health_section(health_raw)

        # --- Finance ---
        finance_raw = raw_data.get("finance", {})
        finance = _build_finance_section(finance_raw)

        # --- Proactive suggestions ---
        proactive_suggestions = _build_proactive_suggestions(
            weather,
            health_raw,
            finance_raw,
            today_events,
        )

        # --- Unavailability note (HC-09) ---
        unavailable = raw_data.get("_unavailable_sources", [])
        unavailability_note = ""
        if unavailable:
            source_labels = {
                "calendar": "Calendar",
                "emails": "Email",
                "weather": "Weather",
                "health": "Health data",
                "finance": "Finance",
                "tasks": "Tasks",
                "github": "GitHub",
                "jobs": "Job listings",
                "system_health": "System health",
            }
            names = [source_labels.get(s, s) for s in unavailable]
            unavailability_note = f"Note: {', '.join(names)} data was temporarily unavailable and may be incomplete."

        return {
            "greeting": greeting,
            "date": today.isoformat(),
            "weather": weather,
            "outfit": outfit,
            "schedule": schedule,
            "leave_home_by": leave_home_by,
            "commute_note": commute_note,
            "email_digest": email_digest,
            "system_health": system_health,
            "tasks_due_today": tasks_due_today,
            "job_matches": job_matches,
            "health": health,
            "finance": finance,
            "proactive_suggestions": proactive_suggestions,
            "unavailability_note": unavailability_note,
        }

    # ------------------------------------------------------------------
    # LOCAL_BRAIN composition (P2.5-02)
    # ------------------------------------------------------------------

    def _build_local_context(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Build a truncated context dict for LOCAL_BRAIN composition.

        Truncation limits: 5 events, 5 emails per account (10 total), 5 tasks.
        """
        calendar = raw_data.get("calendar", {})
        emails = raw_data.get("emails", {})
        weather = raw_data.get("weather", {})
        tasks = raw_data.get("tasks", [])

        return {
            "weather": weather.get("current", {}),
            "weather_summary": weather.get("summary", {}),
            "schedule_today": calendar.get("today", [])[:5],
            "emails_personal": emails.get("personal", [])[:5],
            "emails_professional": emails.get("professional", [])[:5],
            "notion_tasks": tasks[:5],
        }

    async def _compose_with_local(self, context_dict: dict[str, Any]) -> str:
        """Compose briefing narrative using LOCAL_BRAIN (Qwen3-8B).

        Raises RuntimeError if no local_client is available (triggers Gemini fallback).
        """
        local = getattr(self, "_local_client", None)
        if local is None:
            raise RuntimeError("no local_client configured")

        prompt = (
            "[CONTEXT]\n" + json.dumps(context_dict, indent=2, default=str) + "\n[/CONTEXT]\n\n"
            "Generate a morning briefing narrative from the data above."
        )

        response = await local.generate(
            model=ModelName.LOCAL_BRAIN,
            prompt=prompt,
            system=_BRIEFING_LOCAL_SYSTEM_PROMPT,
            max_tokens=700,
            enable_thinking=False,
        )
        log.info(
            "briefing_local_composed",
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            duration_ms=response.duration_ms,
            finish_reason=response.finish_reason,
        )
        text = response.text.strip()
        if not text:
            raise RuntimeError(f"local briefing returned empty content (finish={response.finish_reason})")
        return text

    # ------------------------------------------------------------------
    # Narrative composition (Gemini Pro — fallback)
    # ------------------------------------------------------------------

    async def _compose_narrative(
        self,
        payload: dict[str, Any],
        sections_config: list[dict[str, Any]],
        gemini: GeminiClient | None,
    ) -> str:
        """Use Gemini Pro to compose a natural-language briefing narrative.

        If the Gemini client is unavailable, returns a fallback narrative
        (HC-09 graceful degradation).
        """
        if gemini is None:
            log.warning("briefing_no_gemini", fallback="plain_text")
            return _FALLBACK_NARRATIVE

        # Sort sections by priority, only include enabled ones
        active_sections = sorted(
            [s for s in sections_config if s.get("enabled", True)],
            key=lambda s: s.get("priority", 99),
        )
        section_names = [s["name"] for s in active_sections]

        # Build a clean data representation for the prompt
        prompt_data = _filter_payload_for_prompt(payload, section_names)

        # Include unavailability note in the prompt if any sources failed
        unavailability_note = payload.get("unavailability_note", "")
        unavailability_instruction = ""
        if unavailability_note:
            unavailability_instruction = (
                f"\nIMPORTANT: {unavailability_note} "
                "Briefly mention which data is missing near the end of the "
                "briefing so Tasin is aware, but don't dwell on it.\n"
            )

        prompt = (
            "Compose a concise morning briefing narrative from this data.\n"
            "Speak directly to Tasin in second person. Be warm but efficient.\n"
            f"Prioritize sections in this order: {section_names}\n"
            f"{unavailability_instruction}\n"
            f"Data:\n{json.dumps(prompt_data, indent=2, default=str)}\n\n"
            "Write a natural spoken narrative (will be read aloud via TTS).\n"
            "No markdown, no bullet points — flowing sentences.\n"
            'Start with "Good morning, Tasin." and end with an encouraging note.\n'
            "Keep it under 500 words."
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.5-flash",
                system_instruction=_NARRATIVE_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=2048,
            )
            log.info(
                "briefing_narrative_composed",
                model=response.model,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                duration_ms=response.duration_ms,
            )
            return response.text.strip()

        except Exception:
            log.error("briefing_narrative_failed", exc_info=True)
            return _FALLBACK_NARRATIVE

    # ------------------------------------------------------------------
    # Database storage
    # ------------------------------------------------------------------

    async def _store_briefing(
        self,
        payload: dict[str, Any],
        narrative: str,
    ) -> uuid.UUID:
        """Store the briefing payload and narrative in the ``briefings`` table.

        Uses ON CONFLICT DO UPDATE so repeated /briefing calls on the same day
        refresh the existing row rather than raising a unique-constraint error.

        Returns the UUID of the stored (or updated) briefing row.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from db.models import Briefing
        from db.session import get_db_session

        today = date.today()
        briefing_id = uuid.uuid4()

        async with get_db_session() as session:
            stmt = (
                pg_insert(Briefing)
                .values(
                    id=briefing_id,
                    briefing_type="morning",
                    briefing_date=today,
                    payload=payload,
                    narrative=narrative,
                )
                .on_conflict_do_update(
                    constraint="uq_briefings_date_type",
                    set_={"payload": payload, "narrative": narrative},
                )
            )
            await session.execute(stmt)

        log.info(
            "briefing_stored",
            briefing_id=str(briefing_id),
            briefing_date=today.isoformat(),
        )
        return briefing_id

    # ------------------------------------------------------------------
    # Parallel data aggregation
    # ------------------------------------------------------------------

    async def _fetch_all_data(self) -> dict[str, Any]:
        """Fetch all data sources in parallel. Zero AI tokens.

        Phase 3.5 (P3.5-07): weather/healthkit/tailscale/spotify prefer
        ``world_state`` over direct integration polling. Each fetcher falls
        back to the integration when the cached row is missing or stale.
        """
        results = await asyncio.gather(
            self._fetch_calendar(),
            self._fetch_emails(),
            self._fetch_weather(),
            self._fetch_health_data(),
            self._fetch_finance_data(),
            self._fetch_tasks(),
            self._fetch_github_notifs(),
            self._fetch_job_matches(),
            self._fetch_system_health(),
            self._fetch_tailscale_presence(),
            self._fetch_spotify_now_playing(),
            return_exceptions=True,
        )

        keys = [
            "calendar",
            "emails",
            "weather",
            "health",
            "finance",
            "tasks",
            "github",
            "jobs",
            "system_health",
            "tailscale_presence",
            "spotify",
        ]
        defaults: dict[str, Any] = {
            "calendar": {"error": "unavailable", "today": [], "tomorrow": []},
            "emails": {"error": "unavailable", "personal": [], "professional": []},
            "weather": {"error": "unavailable"},
            "health": {},
            "finance": {},
            "tasks": [],
            "github": [],
            "jobs": {"new_today": 0, "listings": []},
            "system_health": {"status": "unknown"},
            "tailscale_presence": {},
            "spotify": {},
        }

        payload: dict[str, Any] = {}
        unavailable_sources: list[str] = []
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                log.warning(
                    "briefing_fetch_failed",
                    source=key,
                    error=str(result),
                )
                payload[key] = defaults[key]
                unavailable_sources.append(key)
            else:
                payload[key] = result

        # Track which sources were unavailable so the narrative can mention it
        payload["_unavailable_sources"] = unavailable_sources
        if unavailable_sources:
            log.warning(
                "briefing_partial_data",
                unavailable=unavailable_sources,
                available_count=len(keys) - len(unavailable_sources),
            )

        return payload

    # ------------------------------------------------------------------
    # World-state cache reader (P3.5-07)
    # ------------------------------------------------------------------

    async def _read_world_state(self, sensor: str) -> dict[str, Any] | None:
        """Return the latest fresh ``world_state`` payload for ``sensor`` or ``None``.

        Fail-soft per HC-09: any exception logs and returns ``None`` so the
        caller can fall back to direct integration polling.
        """
        try:
            from db.session import get_db_session
            from orchestrator.world_state_cache import read_fresh_sensor

            async with get_db_session() as session:
                return await read_fresh_sensor(session, sensor)
        except Exception:  # noqa: BLE001 — HC-09
            log.warning("briefing_world_state_read_failed", sensor=sensor, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Individual fetch methods
    # ------------------------------------------------------------------

    async def _fetch_calendar(self) -> dict[str, Any]:
        """Fetch today's and tomorrow's events from iCloud CalDAV."""
        today = date.today()
        tomorrow = today + timedelta(days=1)

        today_events, tomorrow_events = await asyncio.gather(
            self._caldav.get_events(today),
            self._caldav.get_events(tomorrow),
        )

        log.info(
            "briefing_calendar_fetched",
            today_count=len(today_events),
            tomorrow_count=len(tomorrow_events),
        )

        return {
            "today": today_events,
            "tomorrow": tomorrow_events,
            "date": today.isoformat(),
        }

    async def _fetch_emails(self) -> dict[str, Any]:
        """Fetch unread emails from both Gmail accounts (last 12 hours)."""
        personal, professional = await asyncio.gather(
            self._gmail_personal.get_unread_emails(max_results=20, since_hours=12),
            self._gmail_professional.get_unread_emails(max_results=20, since_hours=12),
        )

        log.info(
            "briefing_emails_fetched",
            personal_count=len(personal),
            professional_count=len(professional),
        )

        return {
            "personal": personal,
            "professional": professional,
            "total_unread": len(personal) + len(professional),
        }

    async def _fetch_weather(self) -> dict[str, Any]:
        """Fetch weather. Prefer world_state cache; fall back to integration.

        World-state path (P3.5-07): the WeatherSensor publishes a flat
        payload every 15 min. We reshape it into the
        ``{current, forecast, summary}`` form the briefing payload expects.
        Forecast is not cached by the sensor, so it stays empty on cache hit.

        Fallback: when the cached row is missing or stale, hit
        WeatherClient directly (the original behaviour).
        """
        cached = await self._read_world_state("weather")
        if cached is not None:
            log.info(
                "briefing_weather_from_cache",
                temp_f=cached.get("temp_f"),
                location=cached.get("location"),
            )
            return _weather_payload_from_sensor(cached)

        try:
            current, forecast, summary = await asyncio.gather(
                self._weather.get_current(),
                self._weather.get_forecast(hours=12),
                self._weather.get_daily_summary(),
            )
        except Exception:
            log.warning("briefing_weather_fallback_failed", exc_info=True)
            raise

        log.info("briefing_weather_fetched", temp_f=current.get("temp_f"), source="integration")

        return {
            "current": current,
            "forecast": forecast,
            "summary": summary,
        }

    async def _fetch_health_data(self) -> dict[str, Any]:
        """Fetch health data. Prefer world_state cache; fall back to ``health_data`` table.

        World-state path (P3.5-07): HealthKitSensor publishes today's grouped
        readings every 5 min. Cache hit returns those reshaped into the legacy
        flat ``{date, <metric>: {value, unit}, ...}`` shape so downstream
        ``_build_health_section`` keeps working.

        Fallback: direct query against the ``health_data`` table for yesterday's
        rows, the original behaviour.
        """
        cached = await self._read_world_state("healthkit")
        if cached is not None:
            log.info(
                "briefing_health_from_cache",
                types_present=cached.get("types_present", []),
            )
            return _health_payload_from_sensor(cached)

        from db.models import HealthData
        from db.session import get_db_session

        yesterday = date.today() - timedelta(days=1)

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    HealthData.data_type,
                    HealthData.value,
                    HealthData.unit,
                )
                .where(HealthData.recorded_date == yesterday)
                .order_by(HealthData.data_type)
            )
            rows = result.all()

        if not rows:
            return {}

        data: dict[str, Any] = {"date": yesterday.isoformat()}
        for data_type, value, unit in rows:
            data[data_type] = {"value": float(value), "unit": unit}

        log.info("briefing_health_fetched", metrics=len(rows))
        return data

    async def _fetch_finance_data(self) -> dict[str, Any]:
        """Query yesterday's transactions, MTD spending, budgets, anomalies, and subscriptions."""
        from agents.anomaly_detector import AnomalyDetector
        from agents.subscription_tracker import SubscriptionTracker
        from db.models import Budget, Transaction
        from db.session import get_db_session

        today = date.today()
        yesterday = today - timedelta(days=1)
        month_start = today.replace(day=1)

        async with get_db_session() as session:
            # Yesterday's transactions
            txn_result = await session.execute(
                select(
                    Transaction.merchant_name,
                    Transaction.amount,
                    Transaction.category,
                )
                .where(
                    Transaction.transaction_date == yesterday,
                    Transaction.pending == False,  # noqa: E712
                )
                .order_by(Transaction.amount.desc())
            )
            txn_rows = txn_result.all()

            # Month-to-date total
            mtd_result = await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.transaction_date >= month_start,
                    Transaction.transaction_date <= yesterday,
                    Transaction.pending == False,  # noqa: E712
                )
            )
            mtd_total = mtd_result.scalar_one()

            # Budget status: active budgets + MTD spend per category
            budget_result = await session.execute(
                select(Budget).where(Budget.active.is_(True)).order_by(Budget.category)
            )
            budgets = list(budget_result.scalars().all())

            budget_alerts: list[dict[str, Any]] = []
            if budgets:
                cat_spend_result = await session.execute(
                    select(
                        Transaction.category,
                        func.coalesce(func.sum(Transaction.amount), 0).label("spent"),
                    )
                    .where(
                        Transaction.transaction_date >= month_start,
                        Transaction.transaction_date <= yesterday,
                        Transaction.pending == False,  # noqa: E712
                        Transaction.category.is_not(None),
                    )
                    .group_by(Transaction.category)
                )
                cat_spend = {row.category: float(row.spent) for row in cat_spend_result.all()}

                if today.month < 12:
                    next_month_start = today.replace(month=today.month + 1, day=1)
                else:
                    next_month_start = today.replace(year=today.year + 1, month=1, day=1)
                days_left = (next_month_start - today).days - 1  # 0 on last day of month

                for budget in budgets:
                    spent = cat_spend.get(budget.category, 0.0)
                    limit = float(budget.monthly_limit)
                    if limit > 0:
                        pct = spent / limit
                        if pct >= 0.80:
                            budget_alerts.append(
                                {
                                    "category": budget.category,
                                    "spent": spent,
                                    "limit": limit,
                                    "pct": round(pct * 100),
                                    "days_left": days_left,
                                }
                            )

            # Anomalies from yesterday
            detector = AnomalyDetector()
            anomalies_raw = await detector.scan_recent(session, lookback_days=1)
            anomalies = [
                {
                    "merchant": a.merchant,
                    "amount": a.amount,
                    "reason": a.reason,
                    "details": a.details,
                    "severity": a.severity,
                }
                for a in anomalies_raw
            ]

            # Subscription price changes (read-only — no mutation)
            # Query already-flagged recurring transactions and check for price changes
            tracker = SubscriptionTracker()
            merchant_col = func.coalesce(
                Transaction.merchant_name,
                Transaction.counterparty_name,
                "Unknown",
            )
            recurring_result = await session.execute(
                select(
                    merchant_col.label("merchant"),
                    Transaction.amount,
                    Transaction.transaction_date,
                )
                .where(
                    Transaction.is_recurring == True,  # noqa: E712
                    Transaction.pending == False,  # noqa: E712
                    Transaction.amount > 0,
                    Transaction.transaction_date >= today - timedelta(days=90),
                )
                .order_by(merchant_col, Transaction.transaction_date)
            )
            recurring_rows = recurring_result.all()

            recurring_merchant_txns: dict[str, list[tuple[float, date]]] = {}
            for merchant, amount, txn_date in recurring_rows:
                recurring_merchant_txns.setdefault(merchant, []).append((float(amount), txn_date))

            price_changes = tracker.check_price_changes(recurring_merchant_txns)
            subscription_alerts = [
                {
                    "merchant": pc.merchant,
                    "previous_avg": pc.previous_avg,
                    "current_amount": pc.current_amount,
                    "change_pct": pc.change_pct,
                }
                for pc in price_changes
            ]

        yesterday_txns = [
            {
                "merchant": row.merchant_name or "Unknown",
                "amount": float(row.amount),
                "category": row.category,
            }
            for row in txn_rows
        ]
        yesterday_total = sum(t["amount"] for t in yesterday_txns)

        log.info(
            "briefing_finance_fetched",
            yesterday_txns=len(yesterday_txns),
            yesterday_total=yesterday_total,
            budget_alerts=len(budget_alerts),
            anomalies=len(anomalies),
            subscription_alerts=len(subscription_alerts),
        )

        return {
            "date": yesterday.isoformat(),
            "yesterday_transactions": yesterday_txns,
            "yesterday_total": yesterday_total,
            "month_to_date": float(mtd_total),
            "budget_alerts": budget_alerts,
            "anomalies": anomalies,
            "subscription_alerts": subscription_alerts,
        }

    async def _fetch_tasks(self) -> list[dict[str, Any]]:
        """Fetch tasks due today from Notion."""
        if not self._notion_tasks_db:
            log.debug("briefing_tasks_skipped", reason="no notion_tasks_database_id configured")
            return []

        tasks = await self._notion.get_tasks_due(
            database_id=self._notion_tasks_db,
            due_date=date.today(),
        )

        log.info("briefing_tasks_fetched", count=len(tasks))
        return tasks

    async def _fetch_github_notifs(self) -> list[dict[str, Any]]:
        """Fetch GitHub notifications. Stub until GitHub integration is built."""
        # TODO: Implement when GitHubClient is available.
        return []

    async def _fetch_job_matches(self) -> dict[str, Any]:
        """Query new job listings scraped in the last 24 hours."""
        from db.models import JobListing
        from db.session import get_db_session

        since = datetime.now(UTC) - timedelta(hours=24)

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    JobListing.title,
                    JobListing.company,
                    JobListing.location,
                    JobListing.match_score,
                    JobListing.source,
                )
                .where(
                    JobListing.scraped_at >= since,
                    JobListing.status == JobStatus.NEW,
                )
                .order_by(JobListing.match_score.desc().nulls_last())
                .limit(10)
            )
            rows = result.all()

        listings = [
            {
                "title": row.title,
                "company": row.company,
                "location": row.location,
                "match_score": row.match_score,
                "source": row.source,
            }
            for row in rows
        ]

        log.info("briefing_jobs_fetched", new_today=len(listings))

        return {
            "new_today": len(listings),
            "listings": listings,
        }

    async def _fetch_tailscale_presence(self) -> dict[str, Any]:
        """Read Tailscale device presence from the world_state cache.

        No direct integration fallback — TailscaleSensor is the only consumer
        of the API and the briefing tolerates an empty dict gracefully.
        """
        cached = await self._read_world_state("tailscale_presence")
        if cached is None:
            log.debug("briefing_tailscale_no_cache")
            return {}
        log.info(
            "briefing_tailscale_from_cache",
            online_count=cached.get("online_count"),
            total_count=cached.get("total_count"),
        )
        return cached

    async def _fetch_spotify_now_playing(self) -> dict[str, Any]:
        """Read Spotify now-playing state from the world_state cache.

        No direct integration fallback — SpotifySensor is the only consumer
        of the spotipy client.
        """
        cached = await self._read_world_state("spotify")
        if cached is None:
            log.debug("briefing_spotify_no_cache")
            return {}
        log.info(
            "briefing_spotify_from_cache",
            playing=cached.get("playing"),
        )
        return cached

    async def _fetch_system_health(self) -> dict[str, Any]:
        """Query the latest system health check per target."""
        from db.models import SystemHealthLog
        from db.session import get_db_session

        async with get_db_session() as session:
            # Subquery: latest checked_at per target
            latest_sq = (
                select(
                    SystemHealthLog.target,
                    func.max(SystemHealthLog.checked_at).label("latest"),
                )
                .group_by(SystemHealthLog.target)
                .subquery()
            )

            result = await session.execute(
                select(
                    SystemHealthLog.target,
                    SystemHealthLog.status,
                    SystemHealthLog.response_time_ms,
                    SystemHealthLog.checked_at,
                )
                .join(
                    latest_sq,
                    (SystemHealthLog.target == latest_sq.c.target) & (SystemHealthLog.checked_at == latest_sq.c.latest),
                )
                .order_by(SystemHealthLog.target)
            )
            rows = result.all()

        if not rows:
            return {"status": "unknown", "targets": {}}

        targets: dict[str, Any] = {}
        all_green = True
        for row in rows:
            status_str = str(row.status)
            targets[row.target] = {
                "status": status_str,
                "response_time_ms": row.response_time_ms,
                "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            }
            if status_str != "green":
                all_green = False

        overall = "green" if all_green else "degraded"

        log.info(
            "briefing_system_health_fetched",
            targets=len(targets),
            overall=overall,
        )

        return {
            "status": overall,
            "targets": targets,
        }


# ---------------------------------------------------------------------------
# Module-level helpers for sensor → briefing payload translation (P3.5-07)
# ---------------------------------------------------------------------------


def _weather_payload_from_sensor(cached: dict[str, Any]) -> dict[str, Any]:
    """Reshape a flat WeatherSensor payload into ``{current, forecast, summary}``.

    The sensor caches current conditions + a daily summary; it does NOT cache
    the hourly forecast, so ``forecast`` stays empty on a cache hit. Keeps
    backwards compatibility with ``_build_payload`` which reads
    ``weather_raw["current"]`` and ``weather_raw["summary"]``.
    """
    current = {
        "temp_c": cached.get("temp_c"),
        "temp_f": cached.get("temp_f"),
        "conditions": cached.get("conditions"),
        "humidity": cached.get("humidity"),
        "wind_mph": cached.get("wind_mph"),
        "icon": cached.get("icon"),
        "location": cached.get("location"),
    }
    summary = {
        "summary": cached.get("daily_summary", ""),
        "high": cached.get("high"),
        "low": cached.get("low"),
        "needs_umbrella": cached.get("needs_umbrella", False),
        "suggestion": cached.get("suggestion", ""),
    }
    return {
        "current": current,
        "forecast": [],
        "summary": summary,
    }


def _health_payload_from_sensor(cached: dict[str, Any]) -> dict[str, Any]:
    """Flatten a HealthKitSensor payload to the legacy briefing shape.

    Sensor shape::

        {"date": "2026-04-26", "readings": {"steps": {"value": 8500.0, ...}}, ...}

    Briefing shape (consumed by ``_build_health_section``)::

        {"date": "2026-04-26", "steps": {"value": 8500.0, "unit": "count"}, ...}
    """
    readings = cached.get("readings", {}) or {}
    out: dict[str, Any] = {}
    if "date" in cached:
        out["date"] = cached["date"]
    for metric, info in readings.items():
        if not isinstance(info, dict):
            continue
        out[metric] = {
            "value": info.get("value"),
            "unit": info.get("unit"),
        }
    return out


# ---------------------------------------------------------------------------
# Module-level helpers for payload building
# ---------------------------------------------------------------------------


def _build_schedule(today_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert calendar events into the briefing schedule format."""
    schedule: list[dict[str, Any]] = []
    for event in today_events:
        schedule.append(
            {
                "time": event.get("start", event.get("time", "")),
                "event": event.get("title", event.get("summary", "Untitled")),
                "location": event.get("location", ""),
            }
        )
    return schedule


def _compute_leave_home_by(today_events: list[dict[str, Any]]) -> str | None:
    """Compute suggested leave-home time from the first event with a start time.

    Returns a time string like ``"7:35 AM"`` or ``None`` if no timed events.
    Assumes ~25 minutes to prepare and commute to a local event.
    """
    for event in today_events:
        start = event.get("start", "")
        if not start:
            continue
        try:
            # Handle HH:MM format
            if "T" in str(start):
                dt = datetime.fromisoformat(str(start))
            else:
                parts = str(start).split(":")
                dt = datetime.now().replace(
                    hour=int(parts[0]),
                    minute=int(parts[1]) if len(parts) > 1 else 0,
                )
            leave = dt - timedelta(minutes=25)
            return leave.strftime("%-I:%M %p")
        except (ValueError, IndexError):
            continue
    return None


def _build_commute_note(today_events: list[dict[str, Any]]) -> str | None:
    """Build a commute note if any event has an out-of-town location."""
    for event in today_events:
        location = event.get("location", "")
        if location and any(city in location.lower() for city in ["cleveland", "columbus", "akron", "canton"]):
            return f"Allow extra time — {event.get('title', 'event')} at {location}."
    return None


def _build_email_digest(
    emails_raw: dict[str, Any],
    classifications: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the email_digest section from raw emails and classifications."""
    urgent: list[dict[str, Any]] = []
    actionable: list[dict[str, Any]] = []
    informational_count = 0
    noise_count = 0

    for c in classifications:
        tier = c.get("classified_tier", "")
        entry = {
            "from": c.get("from_name") or c.get("from_address", "Unknown"),
            "subject": c.get("subject", "(no subject)"),
            "snippet": c.get("snippet", ""),
        }
        if tier == EmailTier.URGENT:
            urgent.append(entry)
        elif tier == EmailTier.ACTIONABLE:
            actionable.append(entry)
        elif tier == EmailTier.INFORMATIONAL:
            informational_count += 1
        elif tier == EmailTier.NOISE:
            noise_count += 1

    # If no classifications available, use raw email counts
    if not classifications:
        total = emails_raw.get("total_unread", 0)
        informational_count = total

    return {
        "urgent": urgent,
        "actionable": actionable,
        "informational_count": informational_count,
        "noise_filtered_count": noise_count,
    }


def _summarize_system_health(sys_health: dict[str, Any]) -> str:
    """Build a human-readable system health summary."""
    status = sys_health.get("status", "unknown")
    targets = sys_health.get("targets", {})

    if status == "green":
        return "All services operational."
    if status == "unknown":
        return "System health data unavailable."

    degraded = [name for name, info in targets.items() if info.get("status") != "green"]
    if degraded:
        return f"Degraded: {', '.join(degraded)}."
    return f"System status: {status}."


def _build_health_section(health_raw: dict[str, Any]) -> dict[str, Any]:
    """Build the health section from raw health data."""
    if not health_raw:
        return {"note": "No health data available."}

    sleep_data = health_raw.get("sleep", {})
    steps_data = health_raw.get("steps", {})
    workout_data = health_raw.get("last_workout", {})

    sleep_val = sleep_data.get("value") if isinstance(sleep_data, dict) else None
    steps_val = steps_data.get("value") if isinstance(steps_data, dict) else None

    result: dict[str, Any] = {}

    if sleep_val is not None:
        quality = "good" if sleep_val >= 7 else "below target"
        result["sleep"] = f"{sleep_val} hours ({quality})"

    if steps_val is not None:
        result["steps_yesterday"] = int(steps_val)

    if workout_data:
        workout_val = workout_data.get("value") if isinstance(workout_data, dict) else None
        if workout_val is not None:
            result["last_workout"] = f"{int(workout_val)} days ago"

    # Build a summary note
    notes: list[str] = []
    if sleep_val is not None and sleep_val >= 7:
        notes.append("You slept well.")
    if sleep_val is not None and sleep_val < 6:
        notes.append("Short sleep — take it easy today.")
    result["note"] = " ".join(notes) if notes else ""

    return result


def _build_finance_section(finance_raw: dict[str, Any]) -> dict[str, Any]:
    """Build the finance section from raw finance data."""
    if not finance_raw:
        return {"note": "No finance data available."}

    txns = finance_raw.get("yesterday_transactions", [])
    yesterday_total = finance_raw.get("yesterday_total", 0)
    mtd = finance_raw.get("month_to_date", 0)

    # Budget alerts (>80% used)
    budget_alerts: list[str] = []
    for ba in finance_raw.get("budget_alerts", []):
        cat = ba["category"].capitalize()
        budget_alerts.append(
            f"{cat} at {ba['pct']}% (${ba['spent']:.0f}/${ba['limit']:.0f}) — {ba['days_left']} days left"
        )

    # Subscription price change alerts
    subscription_alerts: list[str] = []
    for sa in finance_raw.get("subscription_alerts", []):
        subscription_alerts.append(
            f"{sa['merchant']} increased from ${sa['previous_avg']:.2f} "
            f"to ${sa['current_amount']:.2f} (+{sa['change_pct']:.0f}%)"
        )

    # Anomalies from yesterday
    anomalies: list[str] = [a["details"] for a in finance_raw.get("anomalies", [])]

    return {
        "yesterday_spending": f"${yesterday_total:.2f} ({len(txns)} transaction{'s' if len(txns) != 1 else ''})",
        "month_to_date": f"${mtd:.2f}",
        "budget_alerts": budget_alerts,
        "subscription_alerts": subscription_alerts,
        "anomalies": anomalies,
        "note": "",
    }


def _build_proactive_suggestions(
    weather: dict[str, Any],
    health_raw: dict[str, Any],
    finance_raw: dict[str, Any],
    today_events: list[dict[str, Any]],
) -> list[str]:
    """Generate proactive suggestions based on aggregated data."""
    suggestions: list[str] = []

    # Weather-based suggestions
    if weather.get("needs_umbrella"):
        suggestions.append("Don't forget your umbrella today.")
    weather_suggestion = weather.get("suggestion", "")
    if weather_suggestion and "umbrella" not in weather_suggestion.lower():
        suggestions.append(weather_suggestion)

    # Health-based suggestions
    workout_data = health_raw.get("last_workout", {})
    if isinstance(workout_data, dict):
        days_since = workout_data.get("value")
        if days_since is not None and days_since >= 2:
            # Check if there's gym/workout on today's schedule
            has_gym = any(
                "gym" in str(e.get("title", "")).lower() or "workout" in str(e.get("title", "")).lower()
                for e in today_events
            )
            if not has_gym:
                suggestions.append(f"You haven't worked out in {int(days_since)} days — consider some exercise today.")

    return suggestions


def _filter_payload_for_prompt(
    payload: dict[str, Any],
    section_names: list[str],
) -> dict[str, Any]:
    """Filter the payload to only include data for enabled sections.

    Reduces prompt size by excluding sections the user has disabled.
    """
    # Map section names to payload keys
    section_to_keys: dict[str, list[str]] = {
        "weather": ["weather"],
        "schedule": ["schedule", "leave_home_by", "commute_note"],
        "email_digest": ["email_digest"],
        "tasks_due": ["tasks_due_today"],
        "job_matches": ["job_matches"],
        "system_health": ["system_health"],
        "health_summary": ["health"],
        "finance_summary": ["finance"],
        "proactive_suggestions": ["proactive_suggestions"],
    }

    filtered: dict[str, Any] = {"greeting": payload.get("greeting", ""), "date": payload.get("date", "")}
    for section_name in section_names:
        for key in section_to_keys.get(section_name, []):
            if key in payload:
                filtered[key] = payload[key]

    return filtered
