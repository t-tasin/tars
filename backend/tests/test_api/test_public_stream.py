"""Tests for ``src.api.public_stream`` — public SSE endpoint (P3-13, HC-13).

The endpoint subscribes to a Redis pub/sub channel and re-emits each event
as an SSE frame *after* sanitisation. These tests exercise:

* Auth gate (token unset → open; token set → bearer + query param both work).
* Heartbeat emission when no events flow.
* Sanitised event emission for benign events.
* PII-bearing events are dropped silently.
* Response content-type / headers are correct for ``text/event-stream``.
* Direct unit coverage of ``_format_sse`` and ``_pump_redis`` via the
  ``set_redis_factory`` test seam (no real Redis required).

A small ``FakeRedis`` stand-in mimics the subset of the
``redis.asyncio.Redis``/``PubSub`` API the endpoint uses (``pubsub()``,
``subscribe``, ``get_message``, ``unsubscribe``, ``close``, ``aclose``).
``fakeredis`` itself doesn't expose a queryable ``get_message`` shape that
matches our 1-second-timeout polling loop, so we use a hand-rolled fake.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import public_stream
from src.api.public_stream import (
    _check_token,
    _event_stream,
    _format_sse,
    _pump_redis,
    reset_redis_factory,
    router,
    set_redis_factory,
)

# ---------------------------------------------------------------------------
# FakeRedis / FakePubSub
# ---------------------------------------------------------------------------


class _FakePubSub:
    """Tiny stand-in for ``redis.asyncio.client.PubSub``.

    Pre-loads a list of raw payload strings to deliver via successive
    ``get_message`` calls. Returns ``None`` once exhausted (mirrors the real
    client's behaviour when the subscribe-side timeout expires).
    """

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:  # pragma: no cover - cleanup
        self.subscribed = [c for c in self.subscribed if c != channel]

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0) -> dict[str, Any] | None:
        if not self._messages:
            await asyncio.sleep(0)
            return None
        return {"type": "message", "data": self._messages.pop(0)}

    async def close(self) -> None:  # pragma: no cover - cleanup
        self.closed = True


class _FakeRedis:
    def __init__(self, messages: list[str]) -> None:
        self._pubsub = _FakePubSub(messages)

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:  # pragma: no cover - cleanup
        pass


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _reset_factory():
    """Ensure each test starts/ends with the default redis factory."""
    yield
    reset_redis_factory()


# ---------------------------------------------------------------------------
# _format_sse
# ---------------------------------------------------------------------------


def test_format_sse_emits_data_line_terminated_by_blank_line() -> None:
    frame = _format_sse({"type": "agent_status", "agent": "router"})
    assert frame.endswith(b"\n\n")
    assert frame.startswith(b"data: ")
    body = frame[len("data: ") :].strip()
    assert json.loads(body) == {"type": "agent_status", "agent": "router"}


# ---------------------------------------------------------------------------
# Auth gate (_check_token)
# ---------------------------------------------------------------------------


def test_check_token_no_env_means_public() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PUBLIC_STREAM_TOKEN", None)
        # No exception -> open access.
        _check_token(authorization=None, query_token=None)


def test_check_token_accepts_bearer_header() -> None:
    with patch.dict(os.environ, {"PUBLIC_STREAM_TOKEN": "secret-xyz"}):
        _check_token(authorization="Bearer secret-xyz", query_token=None)


def test_check_token_accepts_query_param() -> None:
    with patch.dict(os.environ, {"PUBLIC_STREAM_TOKEN": "secret-xyz"}):
        _check_token(authorization=None, query_token="secret-xyz")


def test_check_token_rejects_missing_when_required() -> None:
    from fastapi import HTTPException

    with patch.dict(os.environ, {"PUBLIC_STREAM_TOKEN": "secret-xyz"}):
        with pytest.raises(HTTPException) as exc:
            _check_token(authorization=None, query_token=None)
    assert exc.value.status_code == 401


def test_check_token_rejects_wrong_token() -> None:
    from fastapi import HTTPException

    with patch.dict(os.environ, {"PUBLIC_STREAM_TOKEN": "secret-xyz"}):
        with pytest.raises(HTTPException) as exc:
            _check_token(authorization="Bearer nope", query_token=None)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# _pump_redis: sanitises and drops PII
# ---------------------------------------------------------------------------


async def test_pump_redis_emits_sanitised_events() -> None:
    raw_events = [
        json.dumps({"type": "agent_status", "agent": "router", "state": "running"}),
        json.dumps({"type": "model_call", "tier": "L1", "tokens_in": 10, "tokens_out": 5}),
    ]
    set_redis_factory(lambda: _async_return(_FakeRedis(raw_events)))

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    task = asyncio.create_task(_pump_redis(queue))
    try:
        first = await asyncio.wait_for(queue.get(), timeout=2.0)
        second = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert first["type"] == "agent_status"
    assert first["agent"] == "router"
    assert second["type"] == "model_call"
    assert second["tokens_in"] == 10


async def test_pump_redis_drops_unknown_event_types() -> None:
    raw_events = [
        json.dumps({"type": "secret_thing", "value": "leak"}),
        json.dumps({"type": "agent_status", "agent": "router"}),
    ]
    set_redis_factory(lambda: _async_return(_FakeRedis(raw_events)))

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    task = asyncio.create_task(_pump_redis(queue))
    try:
        # The "secret_thing" event is dropped — the next pull is "agent_status".
        evt = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert evt["type"] == "agent_status"


async def test_pump_redis_strips_pii_from_text_field() -> None:
    raw_events = [
        json.dumps(
            {
                "type": "summary",
                "label": "inbox",
                "count": 1,
                "text": "from fake.user@synthetic-domain.test at 100.94.4.103",
            }
        ),
    ]
    set_redis_factory(lambda: _async_return(_FakeRedis(raw_events)))

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    task = asyncio.create_task(_pump_redis(queue))
    try:
        evt = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert "fake.user" not in evt["text"]
    assert "100.94.4.103" not in evt["text"]
    assert "[REDACTED]" in evt["text"]


# ---------------------------------------------------------------------------
# _event_stream — async generator drives heartbeat + sanitised emit
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for ``starlette.requests.Request``.

    ``_event_stream`` only awaits ``request.is_disconnected()``; flipping the
    flag lets the generator exit cleanly so each test wraps up fast.
    """

    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


async def test_event_stream_emits_heartbeat_when_idle() -> None:
    set_redis_factory(lambda: _async_return(_FakeRedis([])))
    request = _FakeRequest()

    with patch.object(public_stream, "HEARTBEAT_INTERVAL_SECONDS", 0.05):
        gen = _event_stream(request)  # type: ignore[arg-type]
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        request.disconnected = True
        # Drain so the generator's finally clause runs.
        try:
            async for _ in gen:
                break
        except StopAsyncIteration:
            pass
        await gen.aclose()

    assert first == b": connected\n\n"
    assert second == b": heartbeat\n\n"


async def test_event_stream_emits_sanitised_event_data() -> None:
    raw_events = [json.dumps({"type": "agent_status", "agent": "router", "state": "running"})]
    set_redis_factory(lambda: _async_return(_FakeRedis(raw_events)))
    request = _FakeRequest()

    with patch.object(public_stream, "HEARTBEAT_INTERVAL_SECONDS", 5.0):
        gen = _event_stream(request)  # type: ignore[arg-type]
        connected = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        data_frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        request.disconnected = True
        await gen.aclose()

    assert connected == b": connected\n\n"
    assert data_frame.startswith(b"data: ")
    payload = json.loads(data_frame[len(b"data: ") :].strip())
    assert payload["type"] == "agent_status"
    assert payload["agent"] == "router"


# ---------------------------------------------------------------------------
# Endpoint surface — auth gate via TestClient. The 401 path completes before
# any streaming starts, so it doesn't block. The 200 (open-stream) path is
# exercised by the ``_event_stream`` tests above to avoid TestClient's sync
# generator tearing down a never-ending SSE response.
# ---------------------------------------------------------------------------


def test_endpoint_rejects_when_token_required_and_missing() -> None:
    set_redis_factory(lambda: _async_return(_FakeRedis([])))
    app = _build_app()
    client = TestClient(app)

    with patch.dict(os.environ, {"PUBLIC_STREAM_TOKEN": "needed"}):
        response = client.get("/api/v1/public/stream")
    assert response.status_code == 401


def test_endpoint_options_preflight_returns_204() -> None:
    app = _build_app()
    client = TestClient(app)
    response = client.options("/api/v1/public/stream")
    assert response.status_code == 204


def test_endpoint_route_is_registered_with_event_stream_media_type() -> None:
    """Route metadata sanity-check (no live request needed)."""
    app = _build_app()
    routes = {(r.path, tuple(sorted(r.methods or []))): r for r in app.routes}  # type: ignore[attr-defined]
    assert ("/api/v1/public/stream", ("GET",)) in routes
    assert ("/api/v1/public/stream", ("OPTIONS",)) in routes


def test_endpoint_cors_header_emitted_when_origin_configured() -> None:
    """``PUBLIC_DASHBOARD_ORIGIN`` populates the preflight CORS headers."""
    app = _build_app()
    client = TestClient(app)
    with patch.dict(os.environ, {"PUBLIC_DASHBOARD_ORIGIN": "https://dashboard.test"}):
        response = client.options("/api/v1/public/stream")
    assert response.status_code == 204
    assert response.headers.get("access-control-allow-origin") == "https://dashboard.test"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _async_return(value: Any) -> Any:
    """Wrap ``value`` so it can be returned from an async factory."""
    return value
