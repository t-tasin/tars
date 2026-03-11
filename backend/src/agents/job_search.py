"""Job Search Agent — three-tier filtering pipeline.

Stage 1: Collection (local — zero AI tokens) — scrape all configured job boards.
Stage 2: Initial Screen (Gemini Flash — bulk batches) — quick keyword/criteria scoring.
Stage 3: Fit Evaluation (Gemini Pro — per listing) — detailed match analysis.
Stage 4: Deep Review (Claude — on-demand) — only when user taps a specific job.

Runs as a daily cron job at 2:00 AM and responds to /jobs for the latest digest.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select, func

from agents.base import AgentContext, AgentResult, BaseAgent
from models.gemini_client import GeminiClient
from shared.constants import JobStatus, ModelName

log = structlog.get_logger()

_FLASH_BATCH_SIZE = 10
_FLASH_SCORE_CUTOFF = 40
_PRO_TOP_N = 20

_FLASH_SYSTEM_PROMPT = (
    "You are a job-matching assistant. You evaluate job listings against a "
    "candidate profile and return structured JSON scores. Be strict — only "
    "jobs that genuinely match the profile should score above 50."
)

_PRO_SYSTEM_PROMPT = (
    "You are a senior career advisor evaluating how well a specific candidate "
    "fits a job opening. Score based on:\n"
    "- Skill overlap: candidate's skills vs required skills (highest weight)\n"
    "- Experience match: years, domain relevance (full-stack, infra, AI systems)\n"
    "- Research interest alignment (multi-agent systems, AI coordination)\n"
    "- Location/remote compatibility\n"
    "- Seniority match\n\n"
    "Return JSON with:\n"
    "- match_score (0-100): how well the CANDIDATE fits this role\n"
    "- match_reasons (list of 2-4 short strings)\n"
    "- concerns (list of 0-3 short strings)\n"
    "- fit_analysis (string): 1-2 sentence analysis like 'Strong match: 4/5 "
    "required skills. Gap: no Rust experience. Your AtlasDesk work directly "
    "relevant to their infrastructure needs.'\n"
    "- tags (list of strings): applicable tags from [remote, ai-ml, backend, "
    "fullstack, startup, faang, infrastructure, research]"
)


class JobSearchAgent(BaseAgent):
    """Job search with three-tier filtering: collect → flash screen → pro evaluate."""

    agent_type = "job_search"

    def __init__(self) -> None:
        from config import get_settings

        settings = get_settings()
        self._serpapi_key = settings.serpapi_key if hasattr(settings, "serpapi_key") else ""

    async def execute(self, context: AgentContext) -> AgentResult:
        """Run the job search pipeline or show existing matches."""
        action = context.config.get("intent_action") or _parse_action(context.user_message)

        if action == "full_scan":
            return await self._run_full_scan(context)
        else:
            return await self._show_digest(context)

    # ------------------------------------------------------------------
    # Digest — show latest matches
    # ------------------------------------------------------------------

    async def _show_digest(self, context: AgentContext) -> AgentResult:
        """Return the latest job matches from the DB."""
        from db.models import JobListing
        from db.session import get_db_session

        since = datetime.now(timezone.utc) - timedelta(hours=48)

        async with get_db_session() as session:
            result = await session.execute(
                select(
                    JobListing.id,
                    JobListing.title,
                    JobListing.company,
                    JobListing.location,
                    JobListing.match_score,
                    JobListing.match_reasons,
                    JobListing.concerns,
                    JobListing.source,
                    JobListing.url,
                    JobListing.salary_range,
                    JobListing.status,
                    JobListing.scraped_at,
                )
                .where(
                    JobListing.scraped_at >= since,
                    JobListing.pro_evaluated == True,  # noqa: E712
                )
                .order_by(JobListing.match_score.desc().nulls_last())
                .limit(10)
            )
            rows = result.all()

        if not rows:
            return AgentResult(
                success=True,
                text="No new job matches in the last 48 hours. The next scan runs at 2:00 AM.",
                content_type="text",
            )

        cards = []
        for row in rows:
            cards.append({
                "id": str(row.id),
                "title": row.title,
                "company": row.company,
                "location": row.location or "",
                "match_score": row.match_score,
                "match_reasons": row.match_reasons or [],
                "concerns": row.concerns or [],
                "source": row.source,
                "url": row.url,
                "salary_range": row.salary_range or "",
                "status": str(row.status),
            })

        top = cards[0]
        text = (
            f"Found {len(cards)} matches in the last 48 hours. "
            f"Top match: {top['title']} at {top['company']} "
            f"({top['match_score']}% match)."
        )

        return AgentResult(
            success=True,
            text=text,
            content_type="card",
            cards=cards,
            data={"total_matches": len(cards), "top_match": top},
        )

    # ------------------------------------------------------------------
    # Full scan — three-tier pipeline
    # ------------------------------------------------------------------

    async def _run_full_scan(self, context: AgentContext) -> AgentResult:
        """Three-tier filtering pipeline: collect → flash screen → pro evaluate."""
        job_config = context.config.get("job_search", {})
        gemini: GeminiClient | None = context.config.get("gemini_client")

        # Stage 1: Collection (zero AI tokens)
        raw_listings = await self._collect_from_all_sources(job_config)
        log.info("job_scan_collected", raw_count=len(raw_listings))

        if not raw_listings:
            return AgentResult(
                success=True,
                text="Job scan complete — no new listings found from any source.",
                data={"new_matches": 0},
            )

        # Stage 2: Deduplicate against existing listings
        raw_listings = await self._deduplicate(raw_listings)
        log.info("job_scan_deduplicated", remaining=len(raw_listings))

        if not raw_listings:
            return AgentResult(
                success=True,
                text="Job scan complete — all listings already seen.",
                data={"new_matches": 0},
            )

        # Stage 3: Initial Screen (Gemini Flash — batch)
        screened = await self._flash_screen(raw_listings, job_config, gemini)
        log.info("job_scan_flash_screened", passed=len(screened), total=len(raw_listings))

        if not screened:
            # Store rejects too so we don't re-process them
            await self._store_results(raw_listings, flash_only=True)
            return AgentResult(
                success=True,
                text="Job scan complete — no listings passed initial screening.",
                data={"new_matches": 0, "screened_out": len(raw_listings)},
            )

        # Stage 4: Fit Evaluation (Gemini Pro — top candidates)
        evaluated = await self._pro_evaluate(screened, job_config, gemini)
        log.info("job_scan_pro_evaluated", evaluated=len(evaluated))

        # Store all results
        all_listings = [l for l in raw_listings if l not in screened] + evaluated
        await self._store_results(evaluated, flash_only=False)
        # Store flash-rejected separately
        rejects = [l for l in raw_listings if l.get("_flash_score", 0) < _FLASH_SCORE_CUTOFF]
        if rejects:
            await self._store_results(rejects, flash_only=True)

        # Build response — sorted by fit score (match_score) descending
        top_matches = sorted(evaluated, key=lambda x: x.get("match_score", 0), reverse=True)[:15]

        # Send Telegram digest
        await self._send_digest(top_matches, {
            "total_scraped": len(raw_listings),
            "sources": "5 sources",
        })

        if top_matches:
            top = top_matches[0]
            text = (
                f"Found {len(evaluated)} matches from {len(raw_listings)} listings. "
                f"Top match: {top['title']} at {top['company']} "
                f"({top.get('match_score', '?')}% match). "
                f"Digest sent to Telegram."
            )
        else:
            text = f"Scanned {len(raw_listings)} listings — no strong matches."

        return AgentResult(
            success=True,
            text=text,
            data={
                "new_matches": len(evaluated),
                "total_scraped": len(raw_listings),
                "top_matches": top_matches[:5],
            },
        )

    async def _send_digest(self, jobs: list[dict], scan_stats: dict) -> None:
        """Send the daily job digest to Telegram (best effort)."""
        try:
            from integrations.notification_service import get_notification_service
            from integrations.telegram_job_handlers import send_job_digest

            notifier = get_notification_service()
            await send_job_digest(notifier.telegram, jobs, scan_stats)
        except Exception:
            log.warning("job_digest_send_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Stage 1: Collection
    # ------------------------------------------------------------------

    async def _collect_from_all_sources(self, job_config: dict) -> list[dict]:
        """Collect raw listings from all configured job board adapters. Zero AI tokens."""
        from integrations.job_boards import (
            GoogleJobsAdapter,
            GreenhouseAdapter,
            JobSpyScraper,
            LinkedInAdapter,
            YCWaaaSAdapter,
        )

        criteria = {
            "query": job_config.get("query", "software engineer"),
            "location": job_config.get("location", ""),
        }

        adapters = [
            LinkedInAdapter(serpapi_key=self._serpapi_key),
            GoogleJobsAdapter(serpapi_key=self._serpapi_key),
            JobSpyScraper(),
            GreenhouseAdapter(),
            YCWaaaSAdapter(),
        ]

        # Fetch from all sources in parallel
        tasks = [self._safe_search(adapter, criteria) for adapter in adapters]
        results = await asyncio.gather(*tasks)

        # Flatten and normalize
        all_listings: list[dict] = []
        for adapter, raw_jobs in zip(adapters, results):
            for raw_job in raw_jobs:
                normalized = adapter.normalize(raw_job)
                all_listings.append(normalized)

        # Cross-source dedup by title + company (fuzzy)
        all_listings = _deduplicate_cross_source(all_listings)

        return all_listings

    async def _safe_search(self, adapter: Any, criteria: dict) -> list[dict]:
        """Search a single adapter, catching errors gracefully (HC-09)."""
        try:
            return await adapter.search(criteria)
        except Exception:
            log.error("job_board_search_failed", source=adapter.source_name, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    async def _deduplicate(self, listings: list[dict]) -> list[dict]:
        """Filter out listings we've already seen (by source + external_id)."""
        from db.models import JobListing
        from db.session import get_db_session

        # Build lookup set of (source, external_id) pairs
        pairs = [(l["source"], l["external_id"]) for l in listings if l.get("external_id")]
        if not pairs:
            return listings

        async with get_db_session() as session:
            result = await session.execute(
                select(JobListing.source, JobListing.external_id)
                .where(
                    JobListing.external_id.in_([p[1] for p in pairs])
                )
            )
            existing = {(row.source, row.external_id) for row in result.all()}

        return [
            l for l in listings
            if (l["source"], l.get("external_id")) not in existing
        ]

    # ------------------------------------------------------------------
    # Stage 2: Flash Screen (Gemini Flash — batch)
    # ------------------------------------------------------------------

    async def _flash_screen(
        self,
        listings: list[dict],
        config: dict,
        gemini: GeminiClient | None,
    ) -> list[dict]:
        """Gemini Flash: quick keyword/criteria screening in batches."""
        if gemini is None:
            log.warning("job_flash_screen_skipped", reason="no gemini client")
            # Pass everything through if no AI available (HC-09)
            return listings

        profile_summary = config.get("profile_summary", "Software engineer seeking full-time roles")

        # Process in batches
        all_scored: list[dict] = []
        for i in range(0, len(listings), _FLASH_BATCH_SIZE):
            batch = listings[i : i + _FLASH_BATCH_SIZE]
            scored = await self._flash_screen_batch(batch, profile_summary, gemini)
            all_scored.extend(scored)

        # Filter by cutoff
        passed = [l for l in all_scored if l.get("_flash_score", 0) >= _FLASH_SCORE_CUTOFF]
        return passed

    async def _flash_screen_batch(
        self,
        batch: list[dict],
        profile_summary: str,
        gemini: GeminiClient,
    ) -> list[dict]:
        """Screen a single batch of listings with Gemini Flash."""
        # Build compact representations for the prompt
        compact = []
        for idx, listing in enumerate(batch):
            compact.append({
                "idx": idx,
                "title": listing.get("title", ""),
                "company": listing.get("company", ""),
                "location": listing.get("location", ""),
                "salary": listing.get("salary_range", ""),
                "snippet": (listing.get("description", "") or "")[:300],
            })

        prompt = (
            f"Candidate profile: {profile_summary}\n\n"
            f"Score each job listing 0-100 based on fit with this profile.\n"
            f"Return a JSON array of objects with 'idx' and 'score' keys.\n"
            f"Only include jobs scoring 20+.\n\n"
            f"Listings:\n{json.dumps(compact, indent=2)}"
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.0-flash",
                system_instruction=_FLASH_SYSTEM_PROMPT,
                temperature=0.1,
                max_output_tokens=1024,
                response_format="json",
            )

            scores = json.loads(response.text)
            score_map = {item["idx"]: item["score"] for item in scores if isinstance(item, dict)}

            for idx, listing in enumerate(batch):
                listing["_flash_score"] = score_map.get(idx, 0)

            log.info(
                "job_flash_batch",
                batch_size=len(batch),
                passed=sum(1 for s in score_map.values() if s >= _FLASH_SCORE_CUTOFF),
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
            )

        except (json.JSONDecodeError, KeyError, TypeError):
            log.error("job_flash_parse_failed", exc_info=True)
            # On parse failure, let everything through (conservative)
            for listing in batch:
                listing["_flash_score"] = 50
        except Exception:
            log.error("job_flash_call_failed", exc_info=True)
            for listing in batch:
                listing["_flash_score"] = 50

        return batch

    # ------------------------------------------------------------------
    # Stage 3: Pro Evaluate (Gemini Pro — per listing)
    # ------------------------------------------------------------------

    async def _pro_evaluate(
        self,
        screened: list[dict],
        config: dict,
        gemini: GeminiClient | None,
    ) -> list[dict]:
        """Gemini Pro: detailed fit evaluation for screened candidates."""
        if gemini is None:
            log.warning("job_pro_evaluate_skipped", reason="no gemini client")
            return screened

        # Only evaluate top N by flash score
        candidates = sorted(screened, key=lambda x: x.get("_flash_score", 0), reverse=True)[:_PRO_TOP_N]

        profile = config.get("full_profile", config.get("profile_summary", "Software engineer"))

        # Evaluate in parallel (bounded concurrency)
        sem = asyncio.Semaphore(3)

        async def eval_one(listing: dict) -> dict:
            async with sem:
                return await self._pro_evaluate_one(listing, profile, gemini)

        results = await asyncio.gather(
            *(eval_one(l) for l in candidates),
            return_exceptions=True,
        )

        evaluated = []
        for listing, result in zip(candidates, results):
            if isinstance(result, Exception):
                log.error(
                    "job_pro_eval_failed",
                    title=listing.get("title"),
                    error=str(result),
                )
                # Keep with flash score as fallback
                listing["match_score"] = listing.get("_flash_score", 0)
                listing["match_reasons"] = []
                listing["concerns"] = ["Pro evaluation failed"]
                listing["pro_evaluated"] = True
                evaluated.append(listing)
            else:
                evaluated.append(result)

        return evaluated

    async def _pro_evaluate_one(
        self,
        listing: dict,
        profile: str,
        gemini: GeminiClient,
    ) -> dict:
        """Evaluate a single listing with Gemini Pro."""
        prompt = (
            f"Candidate profile:\n{profile}\n\n"
            f"Job listing:\n"
            f"Title: {listing.get('title', '')}\n"
            f"Company: {listing.get('company', '')}\n"
            f"Location: {listing.get('location', '')}\n"
            f"Salary: {listing.get('salary_range', '')}\n"
            f"Description:\n{(listing.get('description', '') or '')[:2000]}\n\n"
            f"Evaluate how well the CANDIDATE fits this specific role. "
            f"Score based on skill overlap, experience relevance, research "
            f"interest alignment, location compatibility, and seniority match.\n\n"
            f"Return JSON with:\n"
            f"- match_score (0-100): candidate fit score\n"
            f"- match_reasons (list of 2-4 short strings)\n"
            f"- concerns (list of 0-3 short strings)\n"
            f"- fit_analysis (string): 1-2 sentence fit summary\n"
            f"- tags (list of strings from: remote, ai-ml, backend, fullstack, "
            f"startup, faang, infrastructure, research)\n"
        )

        try:
            response = await gemini.generate(
                prompt=prompt,
                model="gemini-2.0-pro",
                system_instruction=_PRO_SYSTEM_PROMPT,
                temperature=0.2,
                max_output_tokens=512,
                response_format="json",
            )

            evaluation = json.loads(response.text)
            listing["match_score"] = int(evaluation.get("match_score", 0))
            listing["match_reasons"] = evaluation.get("match_reasons", [])
            listing["concerns"] = evaluation.get("concerns", [])
            listing["fit_analysis"] = evaluation.get("fit_analysis", "")
            listing["tags"] = evaluation.get("tags", [])
            listing["pro_evaluated"] = True

            log.info(
                "job_pro_evaluated",
                title=listing.get("title"),
                company=listing.get("company"),
                score=listing["match_score"],
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
            )

        except (json.JSONDecodeError, KeyError, TypeError):
            log.error("job_pro_parse_failed", title=listing.get("title"), exc_info=True)
            listing["match_score"] = listing.get("_flash_score", 0)
            listing["match_reasons"] = []
            listing["concerns"] = ["Evaluation parse error"]
            listing["pro_evaluated"] = True
        except Exception:
            log.error("job_pro_call_failed", title=listing.get("title"), exc_info=True)
            listing["match_score"] = listing.get("_flash_score", 0)
            listing["match_reasons"] = []
            listing["concerns"] = ["Evaluation failed"]
            listing["pro_evaluated"] = True

        return listing

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def _store_results(self, listings: list[dict], *, flash_only: bool = False) -> None:
        """Store evaluated listings in the job_listings table and sync to Notion."""
        from db.models import JobListing
        from db.session import get_db_session
        from integrations.job_applicator import classify_apply_method

        if not listings:
            return

        stored_jobs: list[tuple[JobListing, dict]] = []

        async with get_db_session() as session:
            for listing in listings:
                apply_method = classify_apply_method(listing) if not flash_only else None

                job = JobListing(
                    id=uuid.uuid4(),
                    source=listing.get("source", "unknown"),
                    external_id=listing.get("external_id") or None,
                    title=listing.get("title", "Untitled"),
                    company=listing.get("company", "Unknown"),
                    location=listing.get("location"),
                    salary_range=listing.get("salary_range"),
                    description=listing.get("description"),
                    url=listing.get("url", ""),
                    status=JobStatus.NEW,
                    match_score=listing.get("match_score") if not flash_only else listing.get("_flash_score"),
                    match_reasons=listing.get("match_reasons", []),
                    concerns=listing.get("concerns", []),
                    fit_analysis=listing.get("fit_analysis"),
                    apply_method=apply_method,
                    flash_screen=True,
                    pro_evaluated=not flash_only,
                    scraped_at=listing.get("scraped_at", datetime.now(timezone.utc)),
                )
                session.add(job)
                if not flash_only:
                    listing["id"] = str(job.id)
                    listing["apply_method"] = apply_method
                    stored_jobs.append((job, listing))

        # Sync pro-evaluated jobs to Notion (best effort)
        if stored_jobs:
            await self._sync_to_notion(stored_jobs)

        log.info(
            "job_listings_stored",
            count=len(listings),
            flash_only=flash_only,
        )

    async def _sync_to_notion(self, jobs: list[tuple[Any, dict]]) -> None:
        """Sync stored jobs to Notion database (best effort)."""
        try:
            from config import get_settings
            from db.repositories.config import ConfigRepository
            from db.session import get_db_session
            from integrations.notion_client import NotionClient

            settings = get_settings()
            if not settings.notion_token:
                return

            notion = NotionClient(token=settings.notion_token)

            # Get or create Notion database
            async with get_db_session() as session:
                config_repo = ConfigRepository(session)
                db_id = await config_repo.get("job_search", "notion_job_database_id")
                parent_page_id = await config_repo.get("job_search", "notion_parent_page_id")

                if not db_id and parent_page_id:
                    db_id = await notion.ensure_job_tracking_database(parent_page_id)
                    await config_repo.set("job_search", "notion_job_database_id", db_id)

            if not db_id:
                log.debug("notion_sync_skipped", reason="no database configured")
                await notion.close()
                return

            for job_model, listing in jobs:
                try:
                    page_id = await notion.sync_job_to_notion(listing, db_id)
                    # Update the DB record with the Notion page ID
                    async with get_db_session() as session:
                        from sqlalchemy import update
                        from db.models import JobListing
                        await session.execute(
                            update(JobListing)
                            .where(JobListing.id == job_model.id)
                            .values(notion_page_id=page_id)
                        )
                except Exception:
                    log.warning(
                        "notion_job_sync_failed",
                        title=listing.get("title"),
                        exc_info=True,
                    )

            await notion.close()
            log.info("notion_jobs_synced", count=len(jobs))

        except Exception:
            log.warning("notion_sync_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_action(message: str) -> str:
    """Parse the action from a user message."""
    text = message.strip().lower()
    if text in ("/jobs", "jobs", "job digest", "show jobs"):
        return "show_digest"
    if "scan" in text or "search" in text or "find jobs" in text:
        return "full_scan"
    return "show_digest"


def _deduplicate_cross_source(listings: list[dict]) -> list[dict]:
    """Remove cross-source duplicates by normalizing title + company.

    When the same job appears from multiple sources, keep the first occurrence
    (order is preserved from the adapter list).
    """
    seen: set[str] = set()
    unique: list[dict] = []

    for listing in listings:
        key = _dedup_key(listing.get("title", ""), listing.get("company", ""))
        if key not in seen:
            seen.add(key)
            unique.append(listing)

    removed = len(listings) - len(unique)
    if removed:
        log.info("job_cross_source_dedup", removed=removed, remaining=len(unique))

    return unique


def _dedup_key(title: str, company: str) -> str:
    """Build a normalized dedup key from title and company.

    Lowercases, strips common suffixes (Inc, LLC, etc.), and removes
    non-alphanumeric chars to catch near-duplicates across sources.
    """
    import re

    def _clean(s: str) -> str:
        s = s.lower().strip()
        # Remove common corporate suffixes
        s = re.sub(r"\b(inc|llc|ltd|corp|co|corporation|company)\b\.?", "", s)
        # Keep only alphanumeric and spaces, then collapse whitespace
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    return f"{_clean(title)}|{_clean(company)}"
