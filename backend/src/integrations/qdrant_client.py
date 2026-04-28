"""Async Qdrant wrapper for T.A.R.S. (P3-03).

Phase 3 vector workflows (wiki retrieval, semantic search, sensor embeddings)
talk to the Qdrant deployment on Node 2 through this single class.

The metadata mirror lives in Postgres ``wiki_index`` (see
``db/repositories/wiki_index.py``); vectors and payloads live in Qdrant.
This wrapper:

- centralises connection config (Tailscale IP, port, https, api_key),
- gives callers a small, typed surface (``ensure_collection``, ``upsert``,
  ``search``, ``delete``, ``count``, ``close``),
- maps every underlying ``qdrant-client`` exception to a typed
  :class:`QdrantError` so callers can degrade gracefully (HC-09),
- emits structlog records on every public op with operation, collection,
  and ``duration_ms`` for observability.

Endpoint defaults match ``deploy/node2/docker-compose.yml`` (qdrant v1.11.5
on ``100.119.114.125:6333`` REST).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import exceptions as qdrant_exceptions
from qdrant_client.http import models as rest

log = structlog.get_logger()

# Defaults align with deploy/node2/docker-compose.yml (Qdrant on tars2 over Tailscale).
_DEFAULT_HOST = "100.119.114.125"
_DEFAULT_PORT = 6333
_DEFAULT_HTTPS = False
_DEFAULT_TIMEOUT_S = 10
_DEFAULT_VECTOR_SIZE = 1024  # Qwen3-Embedding-0.6B native dimension.
_DEFAULT_DISTANCE = "COSINE"


_DISTANCE_MAP: dict[str, rest.Distance] = {
    "COSINE": rest.Distance.COSINE,
    "EUCLID": rest.Distance.EUCLID,
    "DOT": rest.Distance.DOT,
    "MANHATTAN": rest.Distance.MANHATTAN,
}


class QdrantError(RuntimeError):
    """Raised when the Qdrant endpoint is unreachable or misbehaves.

    HC-09: callers can catch this and degrade gracefully (skip vector
    retrieval, fall back to keyword search, etc.).
    """


class QdrantClient:
    """Async wrapper around ``qdrant_client.AsyncQdrantClient``.

    Single responsibility: hide the underlying SDK shape behind a tiny
    typed surface tailored to Phase 3 needs (wiki + sensor embeddings).
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        https: bool = _DEFAULT_HTTPS,
        api_key: str | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._https = https
        # ``client`` injected for tests; production builds its own.
        self._client = client or AsyncQdrantClient(
            host=host,
            port=port,
            https=https,
            api_key=api_key,
            timeout=timeout_s,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_collection(
        self,
        name: str,
        vector_size: int = _DEFAULT_VECTOR_SIZE,
        distance: str = _DEFAULT_DISTANCE,
    ) -> bool:
        """Idempotently create a collection.

        Returns ``True`` if a collection was created, ``False`` if it
        already existed.
        """
        start = time.monotonic()
        created = False
        try:
            exists = await self._client.collection_exists(collection_name=name)
            if not exists:
                distance_enum = _DISTANCE_MAP.get(distance.upper())
                if distance_enum is None:
                    raise QdrantError(f"unknown distance metric {distance!r}; expected one of {sorted(_DISTANCE_MAP)}")
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=rest.VectorParams(
                        size=vector_size,
                        distance=distance_enum,
                    ),
                )
                created = True
            return created
        except QdrantError:
            raise
        except (qdrant_exceptions.UnexpectedResponse, qdrant_exceptions.ApiException) as exc:
            raise QdrantError(f"qdrant ensure_collection failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — net/transport faults are not all in qdrant_exceptions
            raise QdrantError(f"qdrant ensure_collection failed: {exc}") from exc
        finally:
            log.info(
                "qdrant.ensure_collection",
                collection=name,
                vector_size=vector_size,
                distance=distance,
                created=created,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def upsert(
        self,
        collection: str,
        point_id: uuid.UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """Insert or update a single point."""
        start = time.monotonic()
        try:
            point = rest.PointStruct(
                id=str(point_id),
                vector=list(vector),
                payload=dict(payload),
            )
            await self._client.upsert(collection_name=collection, points=[point])
        except (qdrant_exceptions.UnexpectedResponse, qdrant_exceptions.ApiException) as exc:
            raise QdrantError(f"qdrant upsert failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise QdrantError(f"qdrant upsert failed: {exc}") from exc
        finally:
            log.info(
                "qdrant.upsert",
                collection=collection,
                point_id=str(point_id),
                vector_dim=len(vector),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        k: int = 8,
        filter: dict[str, Any] | None = None,  # noqa: A002 — public API name kept for clarity
    ) -> list[rest.ScoredPoint]:
        """k-NN search. Returns the underlying ``ScoredPoint`` list.

        ``filter`` is a raw Qdrant-shaped dict (``{"must": [...]}``) that
        gets parsed into a ``rest.Filter``. Most call sites build it via
        ``rest.Filter(...).model_dump()`` or hand-craft the dict.
        """
        start = time.monotonic()
        hits: list[rest.ScoredPoint] = []
        try:
            qdrant_filter = rest.Filter(**filter) if filter else None
            response = await self._client.query_points(
                collection_name=collection,
                query=list(query_vector),
                limit=k,
                query_filter=qdrant_filter,
                with_payload=True,
            )
            hits = list(response.points)
            return hits
        except (qdrant_exceptions.UnexpectedResponse, qdrant_exceptions.ApiException) as exc:
            raise QdrantError(f"qdrant search failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise QdrantError(f"qdrant search failed: {exc}") from exc
        finally:
            log.info(
                "qdrant.search",
                collection=collection,
                k=k,
                hits=len(hits),
                filter_present=filter is not None,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def delete(self, collection: str, point_ids: list[uuid.UUID]) -> None:
        """Delete points by id."""
        start = time.monotonic()
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=rest.PointIdsList(
                    points=[str(p) for p in point_ids],
                ),
            )
        except (qdrant_exceptions.UnexpectedResponse, qdrant_exceptions.ApiException) as exc:
            raise QdrantError(f"qdrant delete failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise QdrantError(f"qdrant delete failed: {exc}") from exc
        finally:
            log.info(
                "qdrant.delete",
                collection=collection,
                count=len(point_ids),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def count(self, collection: str) -> int:
        """Exact point count in ``collection``."""
        start = time.monotonic()
        total = 0
        try:
            result = await self._client.count(collection_name=collection, exact=True)
            total = int(result.count)
            return total
        except (qdrant_exceptions.UnexpectedResponse, qdrant_exceptions.ApiException) as exc:
            raise QdrantError(f"qdrant count failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise QdrantError(f"qdrant count failed: {exc}") from exc
        finally:
            log.info(
                "qdrant.count",
                collection=collection,
                count=total,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def close(self) -> None:
        """Close the underlying Qdrant client."""
        try:
            await self._client.close()
        except Exception as exc:  # noqa: BLE001 — close should be best-effort
            log.warning("qdrant.close_failed", error=str(exc))


def get_qdrant_client(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    https: bool = _DEFAULT_HTTPS,
    api_key: str | None = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> QdrantClient:
    """Build a :class:`QdrantClient` with default Node 2 connection config.

    Caller owns the lifecycle — call :meth:`QdrantClient.close` when done.
    """
    return QdrantClient(
        host=host,
        port=port,
        https=https,
        api_key=api_key,
        timeout_s=timeout_s,
    )
