"""Dedicated async client for the Qwen3-Embedding-0.6B llama-server on Node 2.

Phase 3 vector workflows (wiki retrieval, sensor embedding, semantic search) talk
to the local embedding tier through this single-responsibility client.

The chat-tier ``LocalClient`` already exposes a generic ``embed()`` method, but
Phase 3 needs:

- a typed ``EmbeddingClient`` with single + batch entry points,
- 1024-dim validation (Qwen3-Embedding-0.6B output),
- HC-12 ``UsageTracker.track()`` on every call,
- HC-09 graceful failure via a typed exception.

Endpoint: ``http://<host>:8003/v1/embeddings`` (OpenAI-compatible).
Model alias: ``qwen3-embed-0.6b`` (see ``local_client._ENDPOINTS``).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import httpx
import structlog
from shared.constants import ModelName

if TYPE_CHECKING:
    from models.usage_tracker import UsageTracker

log = structlog.get_logger()

# Defaults align with local_client.py (tars2 over tailscale, qwen3-embed-0.6b on :8003).
_DEFAULT_BASE_URL = "http://100.119.114.125:8003"
_DEFAULT_TIMEOUT_S = 60.0
_MODEL_ALIAS = "qwen3-embed-0.6b"
_EMBED_DIM = 1024  # Qwen3-Embedding-0.6B native dimension.
_AGENT_TYPE = "embedding_client"


class EmbeddingError(RuntimeError):
    """Raised when the local embedding endpoint is unreachable or misbehaves.

    HC-09: callers can catch this and degrade gracefully.
    """


class EmbeddingClient:
    """Async client for the Qwen3-Embedding-0.6B llama-server on Node 2 (port 8003)."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout_s)
        self._usage = usage_tracker

    async def embed(self, text: str) -> list[float]:
        """Embed a single string. Returns a 1024-dim vector."""
        vectors = await self._post([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of strings in a single request.

        llama.cpp's OpenAI-compat ``/v1/embeddings`` accepts batched ``input``;
        we send one HTTP request per call. If the server rejects the batch,
        the typed ``EmbeddingError`` bubbles up — callers can fall back to
        per-item ``embed()`` calls if needed.
        """
        if not texts:
            return []
        return await self._post(texts)

    async def embed_batch_parallel(self, texts: list[str]) -> list[list[float]]:
        """Fallback batching: dispatch parallel single-item calls via gather.

        Useful when the deployed llama-server build is compiled without batched
        embedding support. Default flow uses :meth:`embed_batch`; this is a
        compatibility escape hatch.
        """
        if not texts:
            return []
        results = await asyncio.gather(*(self.embed(t) for t in texts))
        return list(results)

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _post(self, inputs: list[str]) -> list[list[float]]:
        url = f"{self._base_url}/v1/embeddings"
        payload = {"model": _MODEL_ALIAS, "input": inputs}

        start = time.monotonic()
        success = False
        error_type: str | None = None
        tokens_input = 0

        try:
            try:
                response = await self._http.post(url, json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                error_type = type(exc).__name__
                raise EmbeddingError(f"local embedding endpoint unreachable: {exc}") from exc

            try:
                data = response.json()
            except ValueError as exc:
                error_type = "InvalidJSON"
                raise EmbeddingError("local embedding endpoint returned non-JSON body") from exc

            items = data.get("data")
            if not isinstance(items, list) or not items:
                error_type = "MalformedResponse"
                raise EmbeddingError("local embedding endpoint returned no data")

            vectors: list[list[float]] = []
            for entry in items:
                vec = entry.get("embedding")
                if not isinstance(vec, list):
                    error_type = "MalformedResponse"
                    raise EmbeddingError("embedding entry missing 'embedding' field")
                if len(vec) != _EMBED_DIM:
                    error_type = "DimensionMismatch"
                    raise EmbeddingError(f"expected {_EMBED_DIM}-dim vector, got {len(vec)}-dim")
                vectors.append([float(x) for x in vec])

            usage = data.get("usage") or {}
            tokens_input = int(usage.get("prompt_tokens", 0))
            success = True
            return vectors
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "embedding_client_call",
                model=_MODEL_ALIAS,
                count=len(inputs),
                tokens_input=tokens_input,
                duration_ms=duration_ms,
                success=success,
                error_type=error_type,
            )
            if self._usage is not None:
                try:
                    await self._usage.track(
                        model=ModelName.LOCAL_EMBED,
                        agent_type=_AGENT_TYPE,
                        task_id=None,
                        tokens_input=tokens_input,
                        tokens_output=0,
                        duration_ms=duration_ms,
                        success=success,
                        error_type=error_type,
                    )
                except Exception as exc:  # noqa: BLE001 — never let tracking break callers
                    log.warning("embedding_usage_track_failed", error=str(exc))
