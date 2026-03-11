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
        import math
        import struct
        import subprocess
        import wave

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
            with wave.open(str(tmp_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_data)

            # Try platform audio players
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
