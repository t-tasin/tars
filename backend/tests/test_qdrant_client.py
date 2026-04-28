"""Tests for ``integrations.qdrant_client.QdrantClient`` (P3-03).

All Qdrant calls are mocked through an injected ``AsyncQdrantClient``;
no live Qdrant required. Coverage:

- ensure_collection idempotent (exists vs not exists)
- distance metric mapping + invalid metric rejection
- upsert + search round-trip via mocks
- search with filter + without filter
- delete
- count
- error mapping (underlying client raises -> ``QdrantError``) (HC-09)
- close cleans up
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client.http import exceptions as qdrant_exceptions
from qdrant_client.http import models as rest

from integrations.qdrant_client import (
    QdrantClient,
    QdrantError,
    get_qdrant_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(seed: float = 0.1, dim: int = 1024) -> list[float]:
    return [seed + (i * 1e-4) for i in range(dim)]


def _mock_async_client() -> MagicMock:
    """Build an ``AsyncQdrantClient`` mock with all used coros stubbed."""
    mock = MagicMock()
    mock.collection_exists = AsyncMock(return_value=False)
    mock.create_collection = AsyncMock(return_value=True)
    mock.upsert = AsyncMock(return_value=MagicMock(status="acknowledged"))
    mock.query_points = AsyncMock(return_value=MagicMock(points=[]))
    mock.delete = AsyncMock(return_value=MagicMock(status="acknowledged"))
    mock.count = AsyncMock(return_value=MagicMock(count=0))
    mock.close = AsyncMock(return_value=None)
    return mock


def _client(mock: MagicMock | None = None) -> tuple[QdrantClient, MagicMock]:
    inner = mock or _mock_async_client()
    return QdrantClient(client=inner), inner


# ---------------------------------------------------------------------------
# ensure_collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing() -> None:
    qc, inner = _client()
    inner.collection_exists.return_value = False

    created = await qc.ensure_collection("tasin_wiki", vector_size=1024, distance="COSINE")

    assert created is True
    inner.create_collection.assert_awaited_once()
    kwargs = inner.create_collection.await_args.kwargs
    assert kwargs["collection_name"] == "tasin_wiki"
    vectors_config = kwargs["vectors_config"]
    assert isinstance(vectors_config, rest.VectorParams)
    assert vectors_config.size == 1024
    assert vectors_config.distance == rest.Distance.COSINE


@pytest.mark.asyncio
async def test_ensure_collection_idempotent_when_exists() -> None:
    qc, inner = _client()
    inner.collection_exists.return_value = True

    created = await qc.ensure_collection("tasin_wiki")

    assert created is False
    inner.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_collection_distance_mapping_euclid() -> None:
    qc, inner = _client()
    inner.collection_exists.return_value = False

    await qc.ensure_collection("foo", vector_size=8, distance="euclid")

    kwargs = inner.create_collection.await_args.kwargs
    assert kwargs["vectors_config"].distance == rest.Distance.EUCLID


@pytest.mark.asyncio
async def test_ensure_collection_rejects_unknown_distance() -> None:
    qc, inner = _client()
    inner.collection_exists.return_value = False

    with pytest.raises(QdrantError, match="unknown distance metric"):
        await qc.ensure_collection("foo", distance="bogus")

    inner.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_collection_wraps_underlying_error() -> None:
    qc, inner = _client()
    inner.collection_exists.side_effect = qdrant_exceptions.ApiException("boom")

    with pytest.raises(QdrantError, match="ensure_collection"):
        await qc.ensure_collection("foo")


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_sends_pointstruct() -> None:
    qc, inner = _client()
    pid = uuid.uuid4()
    vec = _vec(0.5)
    payload = {"path": "wiki/foo.md", "chunk_index": 0}

    await qc.upsert("tasin_wiki", pid, vec, payload)

    inner.upsert.assert_awaited_once()
    kwargs = inner.upsert.await_args.kwargs
    assert kwargs["collection_name"] == "tasin_wiki"
    points = kwargs["points"]
    assert len(points) == 1
    point = points[0]
    assert isinstance(point, rest.PointStruct)
    assert str(point.id) == str(pid)
    assert point.vector == vec
    assert point.payload == payload


@pytest.mark.asyncio
async def test_upsert_wraps_unexpected_response() -> None:
    qc, inner = _client()
    inner.upsert.side_effect = qdrant_exceptions.UnexpectedResponse(
        status_code=500, reason_phrase="ISE", content=b"", headers=None
    )

    with pytest.raises(QdrantError, match="upsert"):
        await qc.upsert("c", uuid.uuid4(), _vec(), {"k": "v"})


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_round_trip_no_filter() -> None:
    qc, inner = _client()
    fake_hit = MagicMock(spec=rest.ScoredPoint)
    fake_hit.id = "abc"
    fake_hit.score = 0.99
    inner.query_points.return_value = MagicMock(points=[fake_hit])

    results = await qc.search("tasin_wiki", _vec(), k=5)

    assert results == [fake_hit]
    kwargs = inner.query_points.await_args.kwargs
    assert kwargs["collection_name"] == "tasin_wiki"
    assert kwargs["limit"] == 5
    assert kwargs["query_filter"] is None
    assert kwargs["with_payload"] is True


@pytest.mark.asyncio
async def test_search_with_filter_builds_rest_filter() -> None:
    qc, inner = _client()
    inner.query_points.return_value = MagicMock(points=[])

    flt = {
        "must": [
            {"key": "path", "match": {"value": "wiki/foo.md"}},
        ],
    }
    await qc.search("tasin_wiki", _vec(), k=8, filter=flt)

    kwargs = inner.query_points.await_args.kwargs
    qfilter = kwargs["query_filter"]
    assert isinstance(qfilter, rest.Filter)
    assert qfilter.must is not None
    assert len(qfilter.must) == 1


@pytest.mark.asyncio
async def test_search_wraps_underlying_error() -> None:
    qc, inner = _client()
    inner.query_points.side_effect = qdrant_exceptions.ApiException("conn refused")

    with pytest.raises(QdrantError, match="search"):
        await qc.search("c", _vec())


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_passes_point_ids() -> None:
    qc, inner = _client()
    ids = [uuid.uuid4(), uuid.uuid4()]

    await qc.delete("tasin_wiki", ids)

    inner.delete.assert_awaited_once()
    kwargs = inner.delete.await_args.kwargs
    assert kwargs["collection_name"] == "tasin_wiki"
    selector = kwargs["points_selector"]
    assert isinstance(selector, rest.PointIdsList)
    assert {str(p) for p in selector.points} == {str(i) for i in ids}


@pytest.mark.asyncio
async def test_delete_wraps_underlying_error() -> None:
    qc, inner = _client()
    inner.delete.side_effect = qdrant_exceptions.ApiException("offline")

    with pytest.raises(QdrantError, match="delete"):
        await qc.delete("c", [uuid.uuid4()])


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_returns_int() -> None:
    qc, inner = _client()
    inner.count.return_value = MagicMock(count=42)

    n = await qc.count("tasin_wiki")

    assert n == 42
    kwargs = inner.count.await_args.kwargs
    assert kwargs["exact"] is True
    assert kwargs["collection_name"] == "tasin_wiki"


@pytest.mark.asyncio
async def test_count_wraps_underlying_error() -> None:
    qc, inner = _client()
    inner.count.side_effect = qdrant_exceptions.UnexpectedResponse(
        status_code=503, reason_phrase="Unavailable", content=b"", headers=None
    )

    with pytest.raises(QdrantError, match="count"):
        await qc.count("c")


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_invokes_underlying() -> None:
    qc, inner = _client()
    await qc.close()
    inner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_swallows_underlying_error() -> None:
    qc, inner = _client()
    inner.close.side_effect = RuntimeError("already closed")

    # Must not raise — close is best-effort.
    await qc.close()


# ---------------------------------------------------------------------------
# Generic transport faults map to QdrantError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_wraps_generic_transport_failure() -> None:
    """ConnectionError/timeout aren't always in qdrant_exceptions; still typed."""
    qc, inner = _client()
    inner.query_points.side_effect = ConnectionError("dns")

    with pytest.raises(QdrantError, match="search"):
        await qc.search("c", _vec())


# ---------------------------------------------------------------------------
# get_qdrant_client factory + defaults
# ---------------------------------------------------------------------------


def test_get_qdrant_client_uses_node2_defaults() -> None:
    qc = get_qdrant_client()
    assert isinstance(qc, QdrantClient)
    # Defaults: Node 2 over Tailscale.
    assert qc._host == "100.119.114.125"
    assert qc._port == 6333
    assert qc._https is False
