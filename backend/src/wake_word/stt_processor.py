"""Speech-to-text processor using OpenAI Whisper.

Runs transcription in asyncio.to_thread() to avoid blocking the event loop.
Whisper model is loaded lazily on first use with thread-safe locking.
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import wave
from pathlib import Path

import structlog

log = structlog.get_logger()

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit PCM


class STTProcessor:
    """Transcribe raw PCM audio to text using Whisper."""

    def __init__(self, model_name: str = "base") -> None:
        self._model_name = model_name
        self._model = None
        self._model_lock = threading.Lock()

    def _load_model(self):
        """Lazy-load the Whisper model with thread-safe locking.

        Returns the model instance.
        """
        if self._model is None:
            with self._model_lock:
                # Double-check after acquiring lock
                if self._model is None:
                    import whisper

                    log.info("whisper_loading_model", model=self._model_name)
                    self._model = whisper.load_model(self._model_name)
                    log.info("whisper_model_loaded", model=self._model_name)
        return self._model

    async def transcribe(self, pcm_audio: bytes) -> str:
        """Transcribe raw 16-bit PCM audio bytes to text.

        Args:
            pcm_audio: Raw 16-bit signed little-endian PCM at 16kHz mono.

        Returns:
            Transcribed text (stripped), or empty string on failure.
        """
        if not pcm_audio:
            return ""

        try:
            text = await asyncio.to_thread(self._transcribe_sync, pcm_audio)
            return text
        except Exception:
            log.exception("stt_transcription_failed")
            return ""

    def _transcribe_sync(self, pcm_audio: bytes) -> str:
        """Synchronous transcription — runs in a thread."""
        start = time.monotonic()
        tmp_path: Path | None = None

        try:
            # Write PCM to a temporary WAV file (Whisper needs a file path)
            _, tmp_str = tempfile.mkstemp(suffix=".wav")
            tmp_path = Path(tmp_str)

            num_samples = len(pcm_audio) // SAMPLE_WIDTH
            with wave.open(str(tmp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_audio)

            model = self._load_model()
            result = model.transcribe(
                str(tmp_path),
                language="en",
                fp16=False,
            )

            text = result.get("text", "").strip()
            duration_ms = int((time.monotonic() - start) * 1000)

            log.info(
                "stt_transcribed",
                text_length=len(text),
                audio_samples=num_samples,
                duration_ms=duration_ms,
                model=self._model_name,
            )
            return text

        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
