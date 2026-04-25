"""Tests for LocalClient — talks to llama-server on Node 2, all HTTP mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from shared.constants import ModelName

from models.local_client import LocalClient, LocalResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_response(
    content: str = "OK.",
    reasoning: str | None = None,
    prompt_tokens: int = 18,
    completion_tokens: int = 3,
    finish_reason: str = "stop",
    model: str = "qwen3-1.7b-reflex",
):
    """Build a fake llama-server /v1/chat/completions JSON body."""
    message: dict[str, object] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "choices": [
            {"finish_reason": finish_reason, "index": 0, "message": message},
        ],
        "model": model,
        "object": "chat.completion",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _embed_response(vectors: list[list[float]], model: str = "qwen3-embed-0.6b"):
    return {
        "model": model,
        "object": "list",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
        "data": [{"embedding": v, "index": i} for i, v in enumerate(vectors)],
    }


def _fake_post(json_body: dict) -> AsyncMock:
    """Build an AsyncMock for httpx.AsyncClient.post returning the given JSON."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json = MagicMock(return_value=json_body)
    response.raise_for_status = MagicMock()
    return AsyncMock(return_value=response)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_reflex_happy_path_returns_content():
    client = LocalClient(host="100.119.114.125")
    fake = _fake_post(_chat_response(content="OK."))

    with patch.object(client._http, "post", fake):
        resp = await client.generate(model=ModelName.LOCAL_REFLEX, prompt="say ok")

    assert isinstance(resp, LocalResponse)
    assert resp.text == "OK."
    assert resp.reasoning is None or resp.reasoning == ""
    assert resp.tokens_input == 18
    assert resp.tokens_output == 3
    assert resp.finish_reason == "stop"
    assert resp.model == "qwen3-1.7b-reflex"


@pytest.mark.asyncio
async def test_generate_brain_with_thinking_parses_reasoning_separately():
    client = LocalClient(host="100.119.114.125")
    fake = _fake_post(
        _chat_response(
            content="4",
            reasoning="Let me think... 2+2 is 4.",
            model="qwen3-8b-brain",
        )
    )

    with patch.object(client._http, "post", fake):
        resp = await client.generate(model=ModelName.LOCAL_BRAIN, prompt="2+2?")

    assert resp.text == "4"
    assert resp.reasoning == "Let me think... 2+2 is 4."


@pytest.mark.asyncio
async def test_generate_truncated_cot_returns_empty_text_and_full_reasoning():
    """Qwen3 CoT can fill max_tokens before emitting final answer — content stays empty."""
    client = LocalClient(host="100.119.114.125")
    fake = _fake_post(
        _chat_response(
            content="",
            reasoning="Hmm, the user wants...",
            finish_reason="length",
        )
    )

    with patch.object(client._http, "post", fake):
        resp = await client.generate(model=ModelName.LOCAL_REFLEX, prompt="x", max_tokens=16)

    assert resp.text == ""
    assert resp.reasoning == "Hmm, the user wants..."
    assert resp.finish_reason == "length"


@pytest.mark.asyncio
async def test_generate_endpoint_mapping():
    """REFLEX → :8001, BRAIN → :8002 — verified by URL inspection."""
    client = LocalClient(host="10.0.0.1")
    fake = _fake_post(_chat_response())

    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_REFLEX, prompt="x")
    assert fake.call_args.args[0] == "http://10.0.0.1:8001/v1/chat/completions"

    fake.reset_mock()
    fake.return_value.json.return_value = _chat_response(model="qwen3-8b-brain")
    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_BRAIN, prompt="x")
    assert fake.call_args.args[0] == "http://10.0.0.1:8002/v1/chat/completions"


@pytest.mark.asyncio
async def test_generate_default_thinking_per_tier():
    """REFLEX defaults enable_thinking=False (latency); BRAIN defaults True (quality)."""
    client = LocalClient(host="x")
    fake = _fake_post(_chat_response())

    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_REFLEX, prompt="x")
    body = fake.call_args.kwargs["json"]
    assert body["chat_template_kwargs"]["enable_thinking"] is False

    fake.reset_mock()
    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_BRAIN, prompt="x")
    body = fake.call_args.kwargs["json"]
    assert body["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_generate_thinking_explicit_override():
    """enable_thinking=True overrides REFLEX default of False."""
    client = LocalClient(host="x")
    fake = _fake_post(_chat_response())

    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_REFLEX, prompt="x", enable_thinking=True)
    body = fake.call_args.kwargs["json"]
    assert body["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_generate_with_system_prompt_prepends_system_message():
    client = LocalClient(host="x")
    fake = _fake_post(_chat_response())

    with patch.object(client._http, "post", fake):
        await client.generate(model=ModelName.LOCAL_REFLEX, prompt="hi", system="be terse")
    body = fake.call_args.kwargs["json"]
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


@pytest.mark.asyncio
async def test_generate_on_embed_tier_raises():
    client = LocalClient(host="x")
    with pytest.raises(ValueError, match="not a chat tier"):
        await client.generate(model=ModelName.LOCAL_EMBED, prompt="x")


@pytest.mark.asyncio
async def test_embed_happy_path_returns_vectors():
    client = LocalClient(host="100.119.114.125")
    vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    fake = _fake_post(_embed_response(vectors))

    with patch.object(client._http, "post", fake):
        result = await client.embed(model=ModelName.LOCAL_EMBED, inputs=["a", "b"])

    assert result == vectors
    assert fake.call_args.args[0] == "http://100.119.114.125:8003/v1/embeddings"
    body = fake.call_args.kwargs["json"]
    assert body["input"] == ["a", "b"]
    assert body["model"] == "qwen3-embed-0.6b"


@pytest.mark.asyncio
async def test_embed_on_chat_tier_raises():
    client = LocalClient(host="x")
    with pytest.raises(ValueError, match="not an embedding tier"):
        await client.embed(model=ModelName.LOCAL_REFLEX, inputs=["x"])


@pytest.mark.asyncio
async def test_generate_propagates_http_error():
    """5xx from llama-server bubbles up via raise_for_status."""
    client = LocalClient(host="x")
    response = MagicMock(spec=httpx.Response)
    response.status_code = 503
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=response)
    )
    fake = AsyncMock(return_value=response)

    with patch.object(client._http, "post", fake):
        with pytest.raises(httpx.HTTPStatusError):
            await client.generate(model=ModelName.LOCAL_REFLEX, prompt="x")
