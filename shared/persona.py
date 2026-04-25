"""Persona loader — loads T.A.R.S. system prompts from data/persona/*.md.

Using @lru_cache so files are read once per process. Phase 5 (P5-02..04)
refines the .md content only; the loading infrastructure stays stable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "persona"


@lru_cache(maxsize=None)
def load_persona(name: str) -> str:
    """Load persona text from data/persona/{name}.md.

    Args:
        name: Persona name without extension (e.g. "local").

    Returns:
        Stripped content of the persona file.

    Raises:
        FileNotFoundError: If data/persona/{name}.md does not exist.
    """
    path = _DATA_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
