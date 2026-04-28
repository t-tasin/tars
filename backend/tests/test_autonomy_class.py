"""Tests for ``shared.constants.AutonomyClass`` (P4-11).

Covers:
* enum members exist and use the canonical string values
* ``level`` ordering is strictly ascending from READ to WRITE_INFRA
* ``requires_approval()`` truth table — only WRITE_WORLD and WRITE_INFRA
  return True (HC-01 alignment).
"""

from __future__ import annotations

import pytest
from shared.constants import AutonomyClass

# ── Enum members ───────────────────────────────────────────────────────


class TestEnumMembers:
    def test_all_five_members_present(self) -> None:
        names = {m.name for m in AutonomyClass}
        assert names == {
            "READ",
            "WRITE_LOCAL",
            "WRITE_SELF",
            "WRITE_WORLD",
            "WRITE_INFRA",
        }

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (AutonomyClass.READ, "read"),
            (AutonomyClass.WRITE_LOCAL, "write_local"),
            (AutonomyClass.WRITE_SELF, "write_self"),
            (AutonomyClass.WRITE_WORLD, "write_world"),
            (AutonomyClass.WRITE_INFRA, "write_infra"),
        ],
    )
    def test_canonical_string_values(self, member: AutonomyClass, value: str) -> None:
        assert member.value == value
        assert member == value  # StrEnum equality with raw string


# ── Level ordering ─────────────────────────────────────────────────────


class TestLevel:
    def test_levels_are_zero_to_four(self) -> None:
        assert AutonomyClass.READ.level == 0
        assert AutonomyClass.WRITE_LOCAL.level == 1
        assert AutonomyClass.WRITE_SELF.level == 2
        assert AutonomyClass.WRITE_WORLD.level == 3
        assert AutonomyClass.WRITE_INFRA.level == 4

    def test_levels_are_strictly_ascending(self) -> None:
        order = [
            AutonomyClass.READ,
            AutonomyClass.WRITE_LOCAL,
            AutonomyClass.WRITE_SELF,
            AutonomyClass.WRITE_WORLD,
            AutonomyClass.WRITE_INFRA,
        ]
        levels = [c.level for c in order]
        assert levels == sorted(levels)
        assert levels == list(range(5))


# ── requires_approval() ────────────────────────────────────────────────


class TestRequiresApproval:
    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (AutonomyClass.READ, False),
            (AutonomyClass.WRITE_LOCAL, False),
            (AutonomyClass.WRITE_SELF, False),
            (AutonomyClass.WRITE_WORLD, True),
            (AutonomyClass.WRITE_INFRA, True),
        ],
    )
    def test_matrix(self, member: AutonomyClass, expected: bool) -> None:
        assert member.requires_approval() is expected

    def test_only_world_and_infra_require_approval(self) -> None:
        require = {c for c in AutonomyClass if c.requires_approval()}
        assert require == {AutonomyClass.WRITE_WORLD, AutonomyClass.WRITE_INFRA}
