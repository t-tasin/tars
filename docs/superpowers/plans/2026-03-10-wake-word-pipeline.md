# Wake Word Voice Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the always-on voice pipeline: Porcupine wake word detection → Whisper STT → orchestrator → gTTS/pyttsx3 → AirPlay to HomePod.

**Architecture:** Three modules in `backend/src/wake_word/` — `listener.py` (Porcupine + VAD recording), `stt_processor.py` (Whisper transcription), `tts_output.py` (gTTS + AirPlay output). Wired together in `main.py` as an asyncio background task that only starts if a USB mic is detected and `.ppn` model files exist.

**Tech Stack:** pvporcupine, pyaudio, openai-whisper, gTTS, pyttsx3, pyatv, asyncio, structlog

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/pyproject.toml` | Add `gTTS` dependency |
| Modify | `backend/src/config.py` | Add wake word config fields |
| Modify | `backend/tests/conftest.py` | Add wake word mock settings |
| Create | `backend/src/wake_word/stt_processor.py` | Whisper STT with asyncio.to_thread |
| Create | `backend/src/wake_word/tts_output.py` | gTTS/pyttsx3 + AirPlay/USB speaker output |
| Create | `backend/src/wake_word/listener.py` | Porcupine wake word + VAD + pipeline orchestration |
| Modify | `backend/src/wake_word/__init__.py` | Re-export main class |
| Modify | `backend/src/main.py` | Start wake word listener as background task |
| Create | `backend/tests/test_stt_processor.py` | STT unit tests |
| Create | `backend/tests/test_tts_output.py` | TTS unit tests |
| Create | `backend/tests/test_wake_word_listener.py` | Listener + integration tests |

---

## Chunk 1: Dependencies and Config

### Task 1: Add gTTS dependency

**Files:**
- Modify: `backend/pyproject.toml:36` (after pyttsx3 line)

- [ ] **Step 1: Add gTTS to dependencies**

In `backend/pyproject.toml`, add `gTTS` after the `pyttsx3` line:

```toml
    "pyttsx3>=2.90",
    "gTTS>=2.5.0",
```

- [ ] **Step 2: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore: add gTTS dependency for wake word TTS"
```

---

### Task 2: Add wake word config fields and update conftest

**Files:**
- Modify: `backend/src/config.py:56-57` (after picovoice_access_key)
- Modify: `backend/tests/conftest.py:49-51` (after picovoice_access_key mock)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_wake_word_config.py`:

```python
"""Tests for wake word configuration fields."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_wake_word_config_defaults():
    """Config should have wake word fields with sensible defaults."""
    from config import Settings

    with patch.dict("os.environ", {
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/tars",
        "REDIS_URL": "redis://localhost:6379/0",
        "CHROMA_AUTH_TOKEN": "test",
        "TARS_API_KEY": "test",
        "GEMINI_API_KEY": "test",
        "TELEGRAM_BOT_TOKEN": "test",
        "GMAIL_PERSONAL_CREDENTIALS": "dGVzdA==",
        "GMAIL_PROFESSIONAL_CREDENTIALS": "dGVzdA==",
        "ICLOUD_CALDAV_USER": "test",
        "ICLOUD_CALDAV_PASSWORD": "test",
        "GITHUB_PAT": "test",
        "NOTION_TOKEN": "test",
        "PLAID_CLIENT_ID": "test",
        "PLAID_SECRET": "test",
        "PLAID_ACCESS_TOKEN": "test",
        "OPENWEATHERMAP_API_KEY": "test",
        "PICOVOICE_ACCESS_KEY": "test-pv-key",
    }):
        settings = Settings()

    assert settings.picovoice_access_key == "test-pv-key"
    assert settings.wake_word_sensitivity == 0.6
    assert settings.wake_word_silence_threshold == 500
    assert settings.wake_word_silence_duration == 1.5
    assert settings.wake_word_max_record_seconds == 15.0
    assert settings.whisper_model == "base"
    assert settings.homepod_host == ""
    assert settings.usb_mic_device_index is None
    assert len(settings.wake_word_model_paths) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_wake_word_config.py -v`
Expected: FAIL — `Settings` has no attribute `wake_word_sensitivity`

- [ ] **Step 3: Add config fields to Settings**

In `backend/src/config.py`, after the `picovoice_access_key` line (line 57), add:

```python
    # --- Wake Word ---
    wake_word_model_paths: list[str] = [
        "/data/models/hey-tars_linux.ppn",
        "/data/models/tars_linux.ppn",
    ]
    wake_word_sensitivity: float = 0.6
    wake_word_silence_threshold: int = 500
    wake_word_silence_duration: float = 1.5
    wake_word_max_record_seconds: float = 15.0
    whisper_model: str = "base"
    homepod_host: str = ""
    usb_mic_device_index: int | None = None

```

- [ ] **Step 4: Add wake word fields to conftest.py `_MOCK_SETTINGS`**

In `backend/tests/conftest.py`, after line 49 (`_MOCK_SETTINGS.picovoice_access_key = "test"`), add:

```python
_MOCK_SETTINGS.wake_word_model_paths = []
_MOCK_SETTINGS.wake_word_sensitivity = 0.6
_MOCK_SETTINGS.wake_word_silence_threshold = 500
_MOCK_SETTINGS.wake_word_silence_duration = 1.5
_MOCK_SETTINGS.wake_word_max_record_seconds = 15.0
_MOCK_SETTINGS.whisper_model = "base"
_MOCK_SETTINGS.homepod_host = ""
_MOCK_SETTINGS.usb_mic_device_index = None
```

Note: `wake_word_model_paths` is `[]` (empty) in conftest so `check_models_exist()` returns False and no listener starts during tests.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_wake_word_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/config.py backend/tests/conftest.py backend/tests/test_wake_word_config.py
git commit -m "feat: add wake word configuration fields to Settings and conftest"
```

---

## Chunk 2: STT Processor

### Task 3: Implement STTProcessor

**Files:**
- Create: `backend/src/wake_word/stt_processor.py`
- Create: `backend/tests/test_stt_processor.py`

**Key patterns:**
- Runs Whisper in `asyncio.to_thread()` to avoid blocking the event loop (CLAUDE.md rule)
- Saves PCM to temp WAV, transcribes, deletes temp file
- Thread-safe lazy model loading with `threading.Lock`
- Logs confidence and duration via structlog
- Returns transcribed text or empty string on failure

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_stt_processor.py`:

```python
"""Tests for STTProcessor — Whisper-based speech-to-text."""
from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock, patch

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_stt_processor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wake_word.stt_processor'`

- [ ] **Step 3: Implement STTProcessor**

Create `backend/src/wake_word/stt_processor.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_stt_processor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/wake_word/stt_processor.py backend/tests/test_stt_processor.py
git commit -m "feat: add STTProcessor with Whisper transcription"
```

---

## Chunk 3: TTS Output

### Task 4: Implement TTSOutput

**Files:**
- Create: `backend/src/wake_word/tts_output.py`
- Create: `backend/tests/test_tts_output.py`

**Key patterns:**
- gTTS primary, pyttsx3 fallback for speech generation
- **Lazy imports** for gTTS and pyttsx3 inside sync methods (keeps module importable without these packages)
- pyatv AirPlay to HomePod primary, USB speaker fallback for audio output
- HomePod connects by configured IP, falls back to `pyatv.scan()` discovery
- **No `loop=` argument** to `pyatv.connect()` (deprecated/removed in pyatv 0.14+)
- All I/O async; blocking TTS runs in `asyncio.to_thread()`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tts_output.py`:

```python
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
    with patch("wake_word.tts_output.TTSOutput._gtts_sync") as mock_gtts:
        mock_gtts.return_value = b"fake-mp3-audio"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_tts_output.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement TTSOutput**

Create `backend/src/wake_word/tts_output.py`:

```python
"""Text-to-speech output with AirPlay streaming to HomePod Mini.

Primary TTS: gTTS (Google, free, natural voice).
Fallback TTS: pyttsx3 (offline, robotic).
Primary output: AirPlay to HomePod via pyatv.
Fallback output: local USB speaker via system audio player.

gTTS and pyttsx3 are imported lazily inside sync methods so that the module
remains importable in environments where these packages are not installed
(e.g. CI test runners).
"""
from __future__ import annotations

import asyncio
import io
import tempfile
import time
from pathlib import Path

import structlog

log = structlog.get_logger()


class TTSOutput:
    """Convert text to speech and stream to HomePod or local speaker."""

    def __init__(self, homepod_host: str = "") -> None:
        self._homepod_host = homepod_host
        self._atv_device = None  # cached pyatv device

    async def speak(self, text: str) -> None:
        """Convert text to audio and play through the best available output.

        Args:
            text: The text to speak aloud.
        """
        if not text or not text.strip():
            return

        start = time.monotonic()
        audio_data = await self._generate_audio(text)
        if not audio_data:
            log.error("tts_no_audio_generated", text_length=len(text))
            return

        # Try AirPlay to HomePod first, fall back to local speaker
        if self._homepod_host:
            try:
                await self._play_via_airplay(audio_data)
                duration_ms = int((time.monotonic() - start) * 1000)
                log.info("tts_spoke", output="airplay", duration_ms=duration_ms, text_length=len(text))
                return
            except Exception:
                log.warning("tts_airplay_failed_falling_back", host=self._homepod_host)

        await self._play_locally(audio_data)
        duration_ms = int((time.monotonic() - start) * 1000)
        log.info("tts_spoke", output="local", duration_ms=duration_ms, text_length=len(text))

    async def _generate_audio(self, text: str) -> bytes | None:
        """Generate audio bytes from text. gTTS primary, pyttsx3 fallback."""
        # Try gTTS first (natural voice, needs internet)
        try:
            audio = await asyncio.to_thread(self._gtts_sync, text)
            return audio
        except Exception:
            log.warning("tts_gtts_failed_falling_back_to_pyttsx3")

        # Fallback: pyttsx3 (offline, robotic)
        try:
            audio = await asyncio.to_thread(self._pyttsx3_sync, text)
            return audio
        except Exception:
            log.exception("tts_all_engines_failed")
            return None

    def _gtts_sync(self, text: str) -> bytes:
        """Generate MP3 audio using gTTS. Runs in thread."""
        from gtts import gTTS

        tts = gTTS(text=text, lang="en")
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        return buf.getvalue()

    def _pyttsx3_sync(self, text: str) -> bytes:
        """Generate WAV audio using pyttsx3. Runs in thread."""
        import pyttsx3

        _, tmp_str = tempfile.mkstemp(suffix=".wav")
        tmp_path = Path(tmp_str)
        try:
            engine = pyttsx3.init()
            engine.save_to_file(text, str(tmp_path))
            engine.runAndWait()
            return tmp_path.read_bytes()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    async def _play_via_airplay(self, audio_data: bytes) -> None:
        """Stream audio to HomePod Mini via AirPlay using pyatv."""
        import pyatv

        # Connect to HomePod by IP (or discover if cached device is stale)
        if self._atv_device is None:
            self._atv_device = await self._connect_homepod(pyatv)

        # Write audio to temp file for streaming
        _, tmp_str = tempfile.mkstemp(suffix=".mp3")
        tmp_path = Path(tmp_str)
        try:
            tmp_path.write_bytes(audio_data)
            await self._atv_device.stream.stream_file(str(tmp_path))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    async def _connect_homepod(self, pyatv_module) -> object:
        """Connect to HomePod by IP, fall back to network scan."""
        try:
            atvs = await pyatv_module.scan(
                hosts=[self._homepod_host], timeout=5,
            )
            if atvs:
                device = await pyatv_module.connect(atvs[0])
                log.info("tts_homepod_connected", host=self._homepod_host)
                return device
        except Exception:
            log.warning("tts_homepod_ip_scan_failed", host=self._homepod_host)

        # Fall back to full network scan
        log.info("tts_homepod_discovering")
        atvs = await pyatv_module.scan(timeout=10)
        for atv in atvs:
            if atv.device_info and atv.device_info.model_str and "HomePod" in str(atv.device_info.model_str):
                device = await pyatv_module.connect(atv)
                log.info("tts_homepod_discovered", name=atv.name)
                return device

        raise ConnectionError("No HomePod found on network")

    async def _play_locally(self, audio_data: bytes) -> None:
        """Play audio through the local default audio output device."""
        _, tmp_str = tempfile.mkstemp(suffix=".mp3")
        tmp_path = Path(tmp_str)
        try:
            tmp_path.write_bytes(audio_data)
            # macOS: afplay, Linux: ffplay/aplay
            for player_cmd in [["afplay"], ["ffplay", "-nodisp", "-autoexit"], ["aplay"]]:
                cmd = [*player_cmd, str(tmp_path)]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    if proc.returncode == 0:
                        return
                except FileNotFoundError:
                    continue

            log.error("tts_no_local_player_available")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    async def play_tone(self) -> None:
        """Play a short confirmation tone to acknowledge wake word detection.

        Uses a simple synthesized beep. Falls back silently if no audio output.
        """
        try:
            await asyncio.to_thread(self._generate_tone_sync)
        except Exception:
            log.debug("tts_confirmation_tone_failed")

    def _generate_tone_sync(self) -> None:
        """Generate and play a short confirmation beep. Runs in thread."""
        import struct
        import math

        # Generate a 200ms 880Hz sine wave (A5 note) as 16-bit PCM
        sample_rate = 16000
        duration = 0.2
        frequency = 880
        num_samples = int(sample_rate * duration)
        samples = [
            int(16000 * math.sin(2 * math.pi * frequency * i / sample_rate))
            for i in range(num_samples)
        ]
        pcm_data = struct.pack(f"<{num_samples}h", *samples)

        _, tmp_str = tempfile.mkstemp(suffix=".wav")
        tmp_path = Path(tmp_str)
        try:
            import wave

            with wave.open(str(tmp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_data)

            # Try platform audio players
            import subprocess

            for player_cmd in ["afplay", "aplay", "paplay"]:
                try:
                    subprocess.run(
                        [player_cmd, str(tmp_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    async def close(self) -> None:
        """Clean up pyatv connection."""
        if self._atv_device is not None:
            self._atv_device.close()
            self._atv_device = None
            log.info("tts_homepod_disconnected")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_tts_output.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/wake_word/tts_output.py backend/tests/test_tts_output.py
git commit -m "feat: add TTSOutput with gTTS/pyttsx3 and AirPlay/local playback"
```

---

## Chunk 4: Wake Word Listener

### Task 5: Implement WakeWordListener

**Files:**
- Create: `backend/src/wake_word/listener.py`
- Create: `backend/tests/test_wake_word_listener.py`

**Key patterns:**
- Single long-running thread for Porcupine polling loop (avoids thread-per-frame exhaustion)
- `asyncio.Queue` to hand off detected audio from polling thread to asyncio event loop
- Energy-based VAD for silence detection (threshold 500, 1.5s silence, 15s max)
- Porcupine loaded with two `.ppn` keyword files (configurable paths + sensitivity)
- Confirmation tone on wake word detection (per spec)
- Approval detection via `content_type == "approval"` (matches ResponseFormatter output)
- Singleton via `get_wake_word_listener()` / `init_wake_word_listener()`
- Graceful cleanup on shutdown (release Porcupine + close PyAudio)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_wake_word_listener.py`:

```python
"""Tests for WakeWordListener — Porcupine + VAD + pipeline integration."""
from __future__ import annotations

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    with patch("wake_word.listener.pyaudio") as mock_pa:
        mock_instance = MagicMock()
        mock_pa.PyAudio.return_value = mock_instance
        mock_stream = MagicMock()
        mock_instance.open.return_value = mock_stream

        from wake_word.listener import check_mic_available
        result = check_mic_available()

    assert result is True
    mock_stream.close.assert_called_once()
    mock_instance.terminate.assert_called_once()


def test_check_mic_unavailable() -> None:
    """check_mic_available should return False if PyAudio fails."""
    with patch("wake_word.listener.pyaudio") as mock_pa:
        mock_pa.PyAudio.side_effect = OSError("No audio devices")

        from wake_word.listener import check_mic_available
        result = check_mic_available()

    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_wake_word_listener.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement WakeWordListener**

Create `backend/src/wake_word/listener.py`:

```python
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

import pyaudio
import structlog

from config import get_settings
from orchestrator.engine import get_orchestrator
from wake_word.stt_processor import STTProcessor
from wake_word.tts_output import TTSOutput

log = structlog.get_logger()

SAMPLE_RATE = 16000
FRAME_LENGTH = 512  # Porcupine frame size
CHANNELS = 1
FORMAT = pyaudio.paInt16


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
        self._audio: pyaudio.PyAudio | None = None
        self._stream = None
        self._running = False
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def start(self) -> None:
        """Start the wake word listening loop. Runs until stop() is called."""
        self._running = True
        log.info("wake_word_starting", models=self._model_paths)

        try:
            self._porcupine = await asyncio.to_thread(self._init_porcupine)
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                rate=SAMPLE_RATE,
                channels=CHANNELS,
                format=FORMAT,
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
                        self._audio_queue.get(), timeout=1.0,
                    )
                except asyncio.TimeoutError:
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
                    keyword = (
                        self._model_paths[keyword_index]
                        if keyword_index < len(self._model_paths)
                        else "unknown"
                    )
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
            await self._tts.speak(
                "I'll need your approval for that. I've sent the details to your phone."
            )
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
            raise FileNotFoundError(
                f"No wake word model files found. Expected: {self._model_paths}"
            )

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
        pa = pyaudio.PyAudio()
        stream = pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=FORMAT,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_wake_word_listener.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/wake_word/listener.py backend/tests/test_wake_word_listener.py
git commit -m "feat: add WakeWordListener with Porcupine detection and VAD recording"
```

---

## Chunk 5: Wiring and Integration

### Task 6: Update __init__.py and wire into main.py

**Files:**
- Modify: `backend/src/wake_word/__init__.py`
- Modify: `backend/src/main.py:1-76`

- [ ] **Step 1: Update wake_word __init__.py**

Write `backend/src/wake_word/__init__.py`:

```python
"""Wake word voice pipeline — Porcupine detection, Whisper STT, gTTS/AirPlay output."""
from wake_word.listener import (
    WakeWordListener,
    check_mic_available,
    check_models_exist,
    get_wake_word_listener,
    init_wake_word_listener,
)
from wake_word.stt_processor import STTProcessor
from wake_word.tts_output import TTSOutput

__all__ = [
    "WakeWordListener",
    "STTProcessor",
    "TTSOutput",
    "check_mic_available",
    "check_models_exist",
    "get_wake_word_listener",
    "init_wake_word_listener",
]
```

- [ ] **Step 2: Update main.py**

Replace `backend/src/main.py` with:

```python
"""T.A.R.S. — FastAPI application entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from src.api.config_api import seed_default_config
from src.api.router import router
from src.config import get_settings
from src.db.session import async_session_factory, close_db, init_db
from src.integrations.apns_client import APNsClient
from src.integrations.notification_service import init_notification_service
from src.integrations.telegram_bot import TelegramGateway
from src.orchestrator.engine import get_orchestrator
from src.scheduler.jobs import create_scheduler
from src.utils.logger import setup_logging
from src.wake_word.listener import (
    check_mic_available,
    check_models_exist,
    get_wake_word_listener,
    init_wake_word_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup and shutdown lifecycle for the T.A.R.S. backend."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level)
    log = structlog.get_logger()

    await init_db()

    # Seed default config on first run
    async with async_session_factory() as session:
        await seed_default_config(session)

    # Initialise APNs client (None if key/config missing)
    apns_client: APNsClient | None = None
    if settings.apns_key_id and settings.apns_team_id and settings.apns_bundle_id:
        apns_client = APNsClient(
            key_path=settings.apns_key_path,
            key_id=settings.apns_key_id,
            team_id=settings.apns_team_id,
            bundle_id=settings.apns_bundle_id,
            use_sandbox=settings.apns_use_sandbox,
        )

    # Initialise notification service (Telegram + WebSocket + APNs)
    telegram = TelegramGateway(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    init_notification_service(telegram_gateway=telegram, apns_client=apns_client)

    # Start the orchestrator and scheduler
    orchestrator = get_orchestrator()
    scheduler = create_scheduler(orchestrator)
    scheduler.start()
    log.info("tars_online", node_role=settings.node_role, scheduler="started")

    # Start wake word listener (only if USB mic detected and models exist)
    wake_word_task: asyncio.Task | None = None
    if check_mic_available(settings.usb_mic_device_index):
        if check_models_exist(settings.wake_word_model_paths):
            ww_listener = init_wake_word_listener()
            wake_word_task = asyncio.create_task(ww_listener.start())
            log.info("wake_word_daemon_started")
        else:
            log.warning(
                "wake_word_skipped_no_models",
                expected=settings.wake_word_model_paths,
            )
    else:
        log.warning("wake_word_skipped_no_mic")

    yield

    # Shutdown wake word listener
    if wake_word_task is not None:
        ww_listener = get_wake_word_listener()
        if ww_listener:
            await ww_listener.stop()
        wake_word_task.cancel()
        try:
            await wake_word_task
        except asyncio.CancelledError:
            pass
        log.info("wake_word_daemon_stopped")

    scheduler.shutdown(wait=False)
    log.info("scheduler_stopped")
    await close_db()
    log.info("tars_shutting_down")


app = FastAPI(
    title="T.A.R.S. API",
    description="Tasin's Autonomous Resource System",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)
```

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/tasin/Desktop/Codebase.nosync/project-tars/tars/backend && .venv/bin/python -m pytest tests/test_stt_processor.py tests/test_tts_output.py tests/test_wake_word_listener.py tests/test_wake_word_config.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/wake_word/__init__.py backend/src/main.py
git commit -m "feat: wire wake word listener into main.py lifespan as background daemon"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Add gTTS dependency | — |
| 2 | Config fields + conftest update | 1 test |
| 3 | STTProcessor (Whisper, thread-safe) | 3 tests |
| 4 | TTSOutput (gTTS + AirPlay + confirmation tone) | 6 tests |
| 5 | WakeWordListener (single-thread polling + queue) | 9 tests |
| 6 | Wire into main.py | Integration run |

**Total: 19 tests across 4 test files**

## Review Fixes Applied

- Removed `loop=asyncio.get_event_loop()` from `pyatv.connect()` calls (pyatv 0.14+ crash)
- Lazy imports for gTTS/pyttsx3 inside sync methods (import safety in CI)
- Added wake word fields to `conftest.py` `_MOCK_SETTINGS` (prevent MagicMock iteration)
- Single long-lived thread for Porcupine polling via `run_in_executor` + `asyncio.Queue` (thread pool exhaustion)
- `threading.Lock` with double-check on Whisper `_load_model` (race condition)
- Approval detection uses `content_type == "approval"` instead of `approval_id` (matches ResponseFormatter)
- Added `play_tone()` / `_generate_tone_sync()` for confirmation beep on wake word detection (spec requirement)
