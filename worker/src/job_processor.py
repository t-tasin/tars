from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import structlog
from redis.asyncio import Redis

from .executors.base import BaseExecutor
from .executors.code_executor import CodeExecutor
from .executors.diagnostic_executor import DiagnosticExecutor
from .executors.image_processor import ImageProcessor
from .executors.job_scraper_executor import JobScraperExecutor
from .executors.research_executor import ResearchExecutor

log = structlog.get_logger("tars.worker.processor")

# Redis keys
QUEUE_KEY = "tars:jobs:queue"
PROCESSING_KEY = "tars:jobs:processing"
RESULTS_CHANNEL = "tars:jobs:results"

# Priority scores — lower score = higher priority (sorted set ascending)
PRIORITY_SCORES: dict[str, float] = {
    "critical": 0.0,
    "high": 100.0,
    "normal": 200.0,
    "low": 300.0,
}


class JobProcessor:
    """Consumes jobs from a Redis sorted set (priority queue) and dispatches to executors."""

    def __init__(
        self,
        redis: Redis,
        poll_interval: float = 0.5,
        job_timeout: int = 300,
        max_concurrent: int = 4,
    ) -> None:
        self._redis = redis
        self._poll_interval = poll_interval
        self._job_timeout = job_timeout
        self._max_concurrent = max_concurrent
        self._running = False
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._executors: dict[str, BaseExecutor] = self._build_executor_map()

    @staticmethod
    def _build_executor_map() -> dict[str, BaseExecutor]:
        executors: list[BaseExecutor] = [
            CodeExecutor(),
            ResearchExecutor(),
            DiagnosticExecutor(),
            JobScraperExecutor(),
            ImageProcessor(),
        ]
        return {e.EXECUTOR_TYPE: e for e in executors}

    async def start(self) -> None:
        """Start the job consumption loop."""
        self._running = True
        log.info(
            "job_processor_started",
            poll_interval=self._poll_interval,
            max_concurrent=self._max_concurrent,
            executor_types=list(self._executors.keys()),
        )
        while self._running:
            try:
                await self._poll_once()
            except Exception:
                log.exception("job_poll_error")
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        """Gracefully stop the processor and wait for active jobs."""
        self._running = False
        if self._active_jobs:
            log.info("job_processor_draining", active_count=len(self._active_jobs))
            await asyncio.gather(*self._active_jobs.values(), return_exceptions=True)
        log.info("job_processor_stopped")

    async def _poll_once(self) -> None:
        """Try to pop the highest-priority job from the sorted set."""
        if len(self._active_jobs) >= self._max_concurrent:
            return

        # ZPOPMIN returns list of (member, score) tuples — pop 1 item
        results = await self._redis.zpopmin(QUEUE_KEY, count=1)
        if not results:
            return

        raw_job, _score = results[0]
        job = json.loads(raw_job)
        job_id: str = job.get("job_id", str(uuid.uuid4()))
        job_type: str = job.get("type", "unknown")

        log.info("job_dequeued", job_id=job_id, job_type=job_type, score=_score)

        # Track in processing set
        await self._redis.sadd(PROCESSING_KEY, job_id)

        task = asyncio.create_task(self._execute_job(job_id, job_type, job))
        self._active_jobs[job_id] = task
        task.add_done_callback(lambda t, jid=job_id: self._on_job_done(jid))

    def _on_job_done(self, job_id: str) -> None:
        self._active_jobs.pop(job_id, None)

    async def _execute_job(self, job_id: str, job_type: str, job: dict[str, Any]) -> None:
        """Route the job to the appropriate executor, publish result."""
        executor = self._executors.get(job_type)
        start_time = time.monotonic()

        if executor is None:
            result = {"status": "failed", "error": f"unknown job type: {job_type}"}
            log.error("unknown_job_type", job_id=job_id, job_type=job_type)
        else:
            try:
                result = await asyncio.wait_for(
                    executor.execute(job_id, job.get("payload", {})),
                    timeout=self._job_timeout,
                )
            except asyncio.TimeoutError:
                result = {"status": "failed", "error": "job timed out"}
                log.error("job_timeout", job_id=job_id, job_type=job_type, timeout=self._job_timeout)
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)}
                log.exception("job_execution_error", job_id=job_id, job_type=job_type)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        result_message = {
            "job_id": job_id,
            "type": job_type,
            "result": result,
            "duration_ms": duration_ms,
        }

        # Publish result via pub/sub
        await self._redis.publish(RESULTS_CHANNEL, json.dumps(result_message))

        # Remove from processing set
        await self._redis.srem(PROCESSING_KEY, job_id)

        log.info(
            "job_completed",
            job_id=job_id,
            job_type=job_type,
            status=result.get("status"),
            duration_ms=duration_ms,
        )

    @staticmethod
    def enqueue_job_payload(
        job_id: str,
        job_type: str,
        payload: dict[str, Any],
        priority: str = "normal",
    ) -> tuple[str, float]:
        """Build the (serialized_job, score) tuple for ZADD.

        Helper for the Node 1 side to enqueue jobs. Returns (json_str, score).
        """
        score = PRIORITY_SCORES.get(priority, PRIORITY_SCORES["normal"])
        # Add timestamp fraction so same-priority jobs are FIFO
        score += time.time() / 1e12
        job = {
            "job_id": job_id,
            "type": job_type,
            "payload": payload,
            "priority": priority,
            "enqueued_at": time.time(),
        }
        return json.dumps(job), score
