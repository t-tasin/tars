"""End-to-end round-trip test for the distributed job queue.

Exercises the full wire protocol between the backend :class:`JobQueue`
and the worker :class:`JobProcessor` against a shared in-memory Redis
(``fakeredis``).  Two separate Redis clients are used — one per side —
so the test fails if either side drifts from the shared contract:

    * queue key:  ``tars:jobs:queue``  (ZADD / ZPOPMIN sorted set)
    * results:    ``tars:jobs:results`` (single pub/sub channel)
    * message:    ``{"job_id","type","payload","priority","enqueued_at"}``

This is the fix for the Phase 1 P0 bug: backend used to LPUSH
``tars:jobs:code`` (a list) while the worker ZPOPMINed
``tars:jobs:queue`` (a sorted set), so no job ever dispatched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest

from integrations.job_queue import JobQueue


class _EchoExecutor:
    """Minimal executor that echoes its payload back.  Used by the E2E
    test to avoid requiring Docker / Gemini / etc."""

    EXECUTOR_TYPE = "echo"

    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "completed",
            "success": True,
            "job_id": job_id,
            "echo": payload,
        }


@pytest.fixture()
def fake_server():
    return fakeredis.FakeServer()


@pytest.fixture()
async def backend_redis(fake_server):
    r = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
async def worker_redis(fake_server):
    r = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


async def test_backend_enqueue_reaches_worker_and_result_returns(backend_redis, worker_redis):
    """Full round-trip: backend enqueue → worker pop → worker publish → backend receive."""
    from worker.src.job_processor import JobProcessor

    queue = JobQueue(backend_redis)
    processor = JobProcessor(
        redis=worker_redis,
        poll_interval=0.05,
        job_timeout=5,
        max_concurrent=1,
    )
    # Inject the test-only echo executor.
    processor._executors = {"echo": _EchoExecutor()}

    processor_task = asyncio.create_task(processor.start())
    try:
        message = await queue.enqueue_and_wait(
            "echo",
            {"hello": "world"},
            priority="high",
            timeout=5.0,
        )
    finally:
        await processor.stop()
        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass

    assert message["type"] == "echo"
    assert message["result"]["status"] == "completed"
    assert message["result"]["echo"] == {"hello": "world"}


async def test_unknown_job_type_returns_failed_result_not_timeout(backend_redis, worker_redis):
    """A job with no matching executor must still return a result message
    so the caller sees a proper failure instead of hanging until timeout."""
    from worker.src.job_processor import JobProcessor

    queue = JobQueue(backend_redis)
    processor = JobProcessor(
        redis=worker_redis,
        poll_interval=0.05,
        job_timeout=5,
        max_concurrent=1,
    )
    # Empty map: every job type is "unknown".
    processor._executors = {}

    processor_task = asyncio.create_task(processor.start())
    try:
        message = await queue.enqueue_and_wait(
            "nonexistent",
            {},
            timeout=5.0,
        )
    finally:
        await processor.stop()
        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass

    assert message["result"]["status"] == "failed"
    assert "unknown job type" in message["result"]["error"]


async def test_critical_priority_dispatched_before_low(backend_redis, worker_redis):
    """Serialize two jobs with different priorities; worker must pop
    critical first even though low was enqueued first."""
    from worker.src.job_processor import JobProcessor

    queue = JobQueue(backend_redis)
    processor = JobProcessor(
        redis=worker_redis,
        poll_interval=0.02,
        job_timeout=5,
        max_concurrent=1,  # force serial execution
    )

    order: list[str] = []

    class _OrderTracker:
        EXECUTOR_TYPE = "order"

        async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            order.append(payload["label"])
            await asyncio.sleep(0.01)
            return {"status": "completed", "success": True, "label": payload["label"]}

    processor._executors = {"order": _OrderTracker()}

    # Pre-load the queue before starting the processor so priority
    # ordering has something to sort.
    await queue.enqueue("order", {"label": "low"}, priority="low", job_id="low-1")
    await queue.enqueue("order", {"label": "critical"}, priority="critical", job_id="crit-1")

    processor_task = asyncio.create_task(processor.start())
    try:
        # Wait until both labels have been executed.
        for _ in range(100):
            if len(order) == 2:
                break
            await asyncio.sleep(0.05)
    finally:
        await processor.stop()
        processor_task.cancel()
        try:
            await processor_task
        except asyncio.CancelledError:
            pass

    assert order == ["critical", "low"]
