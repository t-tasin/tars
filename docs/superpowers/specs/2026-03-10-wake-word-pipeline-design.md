# Wake Word Voice Pipeline — Design Spec

## Overview

Always-on voice interface for T.A.R.S. running on Node 1. Listens for "Hey TARS" or "TARS" via USB mic, transcribes speech, routes through the orchestrator, and speaks the response via HomePod Mini AirPlay.

## Architecture

```
USB Mic (16kHz PCM)
  → Porcupine (always-on, ~1% CPU)
    → detects "Hey TARS" or "TARS"
      → Audio Recorder (VAD, max 15s)
        → Whisper STT (asyncio.to_thread)
          → orchestrator.process_message(source="wake_word")
            → gTTS / pyttsx3 fallback
              → pyatv AirPlay → HomePod Mini / USB speaker fallback
```

## Components

### 1. WakeWordListener (`backend/src/wake_word/listener.py`)

- Always-on asyncio background task started from `main.py` lifespan
- PyAudio opens USB mic at 16kHz, 512-frame chunks
- Porcupine loaded with two `.ppn` keyword files: `hey-tars` and `tars`
- Sensitivity: configurable, default 0.6 for both keywords
- On detection: plays confirmation tone, starts recording
- Records until silence (energy-based VAD, threshold 500, 1.5s silence gap) or max 15s
- Passes raw PCM bytes to STTProcessor
- Cleanup: releases Porcupine handle + PyAudio stream on shutdown

### 2. STTProcessor (`backend/src/wake_word/stt_processor.py`)

- Primary: OpenAI Whisper (model "base", local, zero API cost)
- Runs in `asyncio.to_thread()` — CPU-heavy, must not block event loop
- Saves PCM to temp WAV file → `whisper.transcribe()` → deletes temp file
- Returns transcribed text
- Logs confidence and duration

### 3. TTSOutput (`backend/src/wake_word/tts_output.py`)

- Primary TTS: gTTS (free, natural voice)
- Fallback TTS: pyttsx3 (offline, robotic)
- Primary output: AirPlay to HomePod Mini via `pyatv` at configured `HOMEPOD_HOST` IP
- Fallback output: USB speaker via PyAudio if HomePod unreachable
- HomePod connection: connect by configured IP, fall back to `pyatv.scan()` if unreachable

### 4. Wiring (`main.py` lifespan)

- On startup: detect USB mic (try opening PyAudio stream)
- If mic found + `.ppn` files exist: create WakeWordListener, start as `asyncio.create_task()`
- If no mic or no models: log warning, skip (HC-09 graceful degradation)
- On shutdown: cancel listener task, cleanup

## Config Additions (`config.py`)

```python
wake_word_model_paths: list[str] = ["/data/models/hey-tars_linux.ppn", "/data/models/tars_linux.ppn"]
wake_word_sensitivity: float = 0.6
wake_word_silence_threshold: int = 500
wake_word_silence_duration: float = 1.5
wake_word_max_record_seconds: float = 15.0
whisper_model: str = "base"
homepod_host: str = ""
usb_mic_device_index: int | None = None
```

## Error Handling

| Failure | Behavior |
|---------|----------|
| `.ppn` files missing | Log error, don't start listener |
| USB mic not found | Log warning, skip wake word |
| Whisper fails | Speak "Sorry, I couldn't understand that" |
| HomePod unreachable | Fall back to USB speaker |
| gTTS fails (no internet) | Fall back to pyttsx3 |
| Orchestrator error | Speak "Something went wrong, please try again" |

## Approval Handling

When orchestrator returns an approval-required response (Tier 2/3), the voice interface speaks: "I'll need your approval for that. I've sent the details to your phone." No voice-based approval — user approves via iOS app or Watch.

## Wake Word Model Training

1. Sign up at [Picovoice Console](https://console.picovoice.ai/)
2. Train two keywords: "Hey TARS" and "TARS" for Linux x86_64
3. Download `.ppn` files to `/data/models/` on Node 1
4. Train Mac versions separately for local development
