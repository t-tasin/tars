# Tech Stack

## Node 1 (Brain)
- Python 3.12+, asyncio + uvloop
- Node.js 22 LTS (for npx-based MCP servers)
- FastAPI 0.115+, Uvicorn
- PostgreSQL 16 via asyncpg + SQLAlchemy 2.0 + Alembic
- Redis 7 async client
- APScheduler 3.x
- python-telegram-bot 21.x
- httpx 0.27+ (incl. local llama.cpp client)
- google-genai 1.0+ (Gemini SDK)
- Claude Code CLI (headless, MCP-enhanced)
- `@modelcontextprotocol/*` MCP servers: GitHub, PostgreSQL, Brave Search, Filesystem
- apprise 1.9+ (notification fan-out)
- caldav, google-api-python-client
- teller-python, notion-client
- pvporcupine 3.x, pyaudio, openai-whisper
- pyatv 0.14+, pyttsx3 / gTTS
- PyAPNs2
- structlog, prometheus-fastapi-instrumentator
- qdrant-client (async)
- Docker + Docker Compose, cloudflared, tailscale

## Node 2 (Muscle)
- Python 3.12+, Redis 7.4+, Qdrant 1.11+
- llama.cpp (built w/ AVX2 + FMA for i7-7700T)
- Docker Engine (sandboxed containers)

## iOS App (`ios/`)
- Swift 5.10+, SwiftUI, iOS 17+, MVVM + Repository
- EventKit, HealthKit, Contacts, Speech, AVSpeechSynthesizer
- SiriKit + App Intents, WidgetKit, WatchKit + WatchConnectivity, Live Activities
- URLSession async/await (REST + WS), Keychain

## Public Dashboard (`web/`)
- Next.js 15 App Router (RSC)
- Tailwind CSS v4 + shadcn/ui
- react-three-fiber + drei (3D)
- framer-motion, tremor (charts)
- Vercel deploy

## Models (local GGUFs)
- Qwen3-1.7B-tars-v1 (LoRA persona) Q4_K_M — ~1.2GB (L0)
- Qwen3-8B-Instruct-2507 Q4_K_M — ~5.0GB (L1)
- Qwen3-Embedding-0.6B — ~0.6GB
- Whisper-small.en for STT (CUDA) — ~500MB VRAM on Quadro M620
- (Stretch bench) Qwen3-30B-A3B-Instruct Q4_K_M — ~18GB, mmap'd from NVMe
