"""Coding/DevOps agent — dispatches code tasks to Node 2 via Redis job queue.

Workflow:
    1. Parse the coding request (repo URL, branch, task description)
    2. Dispatch a job to Node 2 via Redis queue
    3. Node 2 spins up a Docker container, clones the repo, injects CLAUDE.md
    4. Claude Code runs with MCP servers (GitHub + filesystem)
    5. Results (diff, test output) returned via Redis pub/sub
    6. Any code push / PR creation requires explicit approval (HC-02)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from uuid import uuid4

import structlog

from agents.base import AgentContext, AgentResult, BaseAgent
from config import get_settings

log = structlog.get_logger()

# How long to wait for a code execution job to complete (seconds).
_JOB_TIMEOUT = 300

# Redis key prefixes for the coding job queue.
_QUEUE_KEY = "tars:jobs:code"
_RESULT_CHANNEL_PREFIX = "tars:results:code:"

# Default branch to operate on when none is specified.
_DEFAULT_BRANCH = "main"


class CodingAgent(BaseAgent):
    """Dispatches coding/DevOps tasks to Node 2 for sandboxed execution.

    The agent publishes a job to a Redis queue consumed by the worker on
    Node 2.  The worker clones the repo into a Docker container, injects
    project context (CLAUDE.md), spawns Claude Code with GitHub + filesystem
    MCP servers, and streams the result back via Redis pub/sub.

    All code-push and PR-creation actions require explicit user approval
    (HC-02).
    """

    agent_type = "coding"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis: Any | None = None

    # ------------------------------------------------------------------
    # Redis connection (lazy)
    # ------------------------------------------------------------------

    async def _get_redis(self) -> Any:
        """Return a shared async Redis connection (lazy-initialised)."""
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, context: AgentContext) -> AgentResult:
        """Parse the coding request, dispatch to Node 2, and return results."""
        user_message = context.user_message

        # 1. Parse request
        parsed = _parse_coding_request(user_message)
        repo_url = parsed["repo_url"]
        branch = parsed["branch"]
        task = parsed["task"]

        if not repo_url:
            return AgentResult(
                success=False,
                text=(
                    "I need a GitHub repository to work on. "
                    "Could you specify the repo URL or name? "
                    "(e.g. 'Fix the login bug in tasin/my-app')"
                ),
                error="no_repo",
            )

        if not task:
            return AgentResult(
                success=False,
                text="What would you like me to do in this repo? Please describe the task.",
                error="no_task",
            )

        # 2. Build job payload
        job_id = str(uuid4())
        job_payload = {
            "job_id": job_id,
            "type": "code_execution",
            "repo_url": repo_url,
            "branch": branch,
            "task": task,
            "github_pat": self._settings.github_pat,
            "mcp_profile": "coding",
            "max_turns": 10,
            "timeout": _JOB_TIMEOUT - 30,  # leave margin for cleanup
            "created_at": time.time(),
        }

        # 3. Dispatch to Node 2 via Redis queue
        log.info(
            "coding_job_dispatching",
            job_id=job_id,
            repo_url=repo_url,
            branch=branch,
            task=task[:200],
        )

        try:
            result = await self._dispatch_and_wait(job_id, job_payload)
        except TimeoutError:
            log.error("coding_job_timeout", job_id=job_id, timeout=_JOB_TIMEOUT)
            return AgentResult(
                success=False,
                text=(
                    f"The coding task timed out after {_JOB_TIMEOUT}s. "
                    "The task may have been too large — try breaking it into smaller steps."
                ),
                error="job_timeout",
            )
        except Exception:
            log.exception("coding_job_failed", job_id=job_id)
            return AgentResult(
                success=False,
                text="Something went wrong dispatching the coding task. Please try again.",
                error="dispatch_error",
            )

        # 4. Process result
        if not result.get("success"):
            error_msg = result.get("error", "unknown error")
            log.error("coding_job_failed", job_id=job_id, error=error_msg)
            return AgentResult(
                success=False,
                text=f"The coding task failed: {error_msg}",
                error="job_failed",
                data={"job_id": job_id},
            )

        diff_summary = result.get("diff_summary", "")
        test_output = result.get("test_output", "")
        files_changed = result.get("files_changed", [])
        claude_output = result.get("claude_output", "")

        # 5. Determine if this needs approval
        has_push_action = result.get("has_push", False)
        has_pr_action = result.get("has_pr", False)

        if has_push_action or has_pr_action:
            # HC-02: code pushes and PR creation require approval
            action_type = "push_production" if has_push_action else "create_pr"
            preview = {
                "repo": repo_url,
                "branch": branch,
                "task": task,
                "files_changed": files_changed,
                "diff_summary": diff_summary,
                "test_output": test_output,
                "claude_explanation": claude_output,
            }

            approval_title = (
                f"Push code to {branch}" if has_push_action
                else f"Create PR for: {task[:80]}"
            )

            log.info(
                "coding_job_approval_required",
                job_id=job_id,
                action_type=action_type,
                files_changed=len(files_changed),
            )

            return AgentResult(
                success=True,
                text=f"Code changes ready for review ({len(files_changed)} files changed):",
                content_type="approval",
                has_side_effects=True,
                action_type=action_type,
                approval_title=approval_title,
                preview=preview,
                data={
                    "job_id": job_id,
                    "model": "claude_code",
                    "duration_ms": result.get("duration_ms", 0),
                    "cost_usd": result.get("cost_usd", 0.0),
                },
            )

        # No push/PR — just return the results (tier 1: informational)
        response_parts = []
        if claude_output:
            response_parts.append(claude_output)
        if diff_summary:
            response_parts.append(f"\n**Changes:**\n```diff\n{diff_summary}\n```")
        if test_output:
            response_parts.append(f"\n**Tests:**\n```\n{test_output}\n```")

        response_text = "\n".join(response_parts) or "Task completed — no changes were made."

        log.info(
            "coding_job_completed",
            job_id=job_id,
            files_changed=len(files_changed),
            duration_ms=result.get("duration_ms", 0),
        )

        return AgentResult(
            success=True,
            text=response_text,
            data={
                "job_id": job_id,
                "model": "claude_code",
                "files_changed": files_changed,
                "duration_ms": result.get("duration_ms", 0),
                "cost_usd": result.get("cost_usd", 0.0),
            },
        )

    # ------------------------------------------------------------------
    # Job dispatch + wait
    # ------------------------------------------------------------------

    async def _dispatch_and_wait(
        self,
        job_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Push job to Redis queue and wait for result on pub/sub channel.

        Raises ``TimeoutError`` if the worker doesn't respond in time.
        """
        r = await self._get_redis()
        result_channel = f"{_RESULT_CHANNEL_PREFIX}{job_id}"

        # Subscribe to the result channel BEFORE pushing the job so we
        # don't miss the response.
        pubsub = r.pubsub()
        await pubsub.subscribe(result_channel)

        try:
            # Push job onto the queue (LPUSH so the worker BRPOP's FIFO).
            await r.lpush(_QUEUE_KEY, json.dumps(payload))

            log.info("coding_job_dispatched", job_id=job_id, queue=_QUEUE_KEY)

            # Wait for the result message.
            deadline = time.monotonic() + _JOB_TIMEOUT
            while time.monotonic() < deadline:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=5.0,
                )
                if message and message["type"] == "message":
                    return json.loads(message["data"])

            raise TimeoutError(f"No result for job {job_id} within {_JOB_TIMEOUT}s")
        finally:
            await pubsub.unsubscribe(result_channel)
            await pubsub.aclose()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Patterns for extracting a GitHub repo reference from natural language.
_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)
_REPO_SHORTHAND_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b"
)
_BRANCH_RE = re.compile(
    r"\b(?:branch|on)\s+['\"]?([A-Za-z0-9_./-]+)['\"]?",
    re.IGNORECASE,
)


def _parse_coding_request(message: str) -> dict[str, str]:
    """Extract repo URL, branch, and task description from the user message."""
    repo_url = ""
    branch = _DEFAULT_BRANCH
    task = message

    # Try full GitHub URL first
    url_match = _GITHUB_URL_RE.search(message)
    if url_match:
        repo_slug = url_match.group(1).rstrip("/").removesuffix(".git")
        repo_url = f"https://github.com/{repo_slug}.git"
        # Remove the URL from the task description
        task = message[:url_match.start()] + message[url_match.end():]
    else:
        # Try shorthand (owner/repo)
        shorthand_match = _REPO_SHORTHAND_RE.search(message)
        if shorthand_match:
            slug = shorthand_match.group(1)
            # Avoid false positives — must look like a real repo slug
            if "/" in slug and not slug.startswith("/"):
                repo_url = f"https://github.com/{slug}.git"
                task = message[:shorthand_match.start()] + message[shorthand_match.end():]

    # Extract branch
    branch_match = _BRANCH_RE.search(message)
    if branch_match:
        branch = branch_match.group(1)
        task = task[:branch_match.start()] + task[branch_match.end():]

    # Clean up the task description
    task = re.sub(r"\s+", " ", task).strip()
    # Remove leading filler words
    task = re.sub(r"^(?:in|on|for|please|can you|could you)\s+", "", task, flags=re.IGNORECASE).strip()

    return {
        "repo_url": repo_url,
        "branch": branch,
        "task": task,
    }
