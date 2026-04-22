"""Tests for STTProcessor — Whisper-based speech-to-text."""

from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest

from wake_word.stt_processor import STTProcessor


@pytest.fixture()
def stt() -> STTProcessor:
    return STTProcessor(model_name="base")


def _make_pcm_bytes(num_samples: int = 16000, value: int = 1000) -> bytes:
    """Generate fake 16-bit PCM audio data."""
    return struct.pack(f"<{num_samples}h", *([value] * num_samples))


@pytest.mark.asyncio()
async def test_transcribe_returns_text(stt: STTProcessor) -> None:
    """Successful transcription returns stripped text."""
    fake_result = {"text": "  Turn on the lights  "}
    with patch.object(stt, "_load_model") as mock_load:
        mock_model = MagicMock()
        mock_model.transcribe.return_value = fake_result
        mock_load.return_value = mock_model

        text = await stt.transcribe(_make_pcm_bytes())

    assert text == "Turn on the lights"


@pytest.mark.asyncio()
async def test_transcribe_empty_audio_returns_empty(stt: STTProcessor) -> None:
    """Empty audio input returns empty string without calling Whisper."""
    text = await stt.transcribe(b"")
    assert text == ""


@pytest.mark.asyncio()
async def test_transcribe_handles_whisper_error(stt: STTProcessor) -> None:
    """Whisper failure returns empty string, does not raise."""
    with patch.object(stt, "_load_model") as mock_load:
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Whisper crashed")
        mock_load.return_value = mock_model

        text = await stt.transcribe(_make_pcm_bytes())

    assert text == ""
