"""Client for local llama-server endpoints on Node 2 (Qwen3 reflex/brain/embed)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog
from shared.constants import ModelName

log = structlog.get_logger()

# Default host: tars2 over tailscale.
_DEFAULT_HOST = "100.119.114.125"
_DEFAULT_TIMEOUT_S = 60.0

# Per-tier endpoint config: (port, alias, kind, default_thinking)
_ENDPOINTS: dict[ModelName, tuple[int, str, str, bool]] = {
    ModelName.LOCAL_REFLEX: (8001, "qwen3-1.7b-reflex", "chat", False),
    ModelName.LOCAL_BRAIN: (8002, "qwen3-8b-brain", "chat", True),
    ModelName.LOCAL_EMBED: (8003, "qwen3-embed-0.6b", "embed", False),
}


@dataclass(frozen=True)
class LocalResponse:
    """Structured response from a llama-server chat completion."""

    text: str
    reasoning: str | None
    model: str
    tokens_input: int
    tokens_output: int
    duration_ms: int
    finish_reason: str


class LocalClient:
    """Async client for llama-server (Qwen3 reflex/brain/embed) on Node 2."""

    def __init__(self, host: str = _DEFAULT_HOST, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._host = host
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def generate(
        self,
        model: ModelName,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        enable_thinking: bool | None = None,
    ) -> LocalResponse:
        """Run a chat completion on a local llama-server."""
        port, alias, kind, default_thinking = _resolve(model)
        if kind != "chat":
            raise ValueError(f"{model} is not a chat tier; use embed()")

        thinking = default_thinking if enable_thinking is None else enable_thinking
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": alias,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }

        url = f"http://{self._host}:{port}/v1/chat/completions"
        start = time.monotonic()
        response = await self._http.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
        reasoning = message.get("reasoning_content")
        usage = data.get("usage") or {}

        log.info(
            "local_call",
            model=alias,
            tokens_input=usage.get("prompt_tokens", 0),
            tokens_output=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
            finish_reason=choice.get("finish_reason", ""),
            thinking=thinking,
        )

        return LocalResponse(
            text=text,
            reasoning=reasoning,
            model=data.get("model") or alias,
            tokens_input=usage.get("prompt_tokens", 0),
            tokens_output=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
            finish_reason=choice.get("finish_reason", ""),
        )


    async def embed(self, model: ModelName, inputs: list[str]) -> list[list[float]]:
        """Run an embedding call on a local llama-server."""
        port, alias, kind, _ = _resolve(model)
        if kind != "embed":
            raise ValueError(f"{model} is not an embedding tier; use generate()")

        url = f"http://{self._host}:{port}/v1/embeddings"
        payload = {"model": alias, "input": inputs}
        start = time.monotonic()
        response = await self._http.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        usage = data.get("usage") or {}
        log.info(
            "local_embed",
            model=alias,
            count=len(inputs),
            tokens_input=usage.get("prompt_tokens", 0),
            duration_ms=duration_ms,
        )

        return [item["embedding"] for item in data["data"]]


def _resolve(model: ModelName) -> tuple[int, str, str, bool]:
    cfg = _ENDPOINTS.get(model)
    if cfg is None:
        raise ValueError(f"{model} is not a local llama-server tier")
    return cfg
