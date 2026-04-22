"""Always-on wake word listener using Porcupine.

Listens for "Hey TARS" or "TARS" on a USB microphone, records speech
until silence, transcribes via Whisper STT, routes through the
orchestrator, and speaks the response via TTS.

The Porcupine polling loop runs in a single long-lived thread to avoid
spawning a new thread per audio frame. Detected audio is handed off to
the asyncio event loop via an asyncio.Queue.

Runs as an asyncio background task started from main.py lifespan.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time
from pathlib import Path

import structlog

from config import get_settings
from orchestrator.engine import get_orchestrator
from wake_word.stt_processor import STTProcessor
from wake_word.tts_output import TTSOutput

log = structlog.get_logger()

SAMPLE_RATE = 16000
FRAME_LENGTH = 512  # Porcupine frame size
CHANNELS = 1
# pyaudio.paInt16 == 8; define locally to avoid module-level import
_PA_FORMAT_INT16 = 8


class WakeWordListener:
    """Porcupine-based wake word detector with VAD recording and full pipeline."""

    def __init__(self) -> None:
        settings = get_settings()

        self._access_key = settings.picovoice_access_key
        self._model_paths = settings.wake_word_model_paths
        self._sensitivity = settings.wake_word_sensitivity
        self._silence_threshold = settings.wake_word_silence_threshold
        self._silence_duration = settings.wake_word_silence_duration
        self._max_record_seconds = settings.wake_word_max_record_seconds
        self._mic_device_index = settings.usb_mic_device_index

        self._stt = STTProcessor(model_name=settings.whisper_model)
        self._tts = TTSOutput(homepod_host=settings.homepod_host)

        self._porcupine = None
        self._audio = None  # pyaudio.PyAudio instance
        self._stream = None
        self._running = False
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def start(self) -> None:
        """Start the wake word listening loop. Runs until stop() is called."""
        self._running = True
        log.info("wake_word_starting", models=self._model_paths)

        try:
            self._porcupine = await asyncio.to_thread(self._init_porcupine)

            import pyaudio

            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                rate=SAMPLE_RATE,
                channels=CHANNELS,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=FRAME_LENGTH,
                input_device_index=self._mic_device_index,
            )
            log.info("wake_word_listening")

            # Run Porcupine polling in a single long-lived thread.
            # When a wake word is detected, the thread records audio and
            # puts the result on the queue for the asyncio loop to process.
            loop = asyncio.get_running_loop()
            listener_task = loop.run_in_executor(None, self._polling_loop)

            # Process detected audio from the queue
            while self._running:
                try:
                    audio_data = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

                if audio_data is None:
                    break  # Sentinel — polling loop exited

                await self._process_audio(audio_data)

            # Wait for polling thread to finish
            await asyncio.wrap_future(listener_task)

        except asyncio.CancelledError:
            log.info("wake_word_cancelled")
            self._running = False
        except Exception:
            log.exception("wake_word_fatal_error")
        finally:
            await self._cleanup()

    def _polling_loop(self) -> None:
        """Synchronous Porcupine polling loop. Runs in a single thread.

        On wake word detection, records audio until silence and puts the
        result on the asyncio queue.
        """
        while self._running:
            try:
                pcm_bytes = self._stream.read(FRAME_LENGTH, exception_on_overflow=False)
                pcm_unpacked = struct.unpack_from(f"<{FRAME_LENGTH}h", pcm_bytes)

                keyword_index = self._porcupine.process(pcm_unpacked)
                if keyword_index >= 0:
                    keyword = self._model_paths[keyword_index] if keyword_index < len(self._model_paths) else "unknown"
                    log.info("wake_word_detected", keyword_index=keyword_index, keyword_path=keyword)

                    # Play confirmation tone (best-effort, sync context)
                    self._tts._generate_tone_sync()

                    # Record until silence
                    log.info("wake_word_recording_start")
                    audio_data = self._record_until_silence()
                    if audio_data:
                        log.info("wake_word_recording_complete", bytes=len(audio_data))
                        self._audio_queue.put_nowait(audio_data)
                    else:
                        log.warning("wake_word_no_speech_recorded")

            except Exception:
                if self._running:
                    log.exception("wake_word_polling_error")
                break

        # Sentinel to signal the async loop to exit
        self._audio_queue.put_nowait(None)

    async def stop(self) -> None:
        """Signal the listener to stop."""
        self._running = False
        log.info("wake_word_stop_requested")

    def _record_until_silence(self) -> bytes:
        """Record from mic until silence detected or max duration reached.

        Returns raw 16-bit PCM bytes.
        """
        frames: list[bytes] = []
        silence_start: float | None = None
        record_start = time.monotonic()

        while True:
            elapsed = time.monotonic() - record_start
            if elapsed >= self._max_record_seconds:
                log.info("wake_word_max_duration_reached", seconds=self._max_record_seconds)
                break

            frame = self._stream.read(FRAME_LENGTH, exception_on_overflow=False)
            frames.append(frame)

            if self._is_silence(frame):
                if silence_start is None:
                    silence_start = time.monotonic()
                elif time.monotonic() - silence_start >= self._silence_duration:
                    log.info("wake_word_silence_detected")
                    break
            else:
                silence_start = None

        return b"".join(frames)

    def _is_silence(self, frame: bytes) -> bool:
        """Check if a PCM frame is silence based on RMS energy."""
        num_samples = len(frame) // 2
        if num_samples == 0:
            return True
        samples = struct.unpack(f"<{num_samples}h", frame)
        rms = math.sqrt(sum(s * s for s in samples) / num_samples)
        return rms < self._silence_threshold

    async def _process_audio(self, pcm_audio: bytes) -> None:
        """Transcribe audio, route through orchestrator, speak response."""
        # STT
        text = await self._stt.transcribe(pcm_audio)
        if not text:
            await self._tts.speak("Sorry, I couldn't understand that.")
            return

        log.info("wake_word_transcribed", text=text)

        # Route through orchestrator
        try:
            orchestrator = get_orchestrator()
            response = await orchestrator.process_message(
                text=text,
                source="wake_word",
            )
        except Exception:
            log.exception("wake_word_orchestrator_error")
            await self._tts.speak("Something went wrong, please try again.")
            return

        # Handle approval-required responses (check content_type from ResponseFormatter)
        content_type = response.get("response", {}).get("content_type", "")
        if content_type == "approval":
            await self._tts.speak("I'll need your approval for that. I've sent the details to your phone.")
            return

        # Speak the response
        response_text = response.get("response", {}).get("text", "")
        if response_text:
            await self._tts.speak(response_text)

    def _init_porcupine(self):
        """Initialize Porcupine with keyword models. Runs in thread."""
        import pvporcupine

        # Filter to only existing model files
        existing_paths = [p for p in self._model_paths if Path(p).exists()]
        if not existing_paths:
            raise FileNotFoundError(f"No wake word model files found. Expected: {self._model_paths}")

        sensitivities = [self._sensitivity] * len(existing_paths)

        porcupine = pvporcupine.create(
            access_key=self._access_key,
            keyword_paths=existing_paths,
            sensitivities=sensitivities,
        )
        log.info(
            "porcupine_initialized",
            keywords=existing_paths,
            sample_rate=porcupine.sample_rate,
            frame_length=porcupine.frame_length,
        )
        return porcupine

    async def _cleanup(self) -> None:
        """Release all audio resources."""
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._audio is not None:
            self._audio.terminate()
            self._audio = None
        if self._porcupine is not None:
            self._porcupine.delete()
            self._porcupine = None
        await self._tts.close()
        log.info("wake_word_cleaned_up")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_listener: WakeWordListener | None = None


def get_wake_word_listener() -> WakeWordListener | None:
    """Return the module-level listener instance, or None if not initialized."""
    return _listener


def init_wake_word_listener() -> WakeWordListener:
    """Create and store the module-level WakeWordListener singleton."""
    global _listener
    _listener = WakeWordListener()
    return _listener


def check_mic_available(device_index: int | None = None) -> bool:
    """Check if a USB microphone is available.

    Args:
        device_index: Specific device index to check. None = default input.

    Returns:
        True if a mic can be opened, False otherwise.
    """
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        stream = pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=FRAME_LENGTH,
            input_device_index=device_index,
        )
        stream.close()
        pa.terminate()
        return True
    except Exception:
        log.debug("wake_word_no_mic_detected", device_index=device_index)
        return False


def check_models_exist(model_paths: list[str]) -> bool:
    """Check if at least one wake word model file exists."""
    return any(Path(p).exists() for p in model_paths)
