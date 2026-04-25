"""Tests for the image processor executor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.executors.image_processor import ImageProcessor, _get_prompt, _parse_json_response, _is_under


@pytest.fixture
def executor():
    return ImageProcessor()


@pytest.fixture
def mock_wardrobe_response():
    """Mock Gemini Vision response for wardrobe cataloging."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "item_type": "shirt",
                                    "color": "navy blue",
                                    "brand": "Ralph Lauren",
                                    "season": ["spring", "fall"],
                                    "formality": "smart_casual",
                                    "material": "cotton",
                                    "condition": "good",
                                    "additional_notes": "button-down collar, slim fit",
                                }
                            ),
                        }
                    ],
                },
            }
        ],
    }


@pytest.fixture
def mock_receipt_response():
    """Mock Gemini Vision response for receipt OCR."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "merchant": "Whole Foods",
                                    "date": "2026-03-09",
                                    "total": "$45.67",
                                    "subtotal": "$42.30",
                                    "tax": "$3.37",
                                    "line_items": [
                                        {"name": "Organic Milk", "quantity": 1, "price": "$5.99"},
                                        {"name": "Avocados", "quantity": 3, "price": "$4.50"},
                                    ],
                                    "payment_method": "Apple Pay",
                                }
                            ),
                        }
                    ],
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_wardrobe_catalog(executor, mock_wardrobe_response, tmp_path):
    # Create a test image file
    image_path = tmp_path / "test_shirt.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal JPEG header

    payload = {
        "image_path": str(image_path),
        "task_type": "wardrobe_catalog",
        "gemini_api_key": "test-key",
    }

    mock_resp = httpx.Response(200, json=mock_wardrobe_response)

    with patch.object(executor, "_call_gemini_vision", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_wardrobe_response["candidates"][0]["content"]["parts"][0]["text"]

        # Patch allowed paths to include tmp_path
        with patch("src.executors.image_processor._is_under", return_value=True):
            result = await executor.execute("test-img-1", payload)

    assert result["status"] == "completed"
    assert result["success"] is True
    assert result["task_type"] == "wardrobe_catalog"
    assert result["metadata"]["item_type"] == "shirt"
    assert result["metadata"]["color"] == "navy blue"
    assert result["metadata"]["brand"] == "Ralph Lauren"


@pytest.mark.asyncio
async def test_receipt_ocr(executor, mock_receipt_response, tmp_path):
    image_path = tmp_path / "receipt.png"
    image_path.write_bytes(b"\x89PNG" + b"\x00" * 100)

    payload = {
        "image_path": str(image_path),
        "task_type": "receipt_ocr",
        "gemini_api_key": "test-key",
    }

    with patch.object(executor, "_call_gemini_vision", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_receipt_response["candidates"][0]["content"]["parts"][0]["text"]

        with patch("src.executors.image_processor._is_under", return_value=True):
            result = await executor.execute("test-img-2", payload)

    assert result["status"] == "completed"
    assert result["metadata"]["merchant"] == "Whole Foods"
    assert result["metadata"]["total"] == "$45.67"
    assert len(result["metadata"]["line_items"]) == 2


@pytest.mark.asyncio
async def test_missing_image(executor):
    payload = {
        "image_path": "/data/wardrobe/nonexistent.jpg",
        "task_type": "wardrobe_catalog",
        "gemini_api_key": "test-key",
    }

    result = await executor.execute("test-img-3", payload)

    assert result["status"] == "failed"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_missing_api_key(executor, tmp_path):
    image_path = tmp_path / "test.jpg"
    image_path.write_bytes(b"\xff\xd8" + b"\x00" * 50)

    payload = {
        "image_path": str(image_path),
        "task_type": "wardrobe_catalog",
        "gemini_api_key": "",
    }

    result = await executor.execute("test-img-4", payload)

    assert result["status"] == "failed"
    assert "gemini_api_key" in result["error"]


@pytest.mark.asyncio
async def test_unauthorized_path(executor, tmp_path):
    image_path = tmp_path / "evil.jpg"
    image_path.write_bytes(b"\xff\xd8" + b"\x00" * 50)

    payload = {
        "image_path": str(image_path),
        "task_type": "wardrobe_catalog",
        "gemini_api_key": "test-key",
    }

    # Don't patch _is_under — it should reject the tmp_path
    result = await executor.execute("test-img-5", payload)

    assert result["status"] == "failed"
    assert "outside allowed directories" in result["error"]


# ---------------------------------------------------------------------------
# save_image task_type — FashionAgent → worker image persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_image_writes_bytes_from_redis_to_target(executor, tmp_path):
    import fakeredis.aioredis

    image_path = tmp_path / "wardrobe" / "abc.jpg"
    image_bytes = b"\xff\xd8\xff\xe0JPEG-body"
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    await fake.setex("wardrobe_image:abc", 60, image_bytes)

    payload = {
        "task_type": "save_image",
        "image_redis_key": "wardrobe_image:abc",
        "image_path": str(image_path),
        "item_id": "abc",
    }

    def _from_url(url, decode_responses=False):
        return fake

    with (
        patch("redis.asyncio.from_url", side_effect=_from_url),
        patch("src.executors.image_processor._is_under", return_value=True),
    ):
        result = await executor.execute("job-save-1", payload)

    assert result["status"] == "completed"
    assert result["success"] is True
    assert result["bytes_written"] == len(image_bytes)
    assert image_path.read_bytes() == image_bytes
    # Key must be deleted after successful write
    assert await fake.exists("wardrobe_image:abc") == 0


@pytest.mark.asyncio
async def test_save_image_fails_when_redis_key_missing(executor, tmp_path):
    import fakeredis.aioredis

    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    payload = {
        "task_type": "save_image",
        "image_redis_key": "wardrobe_image:missing",
        "image_path": str(tmp_path / "w" / "missing.jpg"),
        "item_id": "missing",
    }

    with (
        patch("redis.asyncio.from_url", return_value=fake),
        patch("src.executors.image_processor._is_under", return_value=True),
    ):
        result = await executor.execute("job-save-2", payload)

    assert result["status"] == "failed"
    assert "bytes missing" in result["error"]


@pytest.mark.asyncio
async def test_save_image_rejects_path_outside_wardrobe(executor, tmp_path):
    payload = {
        "task_type": "save_image",
        "image_redis_key": "wardrobe_image:x",
        "image_path": str(tmp_path / "evil.jpg"),
        "item_id": "x",
    }
    result = await executor.execute("job-save-3", payload)
    assert result["status"] == "failed"
    assert "outside allowed directory" in result["error"]


@pytest.mark.asyncio
async def test_gemini_api_failure(executor, tmp_path):
    image_path = tmp_path / "test.jpg"
    image_path.write_bytes(b"\xff\xd8" + b"\x00" * 50)

    payload = {
        "image_path": str(image_path),
        "task_type": "wardrobe_catalog",
        "gemini_api_key": "test-key",
    }

    with patch.object(
        executor,
        "_call_gemini_vision",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("timeout"),
    ):
        with patch("src.executors.image_processor._is_under", return_value=True):
            result = await executor.execute("test-img-6", payload)

    assert result["status"] == "failed"
    assert "Gemini Vision call failed" in result["error"]


def test_get_prompt_wardrobe():
    prompt = _get_prompt("wardrobe_catalog")
    assert "clothing item" in prompt
    assert "item_type" in prompt


def test_get_prompt_receipt():
    prompt = _get_prompt("receipt_ocr")
    assert "receipt" in prompt.lower()
    assert "merchant" in prompt


def test_get_prompt_with_extra():
    prompt = _get_prompt("visual_context", "Focus on the background")
    assert "Focus on the background" in prompt


def test_parse_json_response_clean():
    text = '{"key": "value"}'
    result = _parse_json_response(text)
    assert result == {"key": "value"}


def test_parse_json_response_markdown_fenced():
    text = '```json\n{"key": "value"}\n```'
    result = _parse_json_response(text)
    assert result == {"key": "value"}


def test_parse_json_response_invalid():
    result = _parse_json_response("not json at all")
    assert "raw_text" in result


def test_is_under():
    assert _is_under(Path("/data/wardrobe/shirt.jpg"), Path("/data/wardrobe"))
    assert not _is_under(Path("/tmp/evil.jpg"), Path("/data/wardrobe"))
