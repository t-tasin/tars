"""Tests for shared/persona.py — persona file loader (P2.5-06)."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Test: load_persona basic
# ---------------------------------------------------------------------------


def test_load_persona_returns_non_empty_string() -> None:
    from shared.persona import load_persona

    text = load_persona("local")
    assert isinstance(text, str)
    assert len(text) > 0


def test_load_persona_local_contains_tars() -> None:
    from shared.persona import load_persona

    text = load_persona("local")
    assert "T.A.R.S." in text


def test_load_persona_local_contains_english_instruction() -> None:
    from shared.persona import load_persona

    text = load_persona("local")
    assert "English" in text


# ---------------------------------------------------------------------------
# Test: lru_cache — same object returned on repeated calls
# ---------------------------------------------------------------------------


def test_load_persona_is_cached() -> None:
    from shared.persona import load_persona

    result1 = load_persona("local")
    result2 = load_persona("local")
    assert result1 is result2  # same object == cache hit


# ---------------------------------------------------------------------------
# Test: missing persona raises FileNotFoundError
# ---------------------------------------------------------------------------


def test_load_persona_raises_for_missing_file() -> None:
    import pytest
    from shared.persona import load_persona

    with pytest.raises(FileNotFoundError):
        load_persona("nonexistent_persona_xyz")


# ---------------------------------------------------------------------------
# Test: engine.BASE_LOCAL_SYSTEM_PROMPT equals persona content
# ---------------------------------------------------------------------------


def test_engine_base_prompt_uses_persona_file() -> None:
    from shared.persona import load_persona

    from orchestrator.engine import BASE_LOCAL_SYSTEM_PROMPT

    assert BASE_LOCAL_SYSTEM_PROMPT == load_persona("local")
