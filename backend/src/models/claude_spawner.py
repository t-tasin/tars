"""Claude Code headless subprocess spawner with MCP server support."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass

import structlog

from models import MCP_PROFILES

log = structlog.get_logger("claude_spawner")

# Environment variable names required by each MCP server
MCP_ENV_KEYS: dict[str, list[str]] = {
    "github": ["GITHUB_PAT"],
    "postgres": ["DATABASE_URL"],
    "brave-search": ["BRAVE_API_KEY"],
    "filesystem": [],
}


class ClaudeSpawnError(Exception):
    """Raised when Claude Code subprocess fails to execute."""


@dataclass
class ClaudeCodeResult:
    """Result from a Claude Code subprocess execution."""

    text: str
    success: bool
    duration_ms: int
    error: str | None = None
    cost_usd: float = 0.0
    session_id: str | None = None
    num_turns: int = 0


class ClaudeCodeSpawner:
    """Spawns Claude Code as a headless subprocess with MCP server support.

    Uses ``claude --print --output-format json`` to run Claude Code in
    non-interactive mode.  MCP profiles scope which MCP servers are
    available to the spawned process.
    """

    def __init__(self, claude_binary: str = "claude") -> None:
        self.claude_binary = claude_binary

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        prompt: str,
        *,
        mcp_profile: str | None = None,
        working_directory: str | None = None,
        timeout: int = 120,
        max_turns: int = 5,
    ) -> ClaudeCodeResult:
        """Spawn ``claude`` CLI as a headless subprocess and return the result.

        Parameters
        ----------
        prompt:
            The user prompt to send to Claude Code.
        mcp_profile:
            Key into :data:`MCP_PROFILES`.  When set, only the listed MCP
            servers' env vars are forwarded to the subprocess.
        working_directory:
            Working directory for the subprocess.  Defaults to ``/data/repos``.
        timeout:
            Maximum wall-clock seconds to wait before killing the process.
        max_turns:
            ``--max-turns`` value passed to the CLI (enables multi-turn MCP
            tool use).
        """
        cmd = self._build_command(prompt, mcp_profile=mcp_profile, max_turns=max_turns)
        env = self._build_env(mcp_profile)
        cwd = working_directory or "/data/repos"

        prompt_preview = prompt[:200] + ("…" if len(prompt) > 200 else "")
        log.info(
            "claude_spawn_start",
            prompt=prompt_preview,
            mcp_profile=mcp_profile,
            max_turns=max_turns,
            timeout=timeout,
        )

        start = time.monotonic()
        try:
            stdout, stderr, returncode = await asyncio.wait_for(
                self._run_subprocess(cmd, env=env, cwd=cwd),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("claude_spawn_timeout", duration_ms=duration_ms, timeout=timeout)
            return ClaudeCodeResult(
                text="",
                success=False,
                duration_ms=duration_ms,
                error=f"Claude Code timed out after {timeout}s",
            )
        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("claude_binary_not_found", binary=self.claude_binary)
            raise ClaudeSpawnError(
                f"Claude Code binary not found: {self.claude_binary!r}. "
                "Ensure the claude CLI is installed and on PATH."
            ) from None

        duration_ms = int((time.monotonic() - start) * 1000)

        if returncode != 0:
            log.error(
                "claude_spawn_failed",
                returncode=returncode,
                stderr=stderr[:500] if stderr else None,
                duration_ms=duration_ms,
            )
            return ClaudeCodeResult(
                text="",
                success=False,
                duration_ms=duration_ms,
                error=stderr or f"Non-zero exit code: {returncode}",
            )

        parsed = self._parse_output(stdout, duration_ms)
        log.info(
            "claude_spawn_success",
            duration_ms=duration_ms,
            cost_usd=parsed.cost_usd,
            num_turns=parsed.num_turns,
            session_id=parsed.session_id,
        )
        return parsed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_command(
        self,
        prompt: str,
        *,
        mcp_profile: str | None = None,
        max_turns: int = 5,
    ) -> list[str]:
        """Build the CLI argument list for the subprocess."""
        cmd = [
            self.claude_binary,
            "--print",
            "--output-format", "json",
            "--max-turns", str(max_turns),
        ]

        if mcp_profile and mcp_profile in MCP_PROFILES:
            allowed_servers = MCP_PROFILES[mcp_profile]
            for server in allowed_servers:
                cmd.extend(["--allowedTools", f"mcp__{server}__*"])

        cmd.extend(["--", prompt])
        return cmd

    def _build_env(self, mcp_profile: str | None) -> dict[str, str] | None:
        """Build environment variables, scoping MCP credentials by profile.

        Returns ``None`` (inherit full parent env) when no profile is set.
        When a profile *is* given, removes MCP env vars for servers not
        in the profile while keeping everything else (PATH, HOME, etc.).
        """
        if mcp_profile is None:
            return None

        env = dict(os.environ)

        allowed_servers = set(MCP_PROFILES.get(mcp_profile, []))
        for server, keys in MCP_ENV_KEYS.items():
            if server not in allowed_servers:
                for key in keys:
                    env.pop(key, None)

        return env

    async def _run_subprocess(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None,
        cwd: str,
    ) -> tuple[str, str, int]:
        """Execute the subprocess, return (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        return stdout, stderr, proc.returncode or 0

    def _parse_output(self, stdout: str, duration_ms: int) -> ClaudeCodeResult:
        """Parse JSON or plain-text output from ``claude --output-format json``."""
        if not stdout.strip():
            return ClaudeCodeResult(
                text="",
                success=True,
                duration_ms=duration_ms,
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            return ClaudeCodeResult(
                text=stdout.strip(),
                success=True,
                duration_ms=duration_ms,
            )

        # The JSON output from `claude --print --output-format json`
        # may contain `result`, `text`, or `content` (list of blocks).
        text = data.get("result", "") or data.get("text", "") or data.get("content", "")
        if isinstance(text, list):
            # Content blocks: [{"type": "text", "text": "..."}]
            text = "\n".join(
                block.get("text", "") for block in text if isinstance(block, dict)
            )

        return ClaudeCodeResult(
            text=text,
            success=True,
            duration_ms=duration_ms,
            cost_usd=float(data.get("cost_usd", 0.0)),
            session_id=data.get("session_id"),
            num_turns=int(data.get("num_turns", 0)),
        )
