"""AI model clients and MCP configuration for T.A.R.S."""

from __future__ import annotations

# MCP server profiles — maps agent types to the MCP servers they may access.
# Used by ClaudeSpawner to scope --allowedTools per agent invocation.
MCP_PROFILES: dict[str, list[str]] = {
    "coding": ["github", "filesystem", "postgres"],
    "research": ["brave-search", "postgres"],
    "diagnostics": ["postgres", "brave-search"],
    "health_monitor": ["postgres"],
    "communication": ["postgres"],
    "general": ["brave-search"],
}

__all__ = ["MCP_PROFILES"]
