"""Coding/DevOps agent — handles code tasks via local pipeline or Node 2.

Simple tasks are dispatched to Node 2 via the shared :class:`JobQueue`
(sorted-set enqueue on ``tars:jobs:queue``; results on
``tars:jobs:results``).  Complex tasks (refactors, new features,
migrations, etc.) use the local multi-agent coding pipeline that plans,
executes, reviews, and tests via multiple Claude Code CLI invocations.

Workflow (simple):
    1. Parse the coding request (repo URL, branch, task description)
    2. Dispatch a ``code`` job via JobQueue
    3. Node 2 spins up a Docker container, clones the repo, injects CLAUDE.md
    4. Claude Code runs with MCP servers (GitHub + filesystem)
    5. Results returned on the shared results pub/sub channel
    6. Any code push / PR creation requires explicit approval (HC-02)

Workflow (complex — pipeline):
    1. Parse the coding request
    2. Run CodingPipeline locally (plan → execute → review → test)
    3. Results returned as approval-gated AgentResult (HC-02)
"""

from __future__ import annotations

import re
from uuid import uuid4

import structlog
from shared.constants import AutonomyClass

from agents.base import AgentContext, AgentResult, BaseAgent
from agents.coding_pipeline import CodingPipeline
from config import get_settings
from integrations.job_queue import JobQueue, get_job_queue

log = structlog.get_logger()

# Keywords that indicate a complex task requiring the multi-agent pipeline.
_COMPLEX_KEYWORDS = (
    "refactor",
    "implement",
    "add feature",
    "build",
    "create",
    "migrate",
    "redesign",
    "rewrite",
    "add oauth",
    "add auth",
    "add api",
    "new endpoint",
)


def _is_complex_task(description: str) -> bool:
    """Return True if the task description suggests a complex coding job."""
    lower = description.lower()
    return any(kw in lower for kw in _COMPLEX_KEYWORDS)


# How long to wait for a code execution job to complete (seconds).
_JOB_TIMEOUT = 300

# Worker executor type for coding jobs (CodeExecutor.EXECUTOR_TYPE).
_JOB_TYPE = "code"

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
    autonomy_class = AutonomyClass.WRITE_WORLD

    def __init__(self, job_queue: JobQueue | None = None) -> None:
        self._settings = get_settings()
        self._job_queue = job_queue

    # ------------------------------------------------------------------
    # JobQueue (lazy)
    # ------------------------------------------------------------------

    def _get_queue(self) -> JobQueue:
        """Return a shared :class:`JobQueue` (lazy-initialised)."""
        if self._job_queue is None:
            self._job_queue = get_job_queue(self._settings.redis_url)
        return self._job_queue

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

        # Route complex tasks to the multi-agent pipeline
        if repo_url and task and _is_complex_task(task):
            return await self._run_pipeline(repo_url, task, branch, context)

        if not repo_url:
            return AgentResult(
                autonomy_class=self.autonomy_class,
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
                autonomy_class=self.autonomy_class,
                success=False,
                text="What would you like me to do in this repo? Please describe the task.",
                error="no_task",
            )

        # 2. Build job payload (executor reads: repo_url, branch, task,
        #    github_pat, timeout — see worker CodeExecutor).
        job_id = str(uuid4())
        payload = {
            "repo_url": repo_url,
            "branch": branch,
            "task": task,
            "github_pat": self._settings.github_pat,
            "mcp_profile": "coding",
            "max_turns": 10,
            "timeout": _JOB_TIMEOUT - 30,  # leave margin for cleanup
        }

        # 3. Dispatch via JobQueue (ZADD tars:jobs:queue).
        log.info(
            "coding_job_dispatching",
            job_id=job_id,
            repo_url=repo_url,
            branch=branch,
            task=task[:200],
        )

        try:
            message = await self._get_queue().enqueue_and_wait(
                _JOB_TYPE,
                payload,
                priority="normal",
                timeout=_JOB_TIMEOUT,
                job_id=job_id,
            )
        except TimeoutError:
            log.error("coding_job_timeout", job_id=job_id, timeout=_JOB_TIMEOUT)
            return AgentResult(
                autonomy_class=self.autonomy_class,
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
                autonomy_class=self.autonomy_class,
                success=False,
                text="Something went wrong dispatching the coding task. Please try again.",
                error="dispatch_error",
            )

        # 4. Process result.  The worker wraps the executor's output as
        # ``{"job_id", "type", "result", "duration_ms"}``.
        result = message.get("result") or {}
        if not result.get("success"):
            error_msg = result.get("error", "unknown error")
            log.error("coding_job_failed", job_id=job_id, error=error_msg)
            return AgentResult(
                autonomy_class=self.autonomy_class,
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

            approval_title = f"Push code to {branch}" if has_push_action else f"Create PR for: {task[:80]}"

            log.info(
                "coding_job_approval_required",
                job_id=job_id,
                action_type=action_type,
                files_changed=len(files_changed),
            )

            return AgentResult(
                autonomy_class=self.autonomy_class,
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
            autonomy_class=self.autonomy_class,
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
    # Multi-agent pipeline (complex tasks)
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        repo_url: str,
        task: str,
        branch: str,
        context: AgentContext,
    ) -> AgentResult:
        """Run the multi-agent coding pipeline for complex tasks."""
        log.info(
            "coding_pipeline_start",
            repo_url=repo_url,
            branch=branch,
            task=task[:200],
        )

        pipeline = CodingPipeline()

        # Build a notify callback for progress updates
        async def _notify(phase: str, message: str) -> None:
            log.info("coding_pipeline_progress", phase=phase, message=message)

        try:
            result = await pipeline.run(
                repo_url=repo_url,
                task_description=task,
                branch=branch,
                notify_callback=_notify,
            )
        except Exception:
            log.exception("coding_pipeline_error")
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text="The coding pipeline encountered an unexpected error.",
                error="pipeline_error",
            )

        if not result.success:
            log.error("coding_pipeline_failed", error=result.error)
            return AgentResult(
                autonomy_class=self.autonomy_class,
                success=False,
                text=f"The coding pipeline failed: {result.error}",
                error="pipeline_failed",
                data={"plan_summary": result.plan_summary},
            )

        # Pipeline succeeded — require approval before any push/PR (HC-02)
        preview = {
            "repo": repo_url,
            "branch": branch,
            "work_branch": result.branch_name,
            "task": task,
            "plan_summary": result.plan_summary,
            "tasks_completed": result.tasks_completed,
            "tasks_total": result.tasks_total,
            "review_passed": result.review_passed,
            "tests_passed": result.tests_passed,
            "diff_stats": result.diff_stats,
        }

        log.info(
            "coding_pipeline_complete",
            branch=result.branch_name,
            tasks_completed=result.tasks_completed,
            tasks_total=result.tasks_total,
        )

        return AgentResult(
            autonomy_class=self.autonomy_class,
            success=True,
            text=(
                f"Pipeline completed: {result.tasks_completed}/{result.tasks_total} "
                f"tasks done. Review {'passed' if result.review_passed else 'failed'}. "
                f"Tests {'passed' if result.tests_passed else 'failed'}.\n\n"
                f"**Plan:** {result.plan_summary}\n"
                f"**Branch:** `{result.branch_name}`\n"
                f"**Changes:** {result.diff_stats}"
            ),
            content_type="approval",
            has_side_effects=True,
            action_type="create_pr",
            approval_title=f"Create PR for: {task[:80]}",
            preview=preview,
            data={
                "model": "claude_code",
                "pipeline": True,
            },
        )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Patterns for extracting a GitHub repo reference from natural language.
_GITHUB_URL_RE = re.compile(r"(?:https?://)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
_REPO_SHORTHAND_RE = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")
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
        task = message[: url_match.start()] + message[url_match.end() :]
    else:
        # Try shorthand (owner/repo)
        shorthand_match = _REPO_SHORTHAND_RE.search(message)
        if shorthand_match:
            slug = shorthand_match.group(1)
            # Avoid false positives — must look like a real repo slug
            if "/" in slug and not slug.startswith("/"):
                repo_url = f"https://github.com/{slug}.git"
                task = message[: shorthand_match.start()] + message[shorthand_match.end() :]

    # Extract branch
    branch_match = _BRANCH_RE.search(message)
    if branch_match:
        branch = branch_match.group(1)
        task = task[: branch_match.start()] + task[branch_match.end() :]

    # Clean up the task description
    task = re.sub(r"\s+", " ", task).strip()
    # Remove leading filler words
    task = re.sub(r"^(?:in|on|for|please|can you|could you)\s+", "", task, flags=re.IGNORECASE).strip()

    return {
        "repo_url": repo_url,
        "branch": branch,
        "task": task,
    }
