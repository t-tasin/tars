"""Tests for the Claude Code headless subprocess spawner."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import MCP_PROFILES
from models.claude_spawner import (
    ClaudeCodeResult,
    ClaudeCodeSpawner,
    ClaudeSpawnError,
    MCP_ENV_KEYS,
)


@pytest.fixture
def spawner() -> ClaudeCodeSpawner:
    return ClaudeCodeSpawner()


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_basic_command(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("Hello world", max_turns=5)
        assert cmd[:6] == ["claude", "--print", "--output-format", "json", "--max-turns", "5"]
        assert cmd[-2:] == ["--", "Hello world"]

    def test_custom_max_turns(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("test", max_turns=10)
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "10"

    def test_mcp_profile_coding(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("fix bug", mcp_profile="coding", max_turns=5)
        allowed = [arg for i, arg in enumerate(cmd) if i > 0 and cmd[i - 1] == "--allowedTools"]
        expected_servers = MCP_PROFILES["coding"]
        assert len(allowed) == len(expected_servers)
        for server in expected_servers:
            assert f"mcp__{server}__*" in allowed

    def test_mcp_profile_research(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("find papers", mcp_profile="research", max_turns=5)
        allowed = [arg for i, arg in enumerate(cmd) if i > 0 and cmd[i - 1] == "--allowedTools"]
        assert "mcp__brave-search__*" in allowed

    def test_unknown_mcp_profile_ignored(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("test", mcp_profile="nonexistent", max_turns=5)
        assert "--allowedTools" not in cmd

    def test_no_mcp_profile(self, spawner: ClaudeCodeSpawner) -> None:
        cmd = spawner._build_command("test", mcp_profile=None, max_turns=5)
        assert "--allowedTools" not in cmd

    def test_custom_binary(self) -> None:
        spawner = ClaudeCodeSpawner(claude_binary="/usr/local/bin/claude")
        cmd = spawner._build_command("test", max_turns=3)
        assert cmd[0] == "/usr/local/bin/claude"

    def test_prompt_with_special_characters(self, spawner: ClaudeCodeSpawner) -> None:
        prompt = 'Fix the "bug" in file.py && run tests'
        cmd = spawner._build_command(prompt, max_turns=5)
        # Prompt is passed as a single argument after --, safe from shell injection
        assert cmd[-1] == prompt


# ---------------------------------------------------------------------------
# Environment building
# ---------------------------------------------------------------------------


class TestBuildEnv:
    def test_no_profile_returns_none(self, spawner: ClaudeCodeSpawner) -> None:
        assert spawner._build_env(None) is None

    @patch.dict("os.environ", {"GITHUB_PAT": "ghp_secret", "DATABASE_URL": "pg://...", "BRAVE_API_KEY": "brv_key", "PATH": "/usr/bin"})
    def test_coding_profile_removes_brave_key(self, spawner: ClaudeCodeSpawner) -> None:
        env = spawner._build_env("coding")
        assert env is not None
        # coding profile includes github, filesystem, postgres
        assert env.get("GITHUB_PAT") == "ghp_secret"
        assert env.get("DATABASE_URL") == "pg://..."
        # brave-search is NOT in coding profile — key should be removed
        assert "BRAVE_API_KEY" not in env
        # PATH is always preserved
        assert env.get("PATH") == "/usr/bin"

    @patch.dict("os.environ", {"GITHUB_PAT": "ghp_secret", "DATABASE_URL": "pg://...", "BRAVE_API_KEY": "brv_key"})
    def test_research_profile_removes_github(self, spawner: ClaudeCodeSpawner) -> None:
        env = spawner._build_env("research")
        assert env is not None
        # research has brave-search + postgres
        assert env.get("BRAVE_API_KEY") == "brv_key"
        assert env.get("DATABASE_URL") == "pg://..."
        assert "GITHUB_PAT" not in env

    @patch.dict("os.environ", {"GITHUB_PAT": "ghp_secret"})
    def test_unknown_profile_returns_env_with_all_mcp_keys_removed(self, spawner: ClaudeCodeSpawner) -> None:
        env = spawner._build_env("nonexistent")
        assert env is not None
        # No servers allowed, so all MCP keys removed
        assert "GITHUB_PAT" not in env


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


class TestParseOutput:
    def test_json_with_result_field(self, spawner: ClaudeCodeSpawner) -> None:
        data = {
            "result": "Hello from Claude",
            "cost_usd": 0.015,
            "session_id": "sess_abc",
            "num_turns": 3,
        }
        result = spawner._parse_output(json.dumps(data), duration_ms=500)
        assert result.text == "Hello from Claude"
        assert result.success is True
        assert result.cost_usd == 0.015
        assert result.session_id == "sess_abc"
        assert result.num_turns == 3
        assert result.duration_ms == 500

    def test_json_with_content_blocks(self, spawner: ClaudeCodeSpawner) -> None:
        data = {
            "content": [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ],
        }
        result = spawner._parse_output(json.dumps(data), duration_ms=200)
        assert result.text == "Line one\nLine two"
        assert result.success is True

    def test_json_with_text_field(self, spawner: ClaudeCodeSpawner) -> None:
        data = {"text": "Simple response"}
        result = spawner._parse_output(json.dumps(data), duration_ms=100)
        assert result.text == "Simple response"

    def test_plain_text_fallback(self, spawner: ClaudeCodeSpawner) -> None:
        result = spawner._parse_output("Just some plain text", duration_ms=50)
        assert result.text == "Just some plain text"
        assert result.success is True

    def test_empty_output(self, spawner: ClaudeCodeSpawner) -> None:
        result = spawner._parse_output("", duration_ms=10)
        assert result.text == ""
        assert result.success is True

    def test_whitespace_only(self, spawner: ClaudeCodeSpawner) -> None:
        result = spawner._parse_output("   \n  ", duration_ms=10)
        assert result.text == ""
        assert result.success is True

    def test_missing_optional_fields_default(self, spawner: ClaudeCodeSpawner) -> None:
        data = {"result": "ok"}
        result = spawner._parse_output(json.dumps(data), duration_ms=100)
        assert result.cost_usd == 0.0
        assert result.session_id is None
        assert result.num_turns == 0


# ---------------------------------------------------------------------------
# Async execution
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_successful_execution(self, spawner: ClaudeCodeSpawner) -> None:
        json_output = json.dumps({"result": "Done!", "cost_usd": 0.01, "num_turns": 2})

        async def mock_run(cmd, *, env, cwd):
            return json_output, "", 0

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            result = await spawner.execute("Hello", working_directory="/tmp")

        assert result.success is True
        assert result.text == "Done!"
        assert result.cost_usd == 0.01

    @pytest.mark.asyncio
    async def test_non_zero_exit_code(self, spawner: ClaudeCodeSpawner) -> None:
        async def mock_run(cmd, *, env, cwd):
            return "", "Error: something failed", 1

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            result = await spawner.execute("test")

        assert result.success is False
        assert "something failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_timeout_handling(self, spawner: ClaudeCodeSpawner) -> None:
        async def mock_run(cmd, *, env, cwd):
            await asyncio.sleep(10)
            return "", "", 0

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            result = await spawner.execute("test", timeout=0)

        assert result.success is False
        assert "timed out" in (result.error or "")

    @pytest.mark.asyncio
    async def test_binary_not_found_raises(self, spawner: ClaudeCodeSpawner) -> None:
        async def mock_run(cmd, *, env, cwd):
            raise FileNotFoundError("No such file")

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            with pytest.raises(ClaudeSpawnError, match="binary not found"):
                await spawner.execute("test")

    @pytest.mark.asyncio
    async def test_mcp_profile_passed_to_command(self, spawner: ClaudeCodeSpawner) -> None:
        captured_cmd: list[str] = []

        async def mock_run(cmd, *, env, cwd):
            captured_cmd.extend(cmd)
            return json.dumps({"result": "ok"}), "", 0

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            await spawner.execute("fix code", mcp_profile="coding", working_directory="/tmp")

        assert "--allowedTools" in captured_cmd
        assert "mcp__github__*" in captured_cmd

    @pytest.mark.asyncio
    async def test_default_working_directory(self, spawner: ClaudeCodeSpawner) -> None:
        captured_cwd: list[str] = []

        async def mock_run(cmd, *, env, cwd):
            captured_cwd.append(cwd)
            return json.dumps({"result": "ok"}), "", 0

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            await spawner.execute("test")

        assert captured_cwd[0] == "/data/repos"

    @pytest.mark.asyncio
    async def test_custom_working_directory(self, spawner: ClaudeCodeSpawner) -> None:
        captured_cwd: list[str] = []

        async def mock_run(cmd, *, env, cwd):
            captured_cwd.append(cwd)
            return json.dumps({"result": "ok"}), "", 0

        with patch.object(spawner, "_run_subprocess", side_effect=mock_run):
            await spawner.execute("test", working_directory="/custom/path")

        assert captured_cwd[0] == "/custom/path"
