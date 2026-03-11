"""Tests for TTSOutput — text-to-speech with AirPlay output."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wake_word.tts_output import TTSOutput


@pytest.fixture()
def tts() -> TTSOutput:
    return TTSOutput(homepod_host="192.168.12.50")


@pytest.mark.asyncio()
async def test_generate_audio_gtts_primary(tts: TTSOutput) -> None:
    """gTTS should be used as primary TTS engine."""
    with patch.object(tts, "_gtts_sync", return_value=b"fake-mp3-audio") as mock_gtts:
        audio_bytes = await tts._generate_audio("Hello world")

    assert audio_bytes == b"fake-mp3-audio"
    mock_gtts.assert_called_once_with("Hello world")


@pytest.mark.asyncio()
async def test_generate_audio_pyttsx3_fallback(tts: TTSOutput) -> None:
    """pyttsx3 should be used when gTTS fails."""
    with (
        patch.object(tts, "_gtts_sync", side_effect=Exception("No internet")),
        patch.object(tts, "_pyttsx3_sync", return_value=b"fake-wav-audio"),
    ):
        audio_bytes = await tts._generate_audio("Hello world")

    assert audio_bytes == b"fake-wav-audio"


@pytest.mark.asyncio()
async def test_speak_routes_to_airplay(tts: TTSOutput) -> None:
    """speak() should try AirPlay first."""
    with (
        patch.object(tts, "_generate_audio", new_callable=AsyncMock, return_value=b"audio-data"),
        patch.object(tts, "_play_via_airplay", new_callable=AsyncMock) as mock_airplay,
    ):
        await tts.speak("Hello")

    mock_airplay.assert_called_once_with(b"audio-data")


@pytest.mark.asyncio()
async def test_speak_falls_back_to_local(tts: TTSOutput) -> None:
    """speak() should fall back to local playback if AirPlay fails."""
    with (
        patch.object(tts, "_generate_audio", new_callable=AsyncMock, return_value=b"audio-data"),
        patch.object(
            tts, "_play_via_airplay", new_callable=AsyncMock, side_effect=Exception("HomePod offline"),
        ),
        patch.object(tts, "_play_locally", new_callable=AsyncMock) as mock_local,
    ):
        await tts.speak("Hello")

    mock_local.assert_called_once_with(b"audio-data")


@pytest.mark.asyncio()
async def test_speak_empty_text_is_noop(tts: TTSOutput) -> None:
    """Empty text should not generate audio or play anything."""
    with patch.object(tts, "_generate_audio", new_callable=AsyncMock) as mock_gen:
        await tts.speak("")

    mock_gen.assert_not_called()


@pytest.mark.asyncio()
async def test_speak_no_homepod_goes_local(tts: TTSOutput) -> None:
    """When no homepod_host configured, skip AirPlay entirely."""
    tts_no_hp = TTSOutput(homepod_host="")
    with (
        patch.object(tts_no_hp, "_generate_audio", new_callable=AsyncMock, return_value=b"audio"),
        patch.object(tts_no_hp, "_play_locally", new_callable=AsyncMock) as mock_local,
    ):
        await tts_no_hp.speak("Hello")

    mock_local.assert_called_once_with(b"audio")
