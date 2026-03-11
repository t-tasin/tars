from __future__ import annotations

from typing import Any

import structlog

from .base import BaseExecutor

log = structlog.get_logger("tars.worker.executor.research")


class ResearchExecutor(BaseExecutor):
    """Executes research tasks — web search, synthesis, report generation."""

    EXECUTOR_TYPE = "research"

    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        log.info("research_executor_start", job_id=job_id, topic=payload.get("topic"))
        # TODO: Spawn research container with Brave Search MCP, collect findings
        return {"status": "completed", "output": "research executor stub — not yet implemented"}
