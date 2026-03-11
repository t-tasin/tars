from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

log = structlog.get_logger("tars.worker.executor")


class BaseExecutor(ABC):
    """Abstract base for all job executors on Node 2."""

    EXECUTOR_TYPE: str = "base"

    @abstractmethod
    async def execute(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the job and return a result dict.

        Args:
            job_id: Unique identifier for this job.
            payload: Job-specific parameters from the Redis queue.

        Returns:
            Result dict with at minimum {"status": "completed"|"failed", ...}.
        """
        ...
