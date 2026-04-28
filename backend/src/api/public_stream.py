"""Public Server-Sent Events stream (P3-13, HC-13).

Exposes ``GET /api/v1/public/stream`` for the public dashboard. The endpoint
subscribes to Redis pub/sub (channel ``tars:public:events``) and re-emits
each message as an SSE ``data:`` line **after** sanitisation. HC-13 requires
that *no PII* leaks; ``sanitize_public_event`` is the sole gate.

Auth model:
    * ``PUBLIC_STREAM_TOKEN`` env unset → endpoint is fully public.
      Trade-off: anyone with the URL can read sanitised events. Sanitiser
      remains the gate, so this is acceptable for a public dashboard.
    * ``PUBLIC_STREAM_TOKEN`` env set → ``Authorization: Bearer <token>``
      (or ``?token=`` query for ``EventSource`` clients) is required.

CORS:
    Configured per-router via ``PUBLIC_DASHBOARD_ORIGIN``. Default empty
    string → same-origin only (no ``Access-Control-Allow-Origin`` header).

Heartbeat:
    Every ``HEARTBEAT_INTERVAL_SECONDS`` we emit an SSE comment line
    (``: heartbeat\\n\\n``) so intermediaries don't drop the connection.

Backpressure:
    A bounded ``asyncio.Queue`` decouples the Redis subscriber from the
    HTTP writer. If the queue fills the oldest event is dropped (we'd
    rather miss a notice than block the dashboard).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from src.api.sanitizer import sanitize_public_event

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/public", tags=["public"])

PUBLIC_CHANNEL = "tars:public:events"
HEARTBEAT_INTERVAL_SECONDS = 15.0
QUEUE_MAXSIZE = 256
SUBSCRIBE_TIMEOUT_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _check_token(authorization: str | None, query_token: str | None) -> None:
    """Validate the optional bearer token; raise 401 if invalid.

    No token configured → endpoint is open. With a token configured, accept
    either ``Authorization: Bearer <token>`` or ``?token=...`` (for
    ``EventSource`` clients which can't set headers).
    """
    expected = os.environ.get("PUBLIC_STREAM_TOKEN", "")
    if not expected:
        return

    presented = ""
    if authorization:
        parts = authorization.split(" ", maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            presented = parts[1]
    if not presented and query_token:
        presented = query_token

    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="invalid_or_missing_public_token")


# ---------------------------------------------------------------------------
# Redis subscriber abstraction (testable)
# ---------------------------------------------------------------------------


async def _default_redis_client():
    """Return a real ``redis.asyncio.Redis`` from ``REDIS_URL``.

    Imported lazily so tests that don't touch this code path don't need
    a redis lib at import time.
    """
    import redis.asyncio as aioredis  # type: ignore[import-not-found]

    from src.config import get_settings

    settings = get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)


# Hook for tests: override with a callable returning a Redis client.
_redis_factory = _default_redis_client


def set_redis_factory(factory) -> None:
    """Override the redis client factory (test seam)."""
    global _redis_factory
    _redis_factory = factory


def reset_redis_factory() -> None:
    """Restore the default redis factory."""
    global _redis_factory
    _redis_factory = _default_redis_client


# ---------------------------------------------------------------------------
# Stream generator
# ---------------------------------------------------------------------------


def _format_sse(event: dict[str, Any]) -> bytes:
    """Render a sanitised event as one SSE ``data:`` frame."""
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    return f"data: {body}\n\n".encode()


async def _pump_redis(queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Subscribe to ``PUBLIC_CHANNEL`` and push *sanitised* events into queue.

    Runs until cancelled. Drops events that fail sanitisation (HC-13).
    """
    client = await _redis_factory()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(PUBLIC_CHANNEL)
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=SUBSCRIBE_TIMEOUT_SECONDS,
            )
            if message is None:
                # No event this tick; loop so cancellation can interrupt.
                await asyncio.sleep(0)
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if not isinstance(data, str):
                continue
            try:
                raw = json.loads(data)
            except json.JSONDecodeError:
                log.warning("public_stream_invalid_json")
                continue
            sanitised = sanitize_public_event(raw)
            if sanitised is None:
                # PII or unrecognised type — drop silently. HC-13.
                continue
            try:
                queue.put_nowait(sanitised)
            except asyncio.QueueFull:
                # Drop oldest to keep the stream live.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(sanitised)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(PUBLIC_CHANNEL)
        with contextlib.suppress(Exception):
            await pubsub.close()
        with contextlib.suppress(Exception):
            await client.aclose()


async def _event_stream(request: Request) -> AsyncIterator[bytes]:
    """Yield SSE frames + heartbeats until the client disconnects."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    pump = asyncio.create_task(_pump_redis(queue))

    # Initial comment so the connection becomes "open" on the client.
    yield b": connected\n\n"

    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )
            except TimeoutError:
                yield b": heartbeat\n\n"
                continue
            yield _format_sse(event)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _cors_headers() -> dict[str, str]:
    origin = os.environ.get("PUBLIC_DASHBOARD_ORIGIN", "").strip()
    headers: dict[str, str] = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Authorization"
        headers["Vary"] = "Origin"
    return headers


@router.get("/stream")
async def public_stream(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> StreamingResponse:
    """Server-Sent Events stream of redacted public activity events."""
    _check_token(authorization, token)

    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers=_cors_headers(),
    )


@router.options("/stream")
async def public_stream_preflight() -> Response:
    """CORS preflight for browser ``EventSource`` clients."""
    return Response(status_code=204, headers=_cors_headers())


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "PUBLIC_CHANNEL",
    "_format_sse",
    "_pump_redis",
    "reset_redis_factory",
    "router",
    "set_redis_factory",
]
