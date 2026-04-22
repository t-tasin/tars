"""JobQueue — Node 1 side of the distributed job queue.

Backend enqueues jobs to the Redis sorted set ``tars:jobs:queue``
(ZADD with a priority score). Worker on Node 2 ZPOPMINs the same
key and publishes results on the single pub/sub channel
``tars:jobs:results``.

This module exists because earlier code LPUSHed to ``tars:jobs:code``
(list) while the worker ZPOPMINed ``tars:jobs:queue`` (sorted set):
jobs never dispatched.  CLAUDE.md pitfall #6.

Enqueued-job schema (JSON string stored as the ZSET member)::

    {"job_id": str, "type": str, "payload": dict,
     "priority": str, "enqueued_at": float}

Result-message schema (JSON string on ``tars:jobs:results``)::

    {"job_id": str, "type": str, "result": dict, "duration_ms": int}

Priority maps to a score: lower score = dequeued first (ZPOPMIN
returns the minimum).  Same-priority jobs get a tiny time-based
jitter so they drain FIFO.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()

QUEUE_KEY = "tars:jobs:queue"
RESULTS_CHANNEL = "tars:jobs:results"

PRIORITY_SCORES: dict[str, float] = {
    "critical": 0.0,
    "high": 100.0,
    "normal": 200.0,
    "low": 300.0,
}

_DEFAULT_PRIORITY = "normal"


class JobQueue:
    """Async client for the T.A.R.S. distributed job queue.

    Call sites typically use :meth:`enqueue_and_wait` to dispatch a job
    and block on its result.  For fire-and-forget dispatch (e.g. image
    saves), call :meth:`enqueue` only.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        priority: str = _DEFAULT_PRIORITY,
        job_id: str | None = None,
    ) -> str:
        """Enqueue a job onto the sorted set.  Returns the job_id."""
        job_id = job_id or str(uuid4())
        base_score = PRIORITY_SCORES.get(priority, PRIORITY_SCORES[_DEFAULT_PRIORITY])
        score = base_score + time.time() / 1e12
        job = {
            "job_id": job_id,
            "type": job_type,
            "payload": payload,
            "priority": priority,
            "enqueued_at": time.time(),
        }
        await self._redis.zadd(QUEUE_KEY, {json.dumps(job): score})
        log.info(
            "job_enqueued",
            job_id=job_id,
            job_type=job_type,
            priority=priority,
            queue=QUEUE_KEY,
        )
        return job_id

    async def wait_for_result(
        self,
        job_id: str,
        timeout: float,
        *,
        pubsub: Any | None = None,
    ) -> dict[str, Any]:
        """Block until a result for ``job_id`` arrives on the results channel.

        If ``pubsub`` is supplied it must already be subscribed to
        :data:`RESULTS_CHANNEL` — the caller keeps ownership.  When omitted
        we subscribe here, which introduces a small race (a result that
        lands before this method subscribes is missed); prefer
        :meth:`enqueue_and_wait`.

        Raises ``TimeoutError`` if no matching result arrives in time.
        """
        owns_pubsub = pubsub is None
        if owns_pubsub:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(RESULTS_CHANNEL)
        try:
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"No result for job {job_id} within {timeout}s")
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=min(remaining, 5.0),
                )
                if not message or message.get("type") != "message":
                    continue
                data = json.loads(message["data"])
                if data.get("job_id") == job_id:
                    return data
        finally:
            if owns_pubsub:
                await pubsub.unsubscribe(RESULTS_CHANNEL)
                await pubsub.aclose()

    async def enqueue_and_wait(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        priority: str = _DEFAULT_PRIORITY,
        timeout: float = 300.0,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Enqueue a job then wait for its result.

        Subscribes to the results channel *before* enqueue to avoid
        missing the response from a fast-returning job.
        """
        job_id = job_id or str(uuid4())
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(RESULTS_CHANNEL)
        try:
            await self.enqueue(
                job_type,
                payload,
                priority=priority,
                job_id=job_id,
            )
            return await self.wait_for_result(job_id, timeout, pubsub=pubsub)
        finally:
            await pubsub.unsubscribe(RESULTS_CHANNEL)
            await pubsub.aclose()

    async def aclose(self) -> None:
        """Close the underlying Redis connection."""
        await self._redis.aclose()


def get_job_queue(redis_url: str | None = None) -> JobQueue:
    """Build a JobQueue backed by a fresh Redis connection.

    Caller owns the lifecycle — call :meth:`JobQueue.aclose` when done.
    """
    from config import get_settings

    url = redis_url or get_settings().redis_url
    redis = Redis.from_url(url, decode_responses=True)
    return JobQueue(redis=redis)
