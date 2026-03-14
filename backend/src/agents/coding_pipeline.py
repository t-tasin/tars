"""Multi-agent coding pipeline — plan, execute, review, test.

Orchestrates multiple Claude Code subprocess invocations to handle complex
coding tasks:

    Phase 0: Prepare repo (clone/fetch, create working branch)
    Phase 1: Plan (Claude reads the repo and outputs a task breakdown)
    Phase 2: Execute (parallel Claude workers implement each task)
    Phase 3: Review (Claude reviews the full diff against the plan)
    Phase 4: Test (deterministic pytest run, no AI)

All code-push and PR-creation actions still require explicit approval (HC-02).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine
from uuid import uuid4

import structlog

from config import get_settings
from models.claude_spawner import ClaudeCodeSpawner

log = structlog.get_logger(__name__)

NotifyCallback = Callable[[str, str], Coroutine[Any, Any, None]] | None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PipelineTask:
    """A single task within a coding plan."""

    id: int
    description: str
    files: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Outcome of a full pipeline run."""

    success: bool
    plan_summary: str = ""
    tasks_completed: int = 0
    tasks_total: int = 0
    review_passed: bool = False
    tests_passed: bool = False
    branch_name: str = ""
    diff_stats: str = ""
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class CodingPipeline:
    """Multi-phase coding pipeline using Claude Code CLI."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._spawner = ClaudeCodeSpawner()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        repo_url: str,
        task_description: str,
        branch: str = "main",
        notify_callback: NotifyCallback = None,
    ) -> PipelineResult:
        """Execute the full plan-execute-review-test pipeline."""
        work_branch = f"tars/{uuid4().hex[:8]}"
        repo_path = self._resolve_repo_path(repo_url)

        log.info(
            "pipeline_start",
            repo_url=repo_url,
            branch=branch,
            work_branch=work_branch,
            task=task_description[:200],
        )

        # Phase 0 — Prepare repo
        try:
            await self._notify(notify_callback, "phase0", "Preparing repository...")
            await self._prepare_repo(repo_url, repo_path, branch, work_branch)
        except Exception as exc:
            log.error("pipeline_phase0_failed", error=str(exc))
            return PipelineResult(
                success=False,
                branch_name=work_branch,
                error=f"Repo preparation failed: {exc}",
            )

        # Phase 1 — Plan
        try:
            await self._notify(notify_callback, "phase1", "Planning tasks...")
            plan, plan_summary, test_commands = await self._phase_plan(
                repo_path, task_description,
            )
        except Exception as exc:
            log.error("pipeline_phase1_failed", error=str(exc))
            return PipelineResult(
                success=False,
                branch_name=work_branch,
                error=f"Planning phase failed: {exc}",
            )

        if not plan:
            return PipelineResult(
                success=False,
                branch_name=work_branch,
                plan_summary=plan_summary,
                error="Planner produced no tasks.",
            )

        tasks_total = len(plan)

        # Phase 2 — Execute
        try:
            await self._notify(
                notify_callback, "phase2",
                f"Executing {tasks_total} task(s)...",
            )
            tasks_completed = await self._phase_execute(
                repo_path, plan, plan_summary,
            )
        except Exception as exc:
            log.error("pipeline_phase2_failed", error=str(exc))
            return PipelineResult(
                success=False,
                branch_name=work_branch,
                plan_summary=plan_summary,
                tasks_total=tasks_total,
                error=f"Execution phase failed: {exc}",
            )

        # Phase 3 — Review
        try:
            await self._notify(notify_callback, "phase3", "Reviewing changes...")
            review_passed = await self._phase_review(
                repo_path, branch, plan_summary, task_description,
            )
        except Exception as exc:
            log.error("pipeline_phase3_failed", error=str(exc))
            review_passed = False

        # Phase 4 — Test
        tests_passed = False
        if test_commands:
            try:
                await self._notify(notify_callback, "phase4", "Running tests...")
                tests_passed = await self._phase_test(
                    repo_path, test_commands,
                )
            except Exception as exc:
                log.error("pipeline_phase4_failed", error=str(exc))
                tests_passed = False
        else:
            # No test commands specified — skip
            tests_passed = True

        diff_stats = await self._get_diff_stats(repo_path, branch)

        result = PipelineResult(
            success=True,
            plan_summary=plan_summary,
            tasks_completed=tasks_completed,
            tasks_total=tasks_total,
            review_passed=review_passed,
            tests_passed=tests_passed,
            branch_name=work_branch,
            diff_stats=diff_stats,
        )

        log.info(
            "pipeline_complete",
            success=True,
            tasks_completed=tasks_completed,
            tasks_total=tasks_total,
            review_passed=review_passed,
            tests_passed=tests_passed,
            branch=work_branch,
        )

        return result

    # ------------------------------------------------------------------
    # Phase 1 — Plan
    # ------------------------------------------------------------------

    async def _phase_plan(
        self,
        repo_path: str,
        task_description: str,
    ) -> tuple[list[PipelineTask], str, list[str]]:
        """Ask Claude to read the repo and produce a JSON task plan."""
        prompt = (
            "You are a senior software engineer planning a coding task.\n\n"
            f"Task: {task_description}\n\n"
            "Read the repository structure and relevant files, then output a "
            "JSON plan with this exact format (no other text):\n"
            "```json\n"
            '{"summary": "brief plan summary", '
            '"tasks": [{"id": 1, "description": "what to do", '
            '"files": ["path/to/file.py"], "depends_on": []}], '
            '"test_commands": ["pytest tests/ -v --tb=short"]}\n'
            "```\n\n"
            "Rules:\n"
            "- Break the work into small, focused tasks\n"
            "- Specify which files each task modifies\n"
            "- Use depends_on to order tasks that must be sequential\n"
            "- Independent tasks should have empty depends_on so they can run in parallel\n"
            "- Include test commands if the project has tests\n"
        )

        settings = self._settings
        max_turns = getattr(settings, "coding_max_plan_turns", 10)

        result = await self._spawner.execute(
            prompt,
            mcp_profile="coding",
            working_directory=repo_path,
            timeout=120,
            max_turns=max_turns,
        )

        if not result.success:
            raise RuntimeError(f"Plan phase failed: {result.error}")

        parsed = self._parse_json(result.text)
        summary = parsed.get("summary", "")
        test_commands = parsed.get("test_commands", [])

        tasks: list[PipelineTask] = []
        for t in parsed.get("tasks", []):
            tasks.append(PipelineTask(
                id=int(t.get("id", len(tasks) + 1)),
                description=t.get("description", ""),
                files=t.get("files", []),
                depends_on=t.get("depends_on", []),
            ))

        log.info(
            "pipeline_plan_complete",
            summary=summary[:200],
            num_tasks=len(tasks),
            test_commands=test_commands,
        )

        return tasks, summary, test_commands

    # ------------------------------------------------------------------
    # Phase 2 — Execute
    # ------------------------------------------------------------------

    async def _phase_execute(
        self,
        repo_path: str,
        tasks: list[PipelineTask],
        plan_summary: str,
    ) -> int:
        """Execute tasks in topologically sorted batches."""
        batches = self._topological_batches(tasks)
        completed = 0

        settings = self._settings
        max_turns = getattr(settings, "coding_max_worker_turns", 15)

        for batch_idx, batch in enumerate(batches):
            log.info(
                "pipeline_execute_batch",
                batch=batch_idx + 1,
                total_batches=len(batches),
                tasks=[t.id for t in batch],
            )

            coros = [
                self._execute_single_task(repo_path, task, plan_summary, max_turns)
                for task in batch
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            for task, res in zip(batch, results):
                if isinstance(res, Exception):
                    log.error(
                        "pipeline_task_failed",
                        task_id=task.id,
                        error=str(res),
                    )
                elif res:
                    completed += 1
                else:
                    log.warning("pipeline_task_unsuccessful", task_id=task.id)

        return completed

    async def _execute_single_task(
        self,
        repo_path: str,
        task: PipelineTask,
        plan_summary: str,
        max_turns: int,
    ) -> bool:
        """Execute a single pipeline task via Claude Code."""
        files_hint = ", ".join(task.files) if task.files else "determine from context"

        prompt = (
            "You are implementing part of a larger plan.\n\n"
            f"Overall plan: {plan_summary}\n\n"
            f"Your task (#{task.id}): {task.description}\n\n"
            f"Files to modify: {files_hint}\n\n"
            "Instructions:\n"
            "- Implement ONLY this specific task\n"
            "- Do not modify files outside your scope unless necessary\n"
            "- Write clean, well-documented code\n"
            "- Stage and commit your changes with a descriptive message\n"
        )

        result = await self._spawner.execute(
            prompt,
            mcp_profile="coding",
            working_directory=repo_path,
            timeout=180,
            max_turns=max_turns,
        )

        if not result.success:
            log.error(
                "pipeline_task_execution_failed",
                task_id=task.id,
                error=result.error,
            )
            return False

        log.info(
            "pipeline_task_complete",
            task_id=task.id,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )
        return True

    # ------------------------------------------------------------------
    # Phase 3 — Review
    # ------------------------------------------------------------------

    async def _phase_review(
        self,
        repo_path: str,
        base_branch: str,
        plan_summary: str,
        task_description: str,
    ) -> bool:
        """Ask Claude to review the diff against the plan."""
        settings = self._settings
        max_turns = getattr(settings, "coding_max_review_turns", 5)
        max_retries = getattr(settings, "coding_max_retries", 2)

        diff_stat = await self._run_git(
            repo_path, "diff", f"{base_branch}...HEAD", "--stat",
        )

        prompt = (
            "You are a senior code reviewer.\n\n"
            f"Original task: {task_description}\n"
            f"Plan summary: {plan_summary}\n\n"
            f"Diff stats:\n```\n{diff_stat}\n```\n\n"
            "Review the changes (use git diff to see details) and output JSON:\n"
            "```json\n"
            '{"approved": true, "issues": []}\n'
            "```\n\n"
            "Or if there are problems:\n"
            "```json\n"
            '{"approved": false, "issues": ["description of issue 1", ...]}\n'
            "```\n\n"
            "Check for: missing plan items, security issues, bugs, style problems.\n"
        )

        result = await self._spawner.execute(
            prompt,
            mcp_profile="coding",
            working_directory=repo_path,
            timeout=120,
            max_turns=max_turns,
        )

        if not result.success:
            log.error("pipeline_review_failed", error=result.error)
            return False

        review = self._parse_json(result.text)

        if review.get("approved", False):
            log.info("pipeline_review_approved")
            return True

        issues = review.get("issues", [])
        log.warning("pipeline_review_rejected", issues=issues)

        # One retry: fix issues, then review again
        if max_retries > 0:
            fix_prompt = (
                "The code review found these issues. Fix them:\n\n"
                + "\n".join(f"- {issue}" for issue in issues)
                + "\n\nCommit the fixes."
            )

            fix_result = await self._spawner.execute(
                fix_prompt,
                mcp_profile="coding",
                working_directory=repo_path,
                timeout=180,
                max_turns=max_turns,
            )

            if fix_result.success:
                # Re-review
                re_result = await self._spawner.execute(
                    prompt,
                    mcp_profile="coding",
                    working_directory=repo_path,
                    timeout=120,
                    max_turns=max_turns,
                )

                if re_result.success:
                    re_review = self._parse_json(re_result.text)
                    if re_review.get("approved", False):
                        log.info("pipeline_review_approved_on_retry")
                        return True

            log.warning("pipeline_review_still_rejected_after_retry")

        return False

    # ------------------------------------------------------------------
    # Phase 4 — Test
    # ------------------------------------------------------------------

    async def _phase_test(
        self,
        repo_path: str,
        test_commands: list[str],
    ) -> bool:
        """Run test commands. Retry once with Claude fix if they fail."""
        settings = self._settings
        max_retries = getattr(settings, "coding_max_retries", 2)

        for attempt in range(1 + min(max_retries, 1)):
            all_passed = True
            failure_output = ""

            for cmd in test_commands:
                try:
                    output = await asyncio.to_thread(
                        subprocess.check_output,
                        cmd,
                        shell=True,
                        cwd=repo_path,
                        stderr=subprocess.STDOUT,
                        timeout=120,
                    )
                    log.info(
                        "pipeline_test_passed",
                        command=cmd,
                        attempt=attempt + 1,
                    )
                except subprocess.CalledProcessError as exc:
                    all_passed = False
                    failure_output = (
                        exc.output.decode("utf-8", errors="replace")
                        if exc.output else str(exc)
                    )
                    log.warning(
                        "pipeline_test_failed",
                        command=cmd,
                        attempt=attempt + 1,
                        output=failure_output[:500],
                    )
                    break
                except subprocess.TimeoutExpired:
                    all_passed = False
                    failure_output = f"Test command timed out: {cmd}"
                    log.warning("pipeline_test_timeout", command=cmd)
                    break

            if all_passed:
                return True

            # Try to fix with Claude (only on first failure)
            if attempt == 0 and failure_output:
                fix_prompt = (
                    "Tests are failing. Fix the code so the tests pass.\n\n"
                    f"Test output:\n```\n{failure_output[:3000]}\n```\n\n"
                    "Fix the issues and commit."
                )

                max_worker_turns = getattr(settings, "coding_max_worker_turns", 15)
                await self._spawner.execute(
                    fix_prompt,
                    mcp_profile="coding",
                    working_directory=repo_path,
                    timeout=180,
                    max_turns=max_worker_turns,
                )

        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _topological_batches(
        self,
        tasks: list[PipelineTask],
    ) -> list[list[PipelineTask]]:
        """Group tasks into batches respecting depends_on ordering.

        Tasks with no unresolved dependencies are grouped into the same
        batch and can run in parallel.  Returns a list of batches in
        execution order.
        """
        task_map = {t.id: t for t in tasks}
        remaining = set(task_map.keys())
        completed: set[int] = set()
        batches: list[list[PipelineTask]] = []

        while remaining:
            batch_ids = [
                tid for tid in remaining
                if all(dep in completed for dep in task_map[tid].depends_on)
            ]

            if not batch_ids:
                # Circular dependency — force remaining into one batch
                batch_ids = list(remaining)

            batches.append([task_map[tid] for tid in batch_ids])
            completed.update(batch_ids)
            remaining -= set(batch_ids)

        return batches

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Extract JSON from text that may contain markdown fences or prose."""
        if not text:
            return {"approved": False, "issues": ["Empty response"]}

        # Try direct parse first
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fence
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding JSON object embedded in text
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", stripped, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback
        return {"approved": False, "issues": [f"Could not parse response: {text[:200]}"]}

    def _resolve_repo_path(self, repo_url: str) -> str:
        """Convert a repo URL or shorthand to a local filesystem path."""
        settings = self._settings
        base_path = getattr(settings, "coding_repo_base_path", "/data/repos")

        # Extract owner/name from URL
        match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", repo_url)
        if match:
            slug = match.group(1)
        else:
            slug = repo_url.replace("/", "_").replace(":", "_")

        return os.path.join(base_path, slug.replace("/", "_"))

    async def _prepare_repo(
        self,
        repo_url: str,
        repo_path: str,
        branch: str,
        work_branch: str,
    ) -> None:
        """Clone or update the repo and create the working branch."""
        if os.path.isdir(os.path.join(repo_path, ".git")):
            # Repo exists — fetch and checkout base branch
            await self._run_git(repo_path, "fetch", "origin")
            await self._run_git(repo_path, "checkout", branch)
            await self._run_git(repo_path, "pull", "origin", branch)
        else:
            # Clone fresh
            await asyncio.to_thread(
                subprocess.check_call,
                ["git", "clone", "--branch", branch, repo_url, repo_path],
                timeout=120,
            )

        # Create working branch
        await self._run_git(repo_path, "checkout", "-b", work_branch)

        log.info(
            "pipeline_repo_prepared",
            repo_path=repo_path,
            branch=branch,
            work_branch=work_branch,
        )

    async def _get_diff_stats(self, repo_path: str, base_branch: str) -> str:
        """Return git diff --shortstat for the working branch vs base."""
        try:
            return await self._run_git(
                repo_path, "diff", f"{base_branch}...HEAD", "--shortstat",
            )
        except Exception:
            return ""

    async def _run_git(self, repo_path: str, *args: str) -> str:
        """Run a git command in the repo directory and return stdout."""
        cmd = ["git", "-C", repo_path, *args]
        output = await asyncio.to_thread(
            subprocess.check_output,
            cmd,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        return output.decode("utf-8", errors="replace").strip()

    @staticmethod
    async def _notify(
        callback: NotifyCallback,
        phase: str,
        message: str,
    ) -> None:
        """Send a progress notification if a callback is provided."""
        if callback is not None:
            try:
                await callback(phase, message)
            except Exception:
                pass  # Notification failures should not halt the pipeline
