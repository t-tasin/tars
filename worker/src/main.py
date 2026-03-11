from __future__ import annotations

import asyncio
import signal

from redis.asyncio import Redis

from .config import get_settings
from .job_processor import JobProcessor
from .logger import setup_logging


async def _run() -> None:
    settings = get_settings()
    log = setup_logging(
        log_level=settings.log_level,
        is_production=settings.tars_env == "production",
    )
    log.info("worker_starting", redis_url=settings.redis_url, node_role=settings.node_role)

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
        log.info("redis_connected")
    except Exception:
        log.exception("redis_connection_failed")
        raise

    processor = JobProcessor(
        redis=redis,
        poll_interval=settings.job_poll_interval,
        job_timeout=settings.job_timeout,
        max_concurrent=settings.max_concurrent_jobs,
    )

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        log.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    processor_task = asyncio.create_task(processor.start())

    log.info("worker_ready")

    # Wait until shutdown is requested
    await shutdown_event.wait()

    log.info("worker_shutting_down")
    await processor.stop()
    processor_task.cancel()
    await redis.aclose()
    log.info("worker_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
