"""Tests for ``agents.coding.CodingAgent`` — simple dispatch path via JobQueue.

The complex-task pipeline path is covered in ``test_coding_pipeline.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.base import AgentContext
from agents.coding import CodingAgent


def _context(message: str) -> AgentContext:
    return AgentContext(user_message=message, intent_type="coding")


def _fake_queue(message: dict) -> MagicMock:
    q = MagicMock()
    q.enqueue_and_wait = AsyncMock(return_value=message)
    return q


async def test_execute_dispatches_via_job_queue_with_correct_payload():
    queue = _fake_queue(
        {
            "job_id": "jid",
            "type": "code",
            "result": {
                "status": "completed",
                "success": True,
                "claude_output": "done",
                "diff_summary": "+1 -1",
                "test_output": "",
                "files_changed": ["a.py"],
                "has_push": False,
                "has_pr": False,
                "duration_ms": 150,
            },
            "duration_ms": 200,
        }
    )
    agent = CodingAgent(job_queue=queue)

    result = await agent.execute(_context("fix the bug in https://github.com/alice/repo"))

    assert result.success is True
    queue.enqueue_and_wait.assert_awaited_once()
    call = queue.enqueue_and_wait.await_args
    assert call.args[0] == "code"  # job_type
    payload = call.args[1]
    assert payload["repo_url"] == "https://github.com/alice/repo.git"
    assert payload["branch"] == "main"
    assert payload["task"].startswith("fix the bug")
    assert "github_pat" in payload
    assert "timeout" in payload


async def test_execute_returns_failure_when_no_repo_provided():
    queue = _fake_queue({})
    agent = CodingAgent(job_queue=queue)

    result = await agent.execute(_context("please do a thing"))

    assert result.success is False
    assert result.error == "no_repo"
    queue.enqueue_and_wait.assert_not_called()


async def test_execute_surfaces_worker_failure_from_result_envelope():
    queue = _fake_queue(
        {
            "job_id": "jid",
            "type": "code",
            "result": {
                "status": "failed",
                "success": False,
                "error": "git clone failed (exit 128)",
                "duration_ms": 50,
            },
            "duration_ms": 60,
        }
    )
    agent = CodingAgent(job_queue=queue)

    result = await agent.execute(_context("tweak alice/repo"))

    assert result.success is False
    assert result.error == "job_failed"
    assert "git clone failed" in result.text


async def test_execute_returns_approval_when_push_intent_detected():
    queue = _fake_queue(
        {
            "job_id": "jid",
            "type": "code",
            "result": {
                "status": "completed",
                "success": True,
                "claude_output": "pushed",
                "diff_summary": "+5 -2",
                "test_output": "",
                "files_changed": ["x.py"],
                "has_push": True,
                "has_pr": False,
                "duration_ms": 200,
            },
            "duration_ms": 220,
        }
    )
    agent = CodingAgent(job_queue=queue)

    result = await agent.execute(_context("tweak alice/repo"))

    assert result.success is True
    assert result.content_type == "approval"
    assert result.has_side_effects is True
    assert result.action_type == "push_production"


async def test_execute_timeout_returns_job_timeout_error():
    queue = MagicMock()
    queue.enqueue_and_wait = AsyncMock(side_effect=TimeoutError("boom"))
    agent = CodingAgent(job_queue=queue)

    result = await agent.execute(_context("tweak alice/repo"))

    assert result.success is False
    assert result.error == "job_timeout"


@pytest.mark.parametrize(
    "message",
    [
        "build a new feature in alice/repo",
        "refactor the auth module in alice/repo",
        "implement OAuth in alice/repo",
    ],
)
async def test_execute_routes_complex_tasks_to_pipeline_not_queue(message, monkeypatch):
    queue = _fake_queue({})
    agent = CodingAgent(job_queue=queue)

    pipeline_run = AsyncMock()
    pipeline_run.return_value = MagicMock(
        success=False,
        error="skip",
        plan_summary="",
    )
    monkeypatch.setattr(
        "agents.coding.CodingPipeline.run",
        pipeline_run,
    )

    await agent.execute(_context(message))

    queue.enqueue_and_wait.assert_not_called()
    pipeline_run.assert_awaited_once()
