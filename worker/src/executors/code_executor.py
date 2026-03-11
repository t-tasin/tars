"""Code executor — runs Claude Code in a sandboxed Docker container on Node 2.

Workflow:
    1. Receive job payload from the job processor (dispatched from Node 1)
    2. Clone the specified repo into a temp directory
    3. Inject CLAUDE.md for project context
    4. Spawn Claude Code inside a Docker container with GitHub + filesystem MCP
    5. Capture output: code changes (diff), test results, Claude's explanation
    6. Return results — the job processor publishes them back via Redis pub/sub

The container is ephemeral — created per-job and removed after completion.
No code is pushed from here; the backend agent decides whether to request
approval for push/PR actions (HC-02).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from .base import BaseExecutor

log = structlog.get_logger("tars.worker.executor.code")

# Docker image used for Claude Code execution.
_CLAUDE_CODE_IMAGE = "ghcr.io/tasin/tars-claude-runner:latest"

# Base path for temporary repo clones on Node 2.
_REPOS_BASE = Path("/data/repos")

# Path to a shared CLAUDE.md template injected into repos that lack one.
_CLAUDE_MD_PATH = Path("/data/config/CLAUDE.md")

# Maximum time (seconds) for the Docker container to run.
_CONTAINER_TIMEOUT = 240


class CodeExecutor(BaseExecutor):
    """Executes coding jobs in sandboxed Docker containers.

    Each job clones a repo, injects CLAUDE.md, runs Claude Code with MCP
    servers, collects the diff and test output, and returns the results.
    """

    EXECUTOR_TYPE = "code"

    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a single coding job end-to-end.

        Expected payload keys:
            repo_url (str): HTTPS clone URL for the repository.
            branch (str): Git branch to check out (default ``main``).
            task (str): Natural-language description of the coding task.
            github_pat (str): GitHub PAT for private repo access.
            timeout (int): Max seconds for Claude Code execution.
        """
        repo_url: str = payload["repo_url"]
        branch: str = payload.get("branch", "main")
        task: str = payload["task"]
        github_pat: str = payload.get("github_pat", "")
        timeout: int = payload.get("timeout", _CONTAINER_TIMEOUT)

        start = time.monotonic()
        _REPOS_BASE.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="tars-code-", dir=str(_REPOS_BASE)))

        log.info(
            "code_executor_start",
            job_id=job_id,
            repo_url=repo_url,
            branch=branch,
            task=task[:200],
        )

        try:
            # 1. Clone repo
            await self._clone_repo(repo_url, branch, work_dir, github_pat)

            # 2. Inject CLAUDE.md
            self._inject_claude_md(work_dir)

            # 3. Run Claude Code in Docker container
            claude_output = await self._run_claude_container(
                work_dir=work_dir,
                task=task,
                repo_url=repo_url,
                branch=branch,
                github_pat=github_pat,
                timeout=timeout,
            )

            # 4. Collect diff
            diff_summary = await self._collect_diff(work_dir)

            # 5. Collect test output (if tests were run)
            test_output = await self._collect_test_output(work_dir)

            # 6. List changed files
            files_changed = await self._list_changed_files(work_dir)

            # 7. Detect push/PR intent from Claude's output
            has_push = _detect_push_intent(claude_output)
            has_pr = _detect_pr_intent(claude_output)

            duration_ms = int((time.monotonic() - start) * 1000)

            log.info(
                "code_job_completed",
                job_id=job_id,
                files_changed=len(files_changed),
                has_push=has_push,
                has_pr=has_pr,
                duration_ms=duration_ms,
            )

            return {
                "status": "completed",
                "success": True,
                "job_id": job_id,
                "claude_output": claude_output,
                "diff_summary": diff_summary,
                "test_output": test_output,
                "files_changed": files_changed,
                "has_push": has_push,
                "has_pr": has_pr,
                "duration_ms": duration_ms,
            }

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.exception("code_job_error", job_id=job_id)
            return {
                "status": "failed",
                "success": False,
                "job_id": job_id,
                "error": str(exc),
                "duration_ms": duration_ms,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Clone
    # ------------------------------------------------------------------

    async def _clone_repo(
        self,
        repo_url: str,
        branch: str,
        work_dir: Path,
        github_pat: str,
    ) -> None:
        """Shallow-clone the repository into ``work_dir/repo``."""
        authed_url = repo_url
        if github_pat and "github.com" in repo_url:
            authed_url = repo_url.replace(
                "https://github.com/",
                f"https://x-access-token:{github_pat}@github.com/",
            )

        cmd = [
            "git", "clone",
            "--depth", "1",
            "--branch", branch,
            "--single-branch",
            authed_url,
            str(work_dir / "repo"),
        ]

        log.info("code_clone_start", repo_url=repo_url, branch=branch)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace") if stderr else "unknown"
            # Scrub PAT from error output.
            if github_pat:
                err = err.replace(github_pat, "***")
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {err}")

        log.info("code_clone_complete", work_dir=str(work_dir / "repo"))

    # ------------------------------------------------------------------
    # CLAUDE.md injection
    # ------------------------------------------------------------------

    def _inject_claude_md(self, work_dir: Path) -> None:
        """Copy the shared CLAUDE.md into the cloned repo if not already present."""
        repo_dir = work_dir / "repo"
        target = repo_dir / "CLAUDE.md"

        if target.exists():
            log.debug("claude_md_already_present", path=str(target))
            return

        if _CLAUDE_MD_PATH.exists():
            shutil.copy2(_CLAUDE_MD_PATH, target)
            log.info("claude_md_injected", path=str(target))
        else:
            log.warning("claude_md_template_missing", expected=str(_CLAUDE_MD_PATH))

    # ------------------------------------------------------------------
    # Docker execution
    # ------------------------------------------------------------------

    async def _run_claude_container(
        self,
        *,
        work_dir: Path,
        task: str,
        repo_url: str,
        branch: str,
        github_pat: str,
        timeout: int,
    ) -> str:
        """Run Claude Code inside a Docker container and return its text output.

        The container:
        - Mounts the cloned repo at /workspace
        - Runs with no network access (all context is local)
        - Is resource-limited (2GB RAM, 2 CPUs)
        - Uses --allowedTools to scope MCP access to GitHub + filesystem
        """
        repo_dir = work_dir / "repo"

        prompt = (
            f"You are working in the repo cloned at /workspace.\n"
            f"Repository: {repo_url} (branch: {branch})\n\n"
            f"Task: {task}\n\n"
            f"Instructions:\n"
            f"- Read CLAUDE.md first if it exists for project context.\n"
            f"- Use MCP tools (GitHub, filesystem) to explore and modify code.\n"
            f"- Make the minimal changes needed to accomplish the task.\n"
            f"- Run tests if a test suite exists.\n"
            f"- Do NOT push code or create PRs — just make local changes.\n"
            f"- Summarise what you changed and why."
        )

        container_name = f"tars-code-{work_dir.name}"

        # All arguments are passed as a list — no shell interpolation.
        cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", "none",
            "-v", f"{repo_dir}:/workspace:rw",
            "-w", "/workspace",
            "--memory", "2g",
            "--cpus", "2",
        ]

        if github_pat:
            cmd.extend(["-e", f"GITHUB_PAT={github_pat}"])

        cmd.extend([
            _CLAUDE_CODE_IMAGE,
            "claude", "--print",
            "--output-format", "json",
            "--max-turns", "10",
            "--allowedTools", "mcp__github__*",
            "--allowedTools", "mcp__filesystem__*",
            "--", prompt,
        ])

        log.info(
            "code_container_start",
            container=container_name,
            image=_CLAUDE_CODE_IMAGE,
            timeout=timeout,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Kill the container if it exceeds the timeout.
            kill_proc = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.communicate()
            raise RuntimeError(f"Claude Code container timed out after {timeout}s")

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            log.error(
                "code_container_failed",
                returncode=proc.returncode,
                stderr=stderr[:500],
            )
            raise RuntimeError(
                f"Claude Code container exited with code {proc.returncode}: "
                f"{stderr[:300]}"
            )

        return _parse_claude_output(stdout)

    # ------------------------------------------------------------------
    # Post-execution collection
    # ------------------------------------------------------------------

    async def _collect_diff(self, work_dir: Path) -> str:
        """Run ``git diff`` in the cloned repo to capture all changes."""
        repo_dir = work_dir / "repo"

        # Stat summary
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        stat = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

        # Full patch (truncated for large diffs)
        proc2 = await asyncio.create_subprocess_exec(
            "git", "diff",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout2, _ = await proc2.communicate()
        full_diff = stdout2.decode("utf-8", errors="replace") if stdout2 else ""

        max_diff = 10_000
        if len(full_diff) > max_diff:
            full_diff = full_diff[:max_diff] + f"\n... (truncated, {len(full_diff)} total chars)"

        # Also include untracked files that Claude may have created.
        proc3 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout3, _ = await proc3.communicate()
        untracked = stdout3.decode("utf-8", errors="replace").strip() if stdout3 else ""

        parts = []
        if stat:
            parts.append(stat)
        if full_diff:
            parts.append(full_diff)
        if untracked:
            parts.append(f"Untracked files:\n{untracked}")

        return "\n\n".join(parts)

    async def _collect_test_output(self, work_dir: Path) -> str:
        """Check for test output files left by Claude Code."""
        repo_dir = work_dir / "repo"
        for candidate in [
            repo_dir / "test-output.txt",
            repo_dir / ".tars-test-output",
        ]:
            if candidate.exists():
                text = candidate.read_text(errors="replace")
                if len(text) > 5000:
                    text = text[:5000] + "\n... (truncated)"
                return text
        return ""

    async def _list_changed_files(self, work_dir: Path) -> list[str]:
        """List files modified or newly created in the working tree."""
        repo_dir = work_dir / "repo"

        # Modified files
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        modified = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

        # Untracked (new) files
        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout2, _ = await proc2.communicate()
        untracked = stdout2.decode("utf-8", errors="replace").strip() if stdout2 else ""

        files = set()
        for raw in (modified, untracked):
            for f in raw.splitlines():
                f = f.strip()
                if f:
                    files.add(f)
        return sorted(files)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_claude_output(stdout: str) -> str:
    """Extract the text response from Claude Code's JSON output."""
    if not stdout.strip():
        return ""

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip()

    text = data.get("result", "") or data.get("text", "") or data.get("content", "")
    if isinstance(text, list):
        text = "\n".join(
            block.get("text", "") for block in text if isinstance(block, dict)
        )
    return text


def _detect_push_intent(claude_output: str) -> bool:
    """Check if Claude's output indicates it attempted to push code."""
    indicators = ["git push", "pushed to", "force push"]
    lower = claude_output.lower()
    return any(ind in lower for ind in indicators)


def _detect_pr_intent(claude_output: str) -> bool:
    """Check if Claude's output indicates it created or wants to create a PR."""
    indicators = [
        "pull request",
        "created pr",
        "gh pr create",
        "open a pr",
        "merge request",
    ]
    lower = claude_output.lower()
    return any(ind in lower for ind in indicators)
