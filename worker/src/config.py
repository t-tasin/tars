from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    """Worker node configuration loaded from environment variables."""

    # --- Redis (Node 1 — brain) ---
    redis_url: str = "redis://100.94.4.103:6379/0"

    # --- System ---
    node_role: str = "muscle"
    log_level: str = "INFO"
    tars_env: str = "development"

    # --- Gemini (for image processing) ---
    gemini_api_key: str = ""

    # --- Worker tuning ---
    job_poll_interval: float = 0.5
    job_timeout: int = 300
    max_concurrent_jobs: int = 4

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache(maxsize=1)
def get_settings() -> WorkerSettings:
    return WorkerSettings()
