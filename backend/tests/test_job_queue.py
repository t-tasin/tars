"""Tests for ``integrations.job_queue.JobQueue``.

Uses fakeredis for in-memory Redis with full pub/sub support.
"""

from __future__ import annotations

import asyncio
import json

import fakeredis.aioredis
import pytest

from integrations.job_queue import (
    PRIORITY_SCORES,
    QUEUE_KEY,
    RESULTS_CHANNEL,
    JobQueue,
)


@pytest.fixture()
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
async def queue(redis):
    return JobQueue(redis)


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


async def test_enqueue_adds_to_sorted_set_with_priority_score(queue, redis):
    job_id = await queue.enqueue("code", {"task": "do thing"}, priority="high")
    assert job_id
    members = await redis.zrange(QUEUE_KEY, 0, -1, withscores=True)
    assert len(members) == 1
    raw, score = members[0]
    job = json.loads(raw)
    assert job["job_id"] == job_id
    assert job["type"] == "code"
    assert job["payload"] == {"task": "do thing"}
    assert job["priority"] == "high"
    assert PRIORITY_SCORES["high"] <= score < PRIORITY_SCORES["normal"]


async def test_enqueue_default_priority_is_normal(queue, redis):
    await queue.enqueue("code", {})
    _, score = (await redis.zrange(QUEUE_KEY, 0, -1, withscores=True))[0]
    assert PRIORITY_SCORES["normal"] <= score < PRIORITY_SCORES["low"]


async def test_enqueue_accepts_explicit_job_id(queue):
    jid = await queue.enqueue("code", {}, job_id="fixed-id")
    assert jid == "fixed-id"


async def test_enqueue_generates_uuid_when_no_job_id(queue):
    jid = await queue.enqueue("code", {})
    # UUID4 string is 36 chars with dashes
    assert len(jid) == 36 and jid.count("-") == 4


async def test_critical_dequeues_before_low(queue, redis):
    await queue.enqueue("code", {}, priority="low", job_id="low-1")
    await queue.enqueue("code", {}, priority="critical", job_id="crit-1")
    raw, _ = (await redis.zpopmin(QUEUE_KEY, count=1))[0]
    assert json.loads(raw)["job_id"] == "crit-1"


# ---------------------------------------------------------------------------
# enqueue_and_wait
# ---------------------------------------------------------------------------


async def _fake_worker_round_trip(redis, override_result=None, delay=0.02):
    """Simulate the worker: pop one job + publish a result for it."""
    await asyncio.sleep(delay)
    members = await redis.zpopmin(QUEUE_KEY, count=1)
    assert members, "worker found no job"
    raw, _score = members[0]
    job = json.loads(raw)
    result = override_result or {
        "job_id": job["job_id"],
        "type": job["type"],
        "result": {"status": "completed", "output": "ok"},
        "duration_ms": 42,
    }
    await redis.publish(RESULTS_CHANNEL, json.dumps(result))


async def test_enqueue_and_wait_returns_matching_result(queue, redis):
    worker = asyncio.create_task(_fake_worker_round_trip(redis))
    data = await queue.enqueue_and_wait("code", {"task": "x"}, timeout=3.0)
    await worker
    assert data["result"]["status"] == "completed"
    assert data["result"]["output"] == "ok"
    assert data["duration_ms"] == 42


async def test_enqueue_and_wait_ignores_results_for_other_jobs(queue, redis):
    async def noise_then_real():
        await asyncio.sleep(0.02)
        # Unrelated result — must be skipped.
        await redis.publish(
            RESULTS_CHANNEL,
            json.dumps(
                {
                    "job_id": "other-job",
                    "type": "code",
                    "result": {"status": "completed"},
                    "duration_ms": 1,
                }
            ),
        )
        await asyncio.sleep(0.02)
        members = await redis.zpopmin(QUEUE_KEY, count=1)
        raw, _ = members[0]
        job = json.loads(raw)
        await redis.publish(
            RESULTS_CHANNEL,
            json.dumps(
                {
                    "job_id": job["job_id"],
                    "type": "code",
                    "result": {"status": "completed", "output": "mine"},
                    "duration_ms": 2,
                }
            ),
        )

    worker = asyncio.create_task(noise_then_real())
    data = await queue.enqueue_and_wait("code", {}, job_id="mine", timeout=3.0)
    await worker
    assert data["job_id"] == "mine"
    assert data["result"]["output"] == "mine"


async def test_enqueue_and_wait_raises_on_timeout(queue):
    with pytest.raises(TimeoutError):
        await queue.enqueue_and_wait("code", {}, timeout=0.3)


async def test_enqueue_and_wait_survives_worker_publishing_before_subscribe_race(queue, redis):
    """Regression: subscribe must happen BEFORE enqueue.

    A worker that returns in ~0ms would otherwise publish before the
    caller subscribes, and the caller would time out.  ``enqueue_and_wait``
    must not be vulnerable to this.
    """
    worker = asyncio.create_task(_fake_worker_round_trip(redis, delay=0))
    data = await queue.enqueue_and_wait("code", {}, timeout=3.0)
    await worker
    assert data["result"]["status"] == "completed"


# ---------------------------------------------------------------------------
# schema sanity
# ---------------------------------------------------------------------------


async def test_enqueued_job_has_all_required_schema_fields(queue, redis):
    await queue.enqueue("code", {"k": "v"}, priority="high", job_id="j1")
    raw, _ = (await redis.zrange(QUEUE_KEY, 0, -1, withscores=True))[0]
    job = json.loads(raw)
    assert set(job.keys()) == {
        "job_id",
        "type",
        "payload",
        "priority",
        "enqueued_at",
    }
    assert isinstance(job["enqueued_at"], float)
