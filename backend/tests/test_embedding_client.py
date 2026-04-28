"""Tests for EmbeddingClient — Qwen3-Embedding-0.6B on tars2:8003 (P3-04).

All HTTP mocked. Verifies request shape, single+batch paths, dimension
validation, usage tracking (HC-12), and graceful failure (HC-09).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from shared.constants import ModelName

from models.embedding_client import EmbeddingClient, EmbeddingError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 1024


def _vec(seed: float = 0.1) -> list[float]:
    """Build a 1024-dim float vector for fixtures."""
    return [seed + (i * 1e-4) for i in range(_DIM)]


def _embed_response(vectors: list[list[float]], model: str = "qwen3-embed-0.6b"):
    return {
        "model": model,
        "object": "list",
        "usage": {"prompt_tokens": 7, "total_tokens": 7},
        "data": [{"embedding": v, "index": i} for i, v in enumerate(vectors)],
    }


def _fake_post(json_body: dict) -> AsyncMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json = MagicMock(return_value=json_body)
    response.raise_for_status = MagicMock()
    return AsyncMock(return_value=response)


def _make_tracker() -> MagicMock:
    tracker = MagicMock()
    tracker.track = AsyncMock()
    return tracker


# ---------------------------------------------------------------------------
# Single embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_single_returns_1024_dim_vector():
    client = EmbeddingClient(base_url="http://100.119.114.125:8003")
    fake = _fake_post(_embed_response([_vec(0.1)]))

    with patch.object(client._http, "post", fake):
        result = await client.embed("hello world")

    assert isinstance(result, list)
    assert len(result) == _DIM
    assert all(isinstance(x, float) for x in result)


@pytest.mark.asyncio
async def test_embed_single_request_shape():
    """Verify URL, model alias, and input payload."""
    client = EmbeddingClient(base_url="http://10.0.0.5:8003")
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        await client.embed("the quick brown fox")

    assert fake.call_args.args[0] == "http://10.0.0.5:8003/v1/embeddings"
    body = fake.call_args.kwargs["json"]
    assert body["model"] == "qwen3-embed-0.6b"
    assert body["input"] == ["the quick brown fox"]


@pytest.mark.asyncio
async def test_base_url_trailing_slash_normalised():
    client = EmbeddingClient(base_url="http://x:8003/")
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        await client.embed("x")

    assert fake.call_args.args[0] == "http://x:8003/v1/embeddings"


# ---------------------------------------------------------------------------
# Batch embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_batch_single_request():
    """Batched embedding goes out as one HTTP request (llama.cpp supports it)."""
    client = EmbeddingClient(base_url="http://x:8003")
    vectors = [_vec(0.1), _vec(0.2), _vec(0.3)]
    fake = _fake_post(_embed_response(vectors))

    with patch.object(client._http, "post", fake):
        result = await client.embed_batch(["a", "b", "c"])

    assert len(result) == 3
    assert all(len(v) == _DIM for v in result)
    # Single HTTP call.
    assert fake.call_count == 1
    body = fake.call_args.kwargs["json"]
    assert body["input"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_embed_batch_empty_list_short_circuits():
    client = EmbeddingClient(base_url="http://x:8003")
    fake = _fake_post(_embed_response([]))

    with patch.object(client._http, "post", fake):
        result = await client.embed_batch([])

    assert result == []
    assert fake.call_count == 0


@pytest.mark.asyncio
async def test_embed_batch_parallel_dispatches_per_item():
    """Fallback path: parallel single-item calls via asyncio.gather."""
    client = EmbeddingClient(base_url="http://x:8003")
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        result = await client.embed_batch_parallel(["a", "b"])

    assert len(result) == 2
    assert fake.call_count == 2


# ---------------------------------------------------------------------------
# Dimension validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dimension_mismatch_raises_typed_error():
    """If the server returns a non-1024-dim vector, raise EmbeddingError."""
    client = EmbeddingClient(base_url="http://x:8003")
    bad_vec = [0.1] * 768  # wrong dim
    fake = _fake_post(_embed_response([bad_vec]))

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="1024-dim"):
            await client.embed("x")


@pytest.mark.asyncio
async def test_malformed_response_no_data_raises():
    client = EmbeddingClient(base_url="http://x:8003")
    fake = _fake_post({"model": "qwen3-embed-0.6b", "data": []})

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="no data"):
            await client.embed("x")


@pytest.mark.asyncio
async def test_malformed_entry_missing_embedding_field_raises():
    client = EmbeddingClient(base_url="http://x:8003")
    fake = _fake_post({"model": "qwen3-embed-0.6b", "data": [{"index": 0}]})

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="missing 'embedding'"):
            await client.embed("x")


# ---------------------------------------------------------------------------
# Usage tracking (HC-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_tracker_called_on_success():
    tracker = _make_tracker()
    client = EmbeddingClient(base_url="http://x:8003", usage_tracker=tracker)
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        await client.embed("hello")

    tracker.track.assert_awaited_once()
    kwargs = tracker.track.await_args.kwargs
    assert kwargs["model"] == ModelName.LOCAL_EMBED
    assert kwargs["agent_type"] == "embedding_client"
    assert kwargs["task_id"] is None
    assert kwargs["tokens_input"] == 7
    assert kwargs["tokens_output"] == 0
    assert kwargs["success"] is True
    assert kwargs["error_type"] is None


@pytest.mark.asyncio
async def test_usage_tracker_called_on_failure_with_error_type():
    tracker = _make_tracker()
    client = EmbeddingClient(base_url="http://x:8003", usage_tracker=tracker)

    response = MagicMock(spec=httpx.Response)
    response.status_code = 503
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=response)
    )
    fake = AsyncMock(return_value=response)

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError):
            await client.embed("x")

    tracker.track.assert_awaited_once()
    kwargs = tracker.track.await_args.kwargs
    assert kwargs["success"] is False
    assert kwargs["error_type"] == "HTTPStatusError"
    assert kwargs["model"] == ModelName.LOCAL_EMBED


@pytest.mark.asyncio
async def test_no_usage_tracker_does_not_break_call():
    """EmbeddingClient must work without a UsageTracker injected."""
    client = EmbeddingClient(base_url="http://x:8003", usage_tracker=None)
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        result = await client.embed("hello")

    assert len(result) == _DIM


@pytest.mark.asyncio
async def test_tracker_failure_swallowed():
    """If UsageTracker.track raises, the embed call still returns the vector."""
    tracker = MagicMock()
    tracker.track = AsyncMock(side_effect=RuntimeError("db down"))
    client = EmbeddingClient(base_url="http://x:8003", usage_tracker=tracker)
    fake = _fake_post(_embed_response([_vec()]))

    with patch.object(client._http, "post", fake):
        result = await client.embed("hello")

    assert len(result) == _DIM
    tracker.track.assert_awaited_once()


# ---------------------------------------------------------------------------
# Graceful failure (HC-09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_5xx_raises_embedding_error():
    client = EmbeddingClient(base_url="http://x:8003")
    response = MagicMock(spec=httpx.Response)
    response.status_code = 503
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=response)
    )
    fake = AsyncMock(return_value=response)

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="unreachable"):
            await client.embed("x")


@pytest.mark.asyncio
async def test_timeout_raises_embedding_error():
    client = EmbeddingClient(base_url="http://x:8003", timeout_s=0.001)
    fake = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="unreachable"):
            await client.embed("x")


@pytest.mark.asyncio
async def test_connect_error_raises_embedding_error():
    client = EmbeddingClient(base_url="http://x:8003")
    fake = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch.object(client._http, "post", fake):
        with pytest.raises(EmbeddingError, match="unreachable"):
            await client.embed("x")


@pytest.mark.asyncio
async def test_aclose_closes_http_client():
    client = EmbeddingClient(base_url="http://x:8003")
    with patch.object(client._http, "aclose", AsyncMock()) as fake_close:
        await client.aclose()
    fake_close.assert_awaited_once()
