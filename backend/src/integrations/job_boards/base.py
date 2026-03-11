from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class JobBoardAdapter(ABC):
    """Abstract base for all job board integrations."""

    source_name: str = "unknown"

    @abstractmethod
    async def search(self, criteria: dict) -> list[dict]:
        """Search for jobs matching criteria. Returns list of raw job dicts."""

    def normalize(self, raw_job: dict) -> dict:
        """Normalize a raw job dict to T.A.R.S. standard format."""
        return {
            "source": self.source_name,
            "external_id": raw_job.get("id", ""),
            "title": raw_job.get("title", ""),
            "company": raw_job.get("company", ""),
            "location": raw_job.get("location", ""),
            "salary_range": raw_job.get("salary", ""),
            "description": raw_job.get("description", ""),
            "url": raw_job.get("url", ""),
            "scraped_at": datetime.now(timezone.utc),
        }
