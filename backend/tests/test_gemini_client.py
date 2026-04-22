"""Tests for the GeminiClient — all SDK calls are mocked."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.gemini_client import GeminiClient, GeminiResponse


# ---------------------------------------------------------------------------
# Helpers to build fake SDK responses
# ---------------------------------------------------------------------------


def _make_usage(prompt_tokens: int = 10, candidate_tokens: int = 20):
    usage = MagicMock()
    usage.prompt_token_count = prompt_tokens
    usage.candidates_token_count = candidate_tokens
    return usage


def _make_candidate(finish_reason: str = "STOP"):
    cand = MagicMock()
    cand.finish_reason = finish_reason
    return cand


def _make_response(text: str = "hello", prompt_tokens: int = 10, candidate_tokens: int = 20):
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata = _make_usage(prompt_tokens, candidate_tokens)
    resp.candidates = [_make_candidate()]
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_genai_client():
    """Patch google.genai.Client and return the mock instance."""
    with patch("models.gemini_client.genai.Client") as MockClient:
        instance = MockClient.return_value
        # Set up the async generate_content method
        instance.aio.models.generate_content = AsyncMock(return_value=_make_response())
        yield instance


@pytest.fixture
def client(mock_genai_client) -> GeminiClient:
    return GeminiClient(api_key="test-key")


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_gemini_response(client: GeminiClient, mock_genai_client):
    result = await client.generate("Hello world")

    assert isinstance(result, GeminiResponse)
    assert result.text == "hello"
    assert result.model == "gemini-2.5-flash"
    assert result.tokens_input == 10
    assert result.tokens_output == 20
    assert result.duration_ms >= 0
    assert result.finish_reason == "STOP"


@pytest.mark.asyncio
async def test_generate_passes_config(client: GeminiClient, mock_genai_client):
    await client.generate(
        prompt="test",
        model="gemini-2.0-pro",
        system_instruction="Be helpful",
        temperature=0.5,
        max_output_tokens=1024,
    )

    call_kwargs = mock_genai_client.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == "gemini-2.0-pro"
    config = call_kwargs.kwargs["config"]
    assert config.system_instruction == "Be helpful"
    assert config.temperature == 0.5
    assert config.max_output_tokens == 1024


@pytest.mark.asyncio
async def test_generate_json_format(client: GeminiClient, mock_genai_client):
    await client.generate(prompt="test", response_format="json")

    call_kwargs = mock_genai_client.aio.models.generate_content.call_args
    config = call_kwargs.kwargs["config"]
    assert config.response_mime_type == "application/json"


@pytest.mark.asyncio
async def test_generate_with_different_model(client: GeminiClient, mock_genai_client):
    result = await client.generate("test", model="gemini-2.0-pro")
    assert result.model == "gemini-2.0-pro"


# ---------------------------------------------------------------------------
# generate_with_vision()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_with_vision(client: GeminiClient, mock_genai_client):
    fake_image = b"\x89PNG\r\n\x1a\n"
    result = await client.generate_with_vision(
        prompt="What's in this image?",
        images=[fake_image],
    )

    assert isinstance(result, GeminiResponse)
    call_kwargs = mock_genai_client.aio.models.generate_content.call_args
    contents = call_kwargs.kwargs["contents"]
    # First element is the text prompt, second is the image Part
    assert contents[0] == "What's in this image?"
    assert len(contents) == 2


@pytest.mark.asyncio
async def test_generate_with_multiple_images(client: GeminiClient, mock_genai_client):
    images = [b"img1", b"img2", b"img3"]
    await client.generate_with_vision(prompt="compare", images=images)

    call_kwargs = mock_genai_client.aio.models.generate_content.call_args
    contents = call_kwargs.kwargs["contents"]
    assert len(contents) == 4  # 1 text + 3 images


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_returns_matching_category(client: GeminiClient, mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = _make_response(text="urgent")

    result = await client.classify(
        text="Server is down!",
        categories=["urgent", "normal", "low"],
    )
    assert result == "urgent"


@pytest.mark.asyncio
async def test_classify_case_insensitive_match(client: GeminiClient, mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = _make_response(text="URGENT")

    result = await client.classify(
        text="Server is down!",
        categories=["urgent", "normal", "low"],
    )
    assert result == "urgent"


@pytest.mark.asyncio
async def test_classify_returns_raw_on_no_match(client: GeminiClient, mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = _make_response(text="unknown_cat")

    result = await client.classify(
        text="Something",
        categories=["a", "b", "c"],
    )
    assert result == "unknown_cat"


# ---------------------------------------------------------------------------
# Rate-limit retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_rate_limit(client: GeminiClient, mock_genai_client):
    """Should retry on 429 and succeed on the second attempt."""
    rate_limit_error = Exception("429 Resource Exhausted: rate limit exceeded")
    mock_genai_client.aio.models.generate_content.side_effect = [
        rate_limit_error,
        _make_response(text="success after retry"),
    ]

    result = await client.generate("test")
    assert result.text == "success after retry"
    assert mock_genai_client.aio.models.generate_content.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises(client: GeminiClient, mock_genai_client):
    """Should raise after exhausting all retries."""
    rate_limit_error = Exception("429 Resource Exhausted")
    mock_genai_client.aio.models.generate_content.side_effect = rate_limit_error

    with pytest.raises(Exception, match="429"):
        await client.generate("test")

    assert mock_genai_client.aio.models.generate_content.call_count == 3


@pytest.mark.asyncio
async def test_no_retry_on_non_retryable_error(client: GeminiClient, mock_genai_client):
    """Non-retryable errors should raise immediately."""
    mock_genai_client.aio.models.generate_content.side_effect = ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await client.generate("test")

    assert mock_genai_client.aio.models.generate_content.call_count == 1


@pytest.mark.asyncio
async def test_retry_on_server_error(client: GeminiClient, mock_genai_client):
    """Should retry on 500 server errors."""
    server_error = Exception("500 Internal Server Error")
    mock_genai_client.aio.models.generate_content.side_effect = [
        server_error,
        _make_response(text="recovered"),
    ]

    result = await client.generate("test")
    assert result.text == "recovered"


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_counts_from_usage_metadata(client: GeminiClient, mock_genai_client):
    mock_genai_client.aio.models.generate_content.return_value = _make_response(
        text="hi", prompt_tokens=42, candidate_tokens=7
    )

    result = await client.generate("test")
    assert result.tokens_input == 42
    assert result.tokens_output == 7


@pytest.mark.asyncio
async def test_token_counts_zero_when_no_metadata(client: GeminiClient, mock_genai_client):
    resp = _make_response()
    resp.usage_metadata = None
    mock_genai_client.aio.models.generate_content.return_value = resp

    result = await client.generate("test")
    assert result.tokens_input == 0
    assert result.tokens_output == 0


# ---------------------------------------------------------------------------
# GeminiResponse is frozen
# ---------------------------------------------------------------------------


def test_gemini_response_is_immutable():
    resp = GeminiResponse(
        text="hi", model="gemini-2.0-flash",
        tokens_input=10, tokens_output=5,
        duration_ms=100, finish_reason="STOP",
    )
    with pytest.raises(AttributeError):
        resp.text = "bye"  # type: ignore[misc]
