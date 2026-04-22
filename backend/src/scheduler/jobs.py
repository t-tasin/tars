"""Scheduled job definitions for T.A.R.S.

All periodic jobs are defined here and wired into a single APScheduler
instance via :func:`create_scheduler`.  Each job:

1. Logs its start.
2. Calls the appropriate agent through the orchestrator pipeline (so
   intent classification, model routing, usage tracking, approval flow,
   and audit logging all happen automatically).
3. Catches all exceptions — a failing job never crashes the scheduler.
4. Logs completion with wall-clock duration.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from orchestrator.engine import Orchestrator

log = structlog.get_logger("scheduler")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_scheduler(orchestrator: Orchestrator) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance with all cron jobs."""

    scheduler = AsyncIOScheduler()

    # Teller daily sync: 5:00 AM daily (before briefing at 5:50 AM)
    scheduler.add_job(
        teller_sync_job,
        CronTrigger(hour=5, minute=0),
        id="teller_sync",
        name="Daily Teller Transaction Sync",
        kwargs={"orchestrator": orchestrator},
    )

    # Morning briefing: 5:50 AM daily
    scheduler.add_job(
        briefing_job,
        CronTrigger(hour=5, minute=50),
        id="morning_briefing",
        name="Morning Briefing",
        kwargs={"orchestrator": orchestrator},
    )

    # Email polling: every 5 minutes
    scheduler.add_job(
        email_poll_job,
        IntervalTrigger(minutes=5),
        id="email_poll",
        name="Email Poll",
        kwargs={"orchestrator": orchestrator},
    )

    # End-of-day summary: 10:30 PM daily
    scheduler.add_job(
        eod_summary_job,
        CronTrigger(hour=22, minute=30),
        id="eod_summary",
        name="End-of-Day Summary",
        kwargs={"orchestrator": orchestrator},
    )

    # System health check: every 5 minutes
    scheduler.add_job(
        health_check_job,
        IntervalTrigger(minutes=5),
        id="health_check",
        name="Health Check",
        kwargs={"orchestrator": orchestrator},
    )

    # Approval expiry: every 10 minutes
    scheduler.add_job(
        expire_approvals_job,
        IntervalTrigger(minutes=10),
        id="expire_approvals",
        name="Expire Stale Approvals",
        kwargs={"orchestrator": orchestrator},
    )

    # Job search scan: 2:00 AM daily
    scheduler.add_job(
        job_search_scan,
        CronTrigger(hour=2, minute=0),
        id="job_search_scan",
        name="Daily Job Search Scan",
        kwargs={"orchestrator": orchestrator},
    )

    # AI usage report: 11:00 PM daily
    scheduler.add_job(
        usage_report_job,
        CronTrigger(hour=23, minute=0),
        id="usage_report",
        name="Daily AI Usage Report",
        kwargs={"orchestrator": orchestrator},
    )

    # Database backup: 3:00 AM daily
    scheduler.add_job(
        backup_job,
        CronTrigger(hour=3, minute=0),
        id="daily_backup",
        name="Daily Database Backup",
        kwargs={"orchestrator": orchestrator},
    )

    # Workout: create daily session at 5:30 AM
    scheduler.add_job(
        create_daily_workout_session,
        CronTrigger(hour=5, minute=30),
        id="create_daily_workout",
        name="Create Daily Workout Session",
        kwargs={"orchestrator": orchestrator},
    )

    # Workout: reminder polling every 5 minutes
    scheduler.add_job(
        workout_reminder_poll,
        IntervalTrigger(minutes=5),
        id="workout_reminder_poll",
        name="Workout Reminder Poll",
        kwargs={"orchestrator": orchestrator},
    )

    # Monthly budget audit: 1st of each month at 6:00 AM
    scheduler.add_job(
        monthly_budget_audit,
        CronTrigger(day=1, hour=6, minute=0),
        id="monthly_budget_audit",
        name="Monthly Budget Category Audit",
        kwargs={"orchestrator": orchestrator},
    )

    log.info(
        "scheduler_jobs_registered",
        jobs=[
            "teller_sync@05:00",
            "morning_briefing@05:50",
            "email_poll@every_5m",
            "eod_summary@22:30",
            "health_check@every_5m",
            "expire_approvals@every_10m",
            "job_search_scan@02:00",
            "usage_report@23:00",
            "daily_backup@03:00",
            "create_daily_workout@05:30",
            "workout_reminder_poll@every_5m",
            "monthly_budget_audit@1st_06:00",
        ],
    )

    return scheduler


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


async def briefing_job(orchestrator: Orchestrator) -> None:
    """Generate and deliver the morning briefing via the orchestrator."""
    await _run_orchestrated_job(
        orchestrator,
        job_name="morning_briefing",
        message="/briefing",
    )


async def email_poll_job(orchestrator: Orchestrator) -> None:
    """Poll for new emails and classify them via the orchestrator."""
    await _run_orchestrated_job(
        orchestrator,
        job_name="email_poll",
        message="/email check",
    )


async def eod_summary_job(orchestrator: Orchestrator) -> None:
    """Generate the end-of-day summary via the orchestrator."""
    await _run_orchestrated_job(
        orchestrator,
        job_name="eod_summary",
        message="/eod",
    )


async def job_search_scan(orchestrator: Orchestrator) -> None:
    """Run the daily job search scan via the orchestrator."""
    await _run_orchestrated_job(
        orchestrator,
        job_name="job_search_scan",
        message="/jobs scan",
    )


async def health_check_job(orchestrator: Orchestrator) -> None:
    """Run system health checks via the orchestrator."""
    await _run_orchestrated_job(
        orchestrator,
        job_name="health_check",
        message="/health check",
    )


async def teller_sync_job(orchestrator: Orchestrator) -> None:
    """Sync yesterday's bank transactions from Teller.io.

    Direct job (not orchestrated) — runs TellerClient.sync_daily(), then
    detects recurring charges and scans for anomalies. Sends notification
    alerts for any warning-severity anomalies found.
    """
    job_name = "teller_sync"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from agents.anomaly_detector import AnomalyDetector
        from agents.subscription_tracker import SubscriptionTracker
        from config import get_settings
        from db.session import get_db_session
        from integrations.notification_service import get_notification_service
        from integrations.teller_client import TellerClient

        settings = get_settings()
        teller = TellerClient(
            access_token=settings.teller_access_token,
            cert_path=settings.teller_cert_path,
            key_path=settings.teller_key_path,
            env=settings.teller_env,
        )

        try:
            new_count = await teller.sync_daily()

            async with get_db_session() as session:
                # Detect recurring charges and update is_recurring flags
                tracker = SubscriptionTracker()
                await tracker.detect_recurring(session)

                # Scan for anomalies
                detector = AnomalyDetector()
                anomalies = await detector.scan_recent(session, lookback_days=1)

            # Send alerts for warning-severity anomalies
            warning_anomalies = [a for a in anomalies if a.severity == "warning"]
            warnings_sent = 0
            if warning_anomalies:
                try:
                    notifier = get_notification_service()
                    lines = [f"• {a.details}" for a in warning_anomalies]
                    await notifier.notify_alert(
                        title=f"Finance Alert: {len(warning_anomalies)} unusual transaction{'s' if len(warning_anomalies) != 1 else ''}",
                        body="\n".join(lines),
                        severity="warning",
                    )
                    warnings_sent = len(warning_anomalies)
                except Exception:
                    log.exception("teller_sync_notify_failed", warnings=len(warning_anomalies))

            duration_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "teller_sync_job_completed",
                job=job_name,
                duration_ms=duration_ms,
                new_transactions=new_count,
                anomalies_found=len(anomalies),
                warnings_sent=warnings_sent,
            )

        finally:
            await teller.close()

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def expire_approvals_job(orchestrator: Orchestrator) -> None:
    """Expire stale approvals directly (no orchestrator pipeline needed)."""
    job_name = "expire_approvals"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from db.session import get_db_session

        async with get_db_session() as session:
            expired_count = await orchestrator.approval_manager.expire_stale(session)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            expired_count=expired_count,
        )
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def usage_report_job(orchestrator: Orchestrator) -> None:
    """Generate a daily AI usage report and send via notification service."""
    job_name = "usage_report"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from db.session import get_db_session
        from integrations.notification_service import get_notification_service
        from models.usage_tracker import UsageTracker

        async with get_db_session() as session:
            tracker = UsageTracker(session)
            report = await tracker.generate_report()

        duration_ms = int((time.monotonic() - start) * 1000)

        log.info(
            "daily_usage_report",
            daily_summary=report["daily"],
            weekly_summary=report["weekly"],
            top_agents=report["top_agents"],
            budget_alert=report.get("budget_alert"),
        )
        log.info("scheduled_job_completed", job=job_name, duration_ms=duration_ms)

        # Send formatted report via notification service
        notifier = get_notification_service()
        await notifier.notify(
            title="Daily AI Usage Report",
            body=report["formatted"],
            priority="warning" if report.get("budget_alert") else "info",
        )

        if report.get("budget_alert"):
            log.warning("budget_alert_from_report", alert=report["budget_alert"])

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def backup_job(orchestrator: Orchestrator) -> None:
    """Run the daily database backup via backup.sh and notify on result."""
    job_name = "daily_backup"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from integrations.notification_service import get_notification_service

        script_path = Path("/opt/tars/deploy/scripts/backup.sh")
        if not script_path.exists():
            # Fallback for development layout
            script_path = Path(__file__).resolve().parents[3] / "deploy" / "scripts" / "backup.sh"

        if not script_path.exists():
            raise FileNotFoundError(f"backup.sh not found at {script_path}")

        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

        duration_ms = int((time.monotonic() - start) * 1000)
        notifier = get_notification_service()

        if proc.returncode == 0:
            output = stdout.decode().strip()
            # Extract backup size from the last line of output
            last_line = output.splitlines()[-1] if output else "Backup complete"

            log.info(
                "scheduled_job_completed",
                job=job_name,
                duration_ms=duration_ms,
                output=last_line,
            )

            await notifier.notify(
                title="Database Backup Complete",
                body=f"{last_line}\nDuration: {duration_ms}ms",
                priority="info",
            )
        else:
            error_output = stderr.decode().strip() or stdout.decode().strip()
            log.error(
                "scheduled_job_failed",
                job=job_name,
                duration_ms=duration_ms,
                return_code=proc.returncode,
                error=error_output[:500],
            )

            await notifier.notify_alert(
                title="Database Backup FAILED",
                body=f"Exit code: {proc.returncode}\n{error_output[:300]}",
                severity="critical",
            )

    except TimeoutError:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.error("scheduled_job_failed", job=job_name, duration_ms=duration_ms, error="timeout")

        try:
            from integrations.notification_service import get_notification_service

            notifier = get_notification_service()
            await notifier.notify_alert(
                title="Database Backup FAILED",
                body="Backup timed out after 5 minutes",
                severity="critical",
            )
        except Exception:
            log.exception("backup_timeout_notify_failed")

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def create_daily_workout_session(orchestrator: Orchestrator) -> None:
    """Create today's workout session from the active split and calendar."""
    job_name = "create_daily_workout"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        async with get_db_session() as session:
            repo = WorkoutRepository(session)
            split = await repo.get_active_split()

            if split is None:
                log.info("workout_no_active_split", job=job_name)
                return

            # Check if session already exists for today
            existing = await repo.get_today_session()
            if existing is not None:
                log.info("workout_session_already_exists", job=job_name)
                return

            # Determine today's rotation day
            from sqlalchemy import select

            from db.models import WorkoutSession

            last_result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.split_id == split.id)
                .order_by(WorkoutSession.created_at.desc())
                .limit(1)
            )
            last_session = last_result.scalar_one_or_none()

            if last_session is not None:
                next_index = (last_session.rotation_index + 1) % len(split.rotation_days)
            else:
                next_index = 0

            day_name = split.rotation_days[next_index]

            # Skip creating a session for rest days
            if day_name.lower() == "rest":
                log.info("workout_rest_day", job=job_name, day_name=day_name)
                duration_ms = int((time.monotonic() - start) * 1000)
                log.info("scheduled_job_completed", job=job_name, duration_ms=duration_ms)
                return

            # Try to get scheduled time from calendar
            scheduled_at = None
            try:
                from datetime import date as date_type

                from config import get_settings
                from integrations.caldav_client import CalDAVClient

                settings = get_settings()
                caldav = CalDAVClient(
                    username=settings.icloud_caldav_user,
                    password=settings.icloud_caldav_password,
                )
                today_events = await caldav.get_events(date_type.today())

                gym_keywords = ("gym", "workout", "exercise", "fitness", "lift", "training")
                for event in today_events:
                    title = str(event.get("title", "")).lower()
                    if any(kw in title for kw in gym_keywords):
                        start_str = event.get("start", "")
                        if start_str:
                            from datetime import datetime as dt

                            scheduled_at = dt.fromisoformat(start_str)
                            break
            except Exception:
                log.warning("workout_calendar_lookup_failed", exc_info=True)

            await repo.create_session(
                split_id=split.id,
                day_name=day_name,
                rotation_index=next_index,
                scheduled_at=scheduled_at,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            day_name=day_name,
            scheduled_at=str(scheduled_at) if scheduled_at else None,
        )

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def workout_reminder_poll(orchestrator: Orchestrator) -> None:
    """Poll for pending workout sessions past their scheduled time and send nudges."""
    job_name = "workout_reminder_poll"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        import json as _json
        from datetime import datetime

        from db.repositories.workout import WorkoutRepository
        from db.session import get_db_session

        async with get_db_session() as session:
            repo = WorkoutRepository(session)
            pending = await repo.get_pending_sessions_past_schedule()

            now = datetime.now(UTC)

            for pending_session in pending:
                if pending_session.scheduled_at is None:
                    continue

                elapsed = now - pending_session.scheduled_at
                elapsed_minutes = elapsed.total_seconds() / 60

                # Track which nudges have been sent via notes field (JSON)
                nudge_state = _json.loads(pending_session.notes or '{"sent": []}')
                sent = set(nudge_state.get("sent", []))

                if elapsed_minutes >= 90 and "auto_skip" not in sent:
                    # Auto-skip
                    await repo.skip_session(pending_session.id, reason="no response")
                    log.info(
                        "workout_auto_skipped",
                        session_id=str(pending_session.id),
                        elapsed_min=int(elapsed_minutes),
                    )
                    try:
                        from integrations.notification_service import get_notification_service

                        notifier = get_notification_service()
                        await notifier.notify_alert(
                            title="Workout Skipped",
                            body=f"No response for {pending_session.day_name} day. Session auto-skipped. Streak broken.",
                            severity="warning",
                        )
                    except Exception:
                        log.warning("workout_skip_notify_failed", exc_info=True)

                elif elapsed_minutes >= 60 and "nudge_60" not in sent:
                    # Second nudge
                    sent.add("nudge_60")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from integrations.notification_service import get_notification_service

                        notifier = get_notification_service()
                        await notifier.notify_alert(
                            title=f"Last chance — {pending_session.day_name.title()} Day",
                            body="You have 30 minutes before this gets marked as skipped. Get moving.",
                            severity="warning",
                        )
                    except Exception:
                        log.warning("workout_nudge_failed", exc_info=True)

                elif elapsed_minutes >= 30 and "nudge_30" not in sent:
                    # First nudge
                    sent.add("nudge_30")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from integrations.notification_service import get_notification_service

                        notifier = get_notification_service()
                        await notifier.notify(
                            title=f"{pending_session.day_name.title()} Day — Still Waiting",
                            body="Workout was scheduled. Starting now or skipping?",
                            priority="info",
                        )
                    except Exception:
                        log.warning("workout_nudge_failed", exc_info=True)

                elif elapsed_minutes >= 0 and "reminder" not in sent:
                    # Initial reminder (workout time arrived)
                    sent.add("reminder")
                    pending_session.notes = _json.dumps({"sent": list(sent)})
                    await session.flush()
                    try:
                        from config import get_settings
                        from integrations.apns_client import APNsClient

                        settings = get_settings()
                        apns = APNsClient(
                            key_path=settings.apns_key_path,
                            key_id=settings.apns_key_id,
                            team_id=settings.apns_team_id,
                            bundle_id=settings.apns_bundle_id,
                        )
                        await apns.send_workout_reminder(
                            session_id=str(pending_session.id),
                            day_name=pending_session.day_name,
                        )
                    except Exception:
                        log.warning("workout_apns_reminder_failed", exc_info=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            pending_sessions=len(pending),
        )

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


async def monthly_budget_audit(orchestrator: Orchestrator) -> None:
    """Analyze last month's transactions and suggest new budget categories.

    Runs on the 1st of each month.  Uses Gemini Flash to review uncategorised
    spending (transactions whose category doesn't match an existing budget) and
    proposes new budget categories.  Suggestions are sent as an info
    notification — never auto-created (user decides).
    """
    job_name = "monthly_budget_audit"
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        import json as _json
        from datetime import date, timedelta

        from config import get_settings
        from db.repositories.budgets import BudgetRepository
        from db.session import get_db_session
        from integrations.notification_service import get_notification_service
        from models.gemini_client import GeminiClient

        settings = get_settings()

        today = date.today()
        # Last month's range
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        async with get_db_session() as session:
            repo = BudgetRepository(session)
            active_budgets = await repo.get_active()
            tracked_categories = {b.category.lower() for b in active_budgets}

            # Query last month's spending grouped by category
            from sqlalchemy import func, select

            from db.models import Transaction

            result = await session.execute(
                select(
                    func.coalesce(func.lower(Transaction.category), "uncategorized"),
                    func.sum(Transaction.amount),
                    func.count(Transaction.id),
                )
                .where(
                    Transaction.transaction_date >= last_month_start,
                    Transaction.transaction_date <= last_month_end,
                    Transaction.pending == False,  # noqa: E712
                    Transaction.amount > 0,
                )
                .group_by(func.coalesce(func.lower(Transaction.category), "uncategorized"))
                .order_by(func.sum(Transaction.amount).desc())
            )
            all_spending = [{"category": row[0], "total": float(row[1]), "count": int(row[2])} for row in result.all()]

        if not all_spending:
            log.info("monthly_budget_audit_skipped", reason="no_transactions")
            return

        # Split into tracked vs untracked
        untracked = [s for s in all_spending if s["category"] not in tracked_categories]
        tracked = [s for s in all_spending if s["category"] in tracked_categories]

        if not untracked:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "monthly_budget_audit_complete",
                job=job_name,
                duration_ms=duration_ms,
                result="all_categories_tracked",
            )
            return

        # Ask Gemini to analyze untracked spending and suggest budgets
        gemini = GeminiClient(api_key=settings.gemini_api_key)
        prompt = f"""You are a personal finance assistant for a single user. Analyze their spending data and suggest new budget categories.

CURRENTLY TRACKED BUDGET CATEGORIES:
{_json.dumps([{"category": s["category"], "spent_last_month": s["total"]} for s in tracked], indent=2)}

UNTRACKED SPENDING (no budget set):
{_json.dumps(untracked, indent=2)}

Rules:
- Only suggest categories where last month's spending was $30+ (meaningful enough to track)
- Suggest a reasonable monthly_limit (round to nearest $25, give ~20% buffer over last month)
- Keep category names simple, lowercase, one or two words (e.g. "utilities", "health", "education")
- If multiple small untracked categories could merge into one, suggest the merged name
- Maximum 3 suggestions

Respond in JSON format:
{{"suggestions": [{{"category": "name", "monthly_limit": 150, "reason": "You spent $X on Y last month"}}], "summary": "one line summary"}}"""

        response = await gemini.generate(
            prompt=prompt,
            model="gemini-2.5-flash",
            system_instruction="You are T.A.R.S., a personal finance assistant. Be concise and practical.",
            temperature=0.3,
            max_output_tokens=512,
            response_format="json",
        )

        try:
            audit_result = _json.loads(response.text)
        except _json.JSONDecodeError:
            log.warning("monthly_budget_audit_parse_failed", raw=response.text[:200])
            audit_result = {"suggestions": [], "summary": "Could not parse AI response"}

        suggestions = audit_result.get("suggestions", [])
        summary = audit_result.get("summary", "")

        duration_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "monthly_budget_audit_complete",
            job=job_name,
            duration_ms=duration_ms,
            suggestions_count=len(suggestions),
            untracked_categories=len(untracked),
            summary=summary,
        )

        # Notify user with suggestions (never auto-create)
        if suggestions:
            lines = [f"Monthly Budget Audit — {last_month_start.strftime('%B %Y')}\n"]
            lines.append(summary)
            lines.append("")
            for s in suggestions:
                lines.append(f"• {s['category'].title()}: suggest ${s['monthly_limit']}/mo — {s['reason']}")
            lines.append("\nTo add: tell me 'set budget [category] to $[amount]'")

            notifier = get_notification_service()
            await notifier.notify(
                title="Budget Suggestions Available",
                body="\n".join(lines),
                priority="info",
            )

    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _run_orchestrated_job(
    orchestrator: Orchestrator,
    *,
    job_name: str,
    message: str,
) -> None:
    """Run a job through the full orchestrator pipeline.

    This ensures intent classification, model routing, usage tracking,
    approval flow, and audit logging all apply — even for scheduled jobs.
    """
    log.info("scheduled_job_started", job=job_name)
    start = time.monotonic()

    try:
        response = await orchestrator.process_message(
            text=message,
            source="scheduler",
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        success = response.get("success", True)
        log.info(
            "scheduled_job_completed",
            job=job_name,
            duration_ms=duration_ms,
            success=success,
        )
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.exception("scheduled_job_failed", job=job_name, duration_ms=duration_ms)
