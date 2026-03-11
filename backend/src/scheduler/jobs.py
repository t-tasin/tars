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
            "bash", str(script_path),
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

    except asyncio.TimeoutError:
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
