"""Tests for WakeWordListener — Porcupine + VAD + pipeline integration."""
from __future__ import annotations

import struct
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock pyaudio before importing listener (requires system PortAudio library)
_mock_pyaudio = MagicMock()
_mock_pyaudio.paInt16 = 8
sys.modules.setdefault("pyaudio", _mock_pyaudio)

from wake_word.listener import WakeWordListener


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.picovoice_access_key = "test-key"
    s.wake_word_model_paths = ["/data/models/hey-tars.ppn", "/data/models/tars.ppn"]
    s.wake_word_sensitivity = 0.6
    s.wake_word_silence_threshold = 500
    s.wake_word_silence_duration = 1.5
    s.wake_word_max_record_seconds = 15.0
    s.whisper_model = "base"
    s.homepod_host = "192.168.12.50"
    s.usb_mic_device_index = None
    return s


@pytest.fixture()
def mock_settings() -> MagicMock:
    return _make_settings()


@pytest.fixture()
def listener(mock_settings: MagicMock) -> WakeWordListener:
    with patch("wake_word.listener.get_settings", return_value=mock_settings):
        return WakeWordListener()


def test_listener_init(listener: WakeWordListener) -> None:
    """Listener should store config values from settings."""
    assert listener._sensitivity == 0.6
    assert listener._silence_threshold == 500
    assert listener._max_record_seconds == 15.0


def test_is_silence_below_threshold(listener: WakeWordListener) -> None:
    """Frames with energy below threshold should be detected as silence."""
    # 512 samples of low amplitude (value=100, RMS = 100)
    frame = struct.pack("<512h", *([100] * 512))
    assert listener._is_silence(frame) is True


def test_is_silence_above_threshold(listener: WakeWordListener) -> None:
    """Frames with energy above threshold should NOT be detected as silence."""
    # 512 samples of high amplitude (value=5000, RMS = 5000)
    frame = struct.pack("<512h", *([5000] * 512))
    assert listener._is_silence(frame) is False


@pytest.mark.asyncio()
async def test_process_audio_calls_stt_and_orchestrator(listener: WakeWordListener) -> None:
    """After recording, audio should flow through STT -> orchestrator -> TTS."""
    mock_stt = AsyncMock()
    mock_stt.transcribe = AsyncMock(return_value="What's the weather?")
    listener._stt = mock_stt

    mock_tts = AsyncMock()
    mock_tts.speak = AsyncMock()
    listener._tts = mock_tts

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_message = AsyncMock(return_value={
        "response": {"text": "It's sunny and 72F.", "content_type": "text"},
        "agent_used": "daily_life",
        "model_used": "gemini_flash",
    })

    with patch("wake_word.listener.get_orchestrator", return_value=mock_orchestrator):
        await listener._process_audio(b"fake-pcm-data")

    mock_stt.transcribe.assert_called_once_with(b"fake-pcm-data")
    mock_orchestrator.process_message.assert_called_once_with(
        text="What's the weather?",
        source="wake_word",
    )
    mock_tts.speak.assert_called_once_with("It's sunny and 72F.")


@pytest.mark.asyncio()
async def test_process_audio_empty_transcription_speaks_error(listener: WakeWordListener) -> None:
    """If STT returns empty text, speak an error message."""
    mock_stt = AsyncMock()
    mock_stt.transcribe = AsyncMock(return_value="")
    listener._stt = mock_stt

    mock_tts = AsyncMock()
    mock_tts.speak = AsyncMock()
    listener._tts = mock_tts

    await listener._process_audio(b"fake-pcm-data")

    mock_tts.speak.assert_called_once_with("Sorry, I couldn't understand that.")


@pytest.mark.asyncio()
async def test_process_audio_approval_response(listener: WakeWordListener) -> None:
    """Approval-required responses should speak the approval redirect message."""
    mock_stt = AsyncMock()
    mock_stt.transcribe = AsyncMock(return_value="Send that email")
    listener._stt = mock_stt

    mock_tts = AsyncMock()
    mock_tts.speak = AsyncMock()
    listener._tts = mock_tts

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_message = AsyncMock(return_value={
        "response": {"text": "Draft ready for review.", "content_type": "approval"},
        "agent_used": "communication",
        "model_used": "claude_code",
    })

    with patch("wake_word.listener.get_orchestrator", return_value=mock_orchestrator):
        await listener._process_audio(b"fake-pcm-data")

    mock_tts.speak.assert_called_once_with(
        "I'll need your approval for that. I've sent the details to your phone."
    )


@pytest.mark.asyncio()
async def test_process_audio_orchestrator_error(listener: WakeWordListener) -> None:
    """Orchestrator errors should speak a generic error message."""
    mock_stt = AsyncMock()
    mock_stt.transcribe = AsyncMock(return_value="Do something")
    listener._stt = mock_stt

    mock_tts = AsyncMock()
    mock_tts.speak = AsyncMock()
    listener._tts = mock_tts

    mock_orchestrator = MagicMock()
    mock_orchestrator.process_message = AsyncMock(side_effect=RuntimeError("Boom"))

    with patch("wake_word.listener.get_orchestrator", return_value=mock_orchestrator):
        await listener._process_audio(b"fake-pcm-data")

    mock_tts.speak.assert_called_once_with("Something went wrong, please try again.")


def test_check_mic_available() -> None:
    """check_mic_available should return True if PyAudio can open a stream."""
    mock_instance = MagicMock()
    mock_stream = MagicMock()
    mock_instance.open.return_value = mock_stream
    _mock_pyaudio.PyAudio.return_value = mock_instance

    from wake_word.listener import check_mic_available
    result = check_mic_available()

    assert result is True
    mock_stream.close.assert_called_once()
    mock_instance.terminate.assert_called_once()


def test_check_mic_unavailable() -> None:
    """check_mic_available should return False if PyAudio fails."""
    _mock_pyaudio.PyAudio.side_effect = OSError("No audio devices")

    from wake_word.listener import check_mic_available
    result = check_mic_available()

    assert result is False
    # Reset side_effect for other tests
    _mock_pyaudio.PyAudio.side_effect = None
