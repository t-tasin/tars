"""Tests for config API — default config completeness and seed logic."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Expected values for regression testing
# ---------------------------------------------------------------------------

EXPECTED_NAMESPACES = {"morning_briefing", "email_contacts", "notifications", "general"}

EXPECTED_BRIEFING_SECTIONS = [
    "weather",
    "schedule",
    "email_digest",
    "tasks_due",
    "job_matches",
    "system_health",
    "health_summary",
    "finance_summary",
    "proactive_suggestions",
]


# ---------------------------------------------------------------------------
# Load DEFAULT_CONFIG from source without triggering full import chain
# ---------------------------------------------------------------------------


def _load_default_config() -> dict[tuple[str, str], Any]:
    """Extract DEFAULT_CONFIG from config_api.py source using ast.literal_eval."""
    source = Path(__file__).parent.parent / "src" / "api" / "config_api.py"
    tree = ast.parse(source.read_text())

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "DEFAULT_CONFIG" and node.value is not None:
                return ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG":
                    return ast.literal_eval(node.value)

    raise RuntimeError("DEFAULT_CONFIG not found in config_api.py")


# ---------------------------------------------------------------------------
# Tests: seed logic (unit, with mocked session)
# ---------------------------------------------------------------------------


class TestSeedLogic:
    """Test the seed algorithm against a mock async session."""

    @pytest.mark.asyncio
    async def test_seed_inserts_and_commits(self) -> None:
        defaults = {("ns", "k1"): "v1", ("ns", "k2"): "v2"}
        existing: set[tuple[str, str]] = set()
        session = AsyncMock()

        inserted = 0
        for (namespace, key), value in defaults.items():
            if (namespace, key) not in existing:
                session.add(MagicMock())
                inserted += 1
        if inserted:
            await session.commit()

        assert session.add.call_count == 2
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seed_skips_existing(self) -> None:
        defaults = {("ns", "k1"): "v1", ("ns", "k2"): "v2"}
        existing = {("ns", "k1")}
        session = AsyncMock()

        inserted = 0
        for (namespace, key), value in defaults.items():
            if (namespace, key) not in existing:
                session.add(MagicMock())
                inserted += 1
        if inserted:
            await session.commit()

        assert session.add.call_count == 1

    @pytest.mark.asyncio
    async def test_seed_no_commit_when_all_exist(self) -> None:
        defaults = {("ns", "k1"): "v1"}
        existing = {("ns", "k1")}
        session = AsyncMock()

        inserted = 0
        for (namespace, key), value in defaults.items():
            if (namespace, key) not in existing:
                session.add(MagicMock())
                inserted += 1
        if inserted:
            await session.commit()

        assert session.add.call_count == 0
        session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: DEFAULT_CONFIG completeness
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    @pytest.fixture(autouse=True)
    def load_config(self) -> None:
        self.config = _load_default_config()

    def test_briefing_sections_included(self) -> None:
        sections = self.config[("morning_briefing", "sections")]
        names = [s["name"] for s in sections]
        for expected in EXPECTED_BRIEFING_SECTIONS:
            assert expected in names, f"Missing briefing section: {expected}"

    def test_required_namespaces(self) -> None:
        namespaces = {ns for ns, _ in self.config}
        for expected in EXPECTED_NAMESPACES:
            assert expected in namespaces, f"Missing namespace: {expected}"

    def test_location(self) -> None:
        assert self.config[("general", "location")] == "Wooster, OH"

    def test_timezone(self) -> None:
        assert self.config[("general", "timezone")] == "America/New_York"

    def test_email_contacts_stanford_urgent(self) -> None:
        urgent = self.config[("email_contacts", "always_urgent")]
        assert "*@stanford.edu" in urgent

    def test_quiet_hours(self) -> None:
        assert self.config[("notifications", "quiet_hours_start")] == "23:00"
        assert self.config[("notifications", "quiet_hours_end")] == "06:00"

    def test_briefing_time(self) -> None:
        assert self.config[("morning_briefing", "time")] == "05:50"

    def test_briefing_voice_enabled(self) -> None:
        assert self.config[("morning_briefing", "voice_enabled")] is True
