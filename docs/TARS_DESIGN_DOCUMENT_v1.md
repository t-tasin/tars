# T.A.R.S. — Design Document
## Tasin's Autonomous Resource System

**Version:** 1.0  
**Author:** Tasin (KM Khalid Saifullah) & Claude  
**Date:** March 9, 2026  
**Status:** DRAFT — Derived from Requirements Document v2.1 (LOCKED)  
**Purpose:** Technical blueprint for implementation. This document, combined with the Requirements Document v2.1, provides Claude Code with everything needed to build T.A.R.S.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Technology Stack](#2-technology-stack)
3. [Project Repository Structure](#3-project-repository-structure)
4. [Database Schema](#4-database-schema)
5. [API Contract](#5-api-contract)
6. [Agent System Design](#6-agent-system-design)
7. [Docker Compose Configuration](#7-docker-compose-configuration)
8. [CI/CD Pipeline](#8-cicd-pipeline)
9. [iOS App Architecture](#9-ios-app-architecture)
10. [Wake Word System Design](#10-wake-word-system-design)
11. [Integration Patterns](#11-integration-patterns)
12. [Security Design](#12-security-design)
13. [Monitoring & Observability](#13-monitoring--observability)

---

## 1. System Architecture

### 1.1 High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                                     │
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ iOS App  │  │  Apple   │  │ Telegram │  │  Siri    │  │  HomePod    │  │
│  │ (SwiftUI)│  │  Watch   │  │   Bot    │  │ Shortcut │  │  Mini       │  │
│  │          │  │ Companion│  │ @TarsBot │  │  Bridge  │  │ (AirPlay)   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘  │
│       │              │             │              │               │          │
└───────┼──────────────┼─────────────┼──────────────┼───────────────┼──────────┘
        │              │             │              │               │
        │  REST/WS     │  WatchConn  │  HTTP Long   │  HTTP         │ AirPlay
        │  (Tailscale  │  ectivity   │  Polling     │  Callback     │ Audio
        │  or CF Tun)  │             │              │               │
        ▼              ▼             ▼              ▼               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    NODE 1 — "BRAIN" (10.0.1.1)                              │
│                    HP Z2 Mini G3 — Ubuntu Server 24.04                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    API GATEWAY (FastAPI)                             │    │
│  │  REST Endpoints (/message, /briefing, /approvals, /health, etc.)    │    │
│  │  WebSocket Server (/stream — real-time push to clients)             │    │
│  │  APNs Push Notification Dispatcher                                  │    │
│  └──────────────────────────────┬──────────────────────────────────────┘    │
│                                 │                                           │
│  ┌──────────────────────────────▼──────────────────────────────────────┐    │
│  │                    ORCHESTRATOR (Python asyncio)                     │    │
│  │                                                                     │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │    │
│  │  │   Intent     │  │   Model      │  │   Approval Queue         │  │    │
│  │  │  Classifier  │──▶   Router     │  │   Manager                │  │    │
│  │  │  (rule-based │  │              │  │   (Tier 1/2/3 enforce)   │  │    │
│  │  │  + keyword)  │  │  ┌─────────┐ │  └──────────────────────────┘  │    │
│  │  └──────────────┘  │  │ Claude  │ │                                │    │
│  │                     │  │ Code    │ │  ┌──────────────────────────┐  │    │
│  │  ┌──────────────┐  │  │ Spawner │ │  │   Scheduler Daemon       │  │    │
│  │  │  Context     │  │  ├─────────┤ │  │   (APScheduler)          │  │    │
│  │  │  Builder     │  │  │ Gemini  │ │  │   Cron: briefing, email  │  │    │
│  │  │  (per-agent  │  │  │ API     │ │  │   poll, jobs, health     │  │    │
│  │  │   scoping)   │  │  │ Client  │ │  └──────────────────────────┘  │    │
│  │  └──────────────┘  │  ├─────────┤ │                                │    │
│  │                     │  │ Local   │ │  ┌──────────────────────────┐  │    │
│  │                     │  │ Handler │ │  │   Integration Layer      │  │    │
│  │                     │  └─────────┘ │  │   CalDAV, Gmail, GitHub  │  │    │
│  │                     └──────────────┘  │   Notion, Weather, Plaid │  │    │
│  │                                       │   Grafana/Loki           │  │    │
│  └───────────────────────────────────────┴──────────────────────────┘  │    │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────────────┐      │
│  │   PostgreSQL     │  │  Porcupine   │  │  Telegram Bot Gateway    │      │
│  │   (State DB)     │  │  Wake Word   │  │  (python-telegram-bot)   │      │
│  │                  │  │  Daemon      │  │                          │      │
│  │                  │  │  USB Mic →   │  │                          │      │
│  │                  │  │  STT → Orch  │  │                          │      │
│  └──────────────────┘  └──────────────┘  └──────────────────────────┘      │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Cloudflare Tunnel (cloudflared) — exposes API externally            │   │
│  │  Tailscale — mesh VPN to Node 2, iPhone, Wooster server             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
        │                                         ▲
        │  Redis Queue (Bull)                     │  Redis Pub/Sub (results)
        │  Jobs: code-exec, research,             │
        │  diagnostics, image-proc, job-scrape    │
        ▼                                         │
┌─────────────────────────────────────────────────────────────────────────────┐
│                    NODE 2 — "MUSCLE" (10.0.1.2)                             │
│                    HP Z2 Mini G3 — Ubuntu Server 24.04                      │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │   Redis 7.x      │  │   ChromaDB       │  │   Job Worker Daemon      │  │
│  │   (Job Queue +   │  │   (Vector Store) │  │   (Bull worker process)  │  │
│  │    Pub/Sub)       │  │   Semantic       │  │   Picks up jobs from     │  │
│  │                   │  │   Memory         │  │   Redis, dispatches to   │  │
│  │                   │  │                  │  │   Docker containers      │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │   Docker Engine — Sandboxed Agent Execution Containers               │   │
│  │                                                                      │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │   │
│  │  │ Code Agent │  │ Research   │  │ Diagnostics│  │ Job Scraper  │  │   │
│  │  │ Container  │  │ Container  │  │ Container  │  │ Container    │  │   │
│  │  │ (repo      │  │            │  │            │  │              │  │   │
│  │  │  clone +   │  │            │  │            │  │              │  │   │
│  │  │  Claude    │  │            │  │            │  │              │  │   │
│  │  │  Code)     │  │            │  │            │  │              │  │   │
│  │  └────────────┘  └────────────┘  └────────────┘  └──────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │   Persistent Volume Storage                                          │   │
│  │   /data/wardrobe/   — Wardrobe images                               │   │
│  │   /data/outputs/    — Agent output files, diffs, reports             │   │
│  │   /data/repos/      — Cloned repositories for coding agents          │   │
│  │   /data/logs/       — Centralized log storage                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                       EXTERNAL SERVICES                                     │
│                                                                             │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│  │  iCloud    │ │  Gmail API │ │  GitHub    │ │  Notion    │              │
│  │  CalDAV    │ │  (2 accts) │ │  API       │ │  API       │              │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│  │  Gemini    │ │  OpenWeath │ │  Plaid     │ │  Grafana/  │              │
│  │  API       │ │  erMap API │ │  API       │ │  Loki API  │              │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│  │  Picovoice │ │  APNs      │ │  Telegram  │ │  GHCR      │              │
│  │  Console   │ │  (Apple)   │ │  Bot API   │ │  (Docker)  │              │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘              │
│  ┌────────────┐ ┌────────────┐                                             │
│  │  SerpAPI / │ │  Cloudflare│                                             │
│  │  Job Boards│ │  Tunnel    │                                             │
│  └────────────┘ └────────────┘                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow Diagrams

#### 1.2.1 User Message Flow (iOS App → Response)

```
iOS App                    Node 1 (Brain)                          Node 2 (Muscle)
  │                            │                                       │
  │  POST /message             │                                       │
  │  {text, device_token}      │                                       │
  │───────────────────────────▶│                                       │
  │                            │  Intent Classifier                    │
  │                            │  ─────────────────                    │
  │                            │  Parses intent + entities             │
  │                            │         │                             │
  │                            │  Model Router                         │
  │                            │  ────────────                         │
  │                            │  Selects: Claude | Gemini | Local     │
  │                            │         │                             │
  │                            │  ┌──────┴────────────┐                │
  │                            │  │ IF needs Node 2:  │                │
  │                            │  │ Enqueue Redis job  │───────────────▶│
  │                            │  └───────────────────┘                │  Execute in Docker
  │                            │                                       │  Return via pub/sub
  │                            │◀──────────────────────────────────────│
  │                            │                                       │
  │                            │  Context Builder                      │
  │                            │  ───────────────                      │
  │                            │  Queries state DB, builds prompt      │
  │                            │         │                             │
  │                            │  AI Model Invocation                  │
  │                            │  ──────────────────                   │
  │                            │  Claude subprocess OR Gemini REST     │
  │                            │         │                             │
  │                            │  Approval Check                       │
  │                            │  ──────────────                       │
  │                            │  IF action has side effects:          │
  │                            │    → Queue in approvals table         │
  │                            │    → Push to client for review        │
  │                            │  ELSE:                                │
  │                            │    → Return response directly         │
  │                            │         │                             │
  │  WS push or HTTP response  │         │                             │
  │◀───────────────────────────│─────────┘                             │
  │                            │                                       │
```

#### 1.2.2 Morning Briefing Flow

```
5:45 AM                                    5:50 AM                        6:00 AM
  │                                           │                              │
  │  Scheduler fires                          │                              │
  │  outfit_agent + health_agent              │  Scheduler fires             │  User dismisses alarm
  │         │                                 │  briefing_agent              │         │
  │         ▼                                 │         │                    │         ▼
  │  ┌─────────────────────────┐              │         ▼                    │  iOS app fetches
  │  │ LOCAL: Parallel fetch   │              │  ┌────────────────────┐      │  GET /briefing
  │  │  ├─ CalDAV (calendar)   │              │  │ Gemini Pro:        │      │         │
  │  │  ├─ Gmail API (emails)  │              │  │ Compose briefing   │      │         ▼
  │  │  ├─ OpenWeather (wx)    │              │  │ from structured    │      │  AVSpeechSynthesizer
  │  │  ├─ Grafana (health)    │              │  │ data payload       │      │  speaks briefing
  │  │  ├─ Notion (tasks)      │              │  └────────┬───────────┘      │         │
  │  │  ├─ GitHub (notifs)     │              │           │                  │         ▼
  │  │  ├─ Plaid (txns)        │              │  Store in briefings table    │  AirPlay → HomePod
  │  │  ├─ HealthKit cache     │              │  Push notification to iOS    │  Mini plays audio
  │  │  └─ Job matches cache   │              │  "Briefing ready"            │
  │  └─────────────────────────┘              │                              │
  │         │                                 │                              │
  │         ▼                                 │                              │
  │  Gemini Vision: outfit                    │                              │
  │  suggestion from wardrobe                 │                              │
  │  + weather + calendar                     │                              │
  │         │                                 │                              │
  │         ▼                                 │                              │
  │  Store outfit in state DB                 │                              │
  │  Store health summary                     │                              │
```

#### 1.2.3 Approval Flow

```
Agent                 Node 1                  iOS App / Telegram       Apple Watch
  │                     │                          │                      │
  │  Proposed action    │                          │                      │
  │  (e.g., send email) │                          │                      │
  │────────────────────▶│                          │                      │
  │                     │  INSERT INTO approvals   │                      │
  │                     │  status='pending'        │                      │
  │                     │  risk_tier=2             │                      │
  │                     │  expires_at=now()+1h     │                      │
  │                     │         │                │                      │
  │                     │  WS push + APNs push     │                      │
  │                     │─────────────────────────▶│                      │
  │                     │──────────────────────────┼─────────────────────▶│
  │                     │                          │                      │
  │                     │       User taps Approve  │                      │
  │                     │◀─────────────────────────│  (or Watch: Approve) │
  │                     │                          │                      │
  │                     │  UPDATE approvals        │                      │
  │                     │  status='approved'       │                      │
  │                     │  decision_at=now()       │                      │
  │                     │         │                │                      │
  │                     │  Execute action          │                      │
  │  Execute            │  (send email via Gmail)  │                      │
  │◀────────────────────│         │                │                      │
  │                     │  UPDATE approvals        │                      │
  │                     │  status='executed'       │                      │
  │                     │         │                │                      │
  │                     │  WS push: "Email sent"   │                      │
  │                     │─────────────────────────▶│                      │
```

### 1.3 Network Topology

```
                     ┌──────────────────────────────────┐
                     │         INTERNET                  │
                     │    (T-Mobile CGNAT — no inbound)  │
                     └──────────┬───────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Cloudflare   │    │   Tailscale      │    │  Cloudflare      │
│  Tunnel       │    │   Mesh VPN       │    │  Tunnel          │
│  (Node 1)     │    │   (all devices)  │    │  (Wooster Svr)   │
│               │    │                  │    │                  │
│  tars.domain  │    │  100.x.y.z/24   │    │  Loki/Grafana    │
│  .com         │    │  overlay network │    │  APIs            │
└───────┬───────┘    └────────┬─────────┘    └────────┬─────────┘
        │                     │                       │
        ▼                     ▼                       ▼
  ┌──────────┐    ┌──────────────────────┐    ┌──────────────┐
  │ Node 1   │◀──▶│ Private LAN          │    │ Wooster      │
  │ 10.0.1.1 │    │ Gigabit Ethernet     │    │ Server       │
  └──────────┘    │ 10.0.1.0/24          │    │ (AtlasDesk)  │
        ▲         └──────────┬───────────┘    └──────────────┘
        │                    │
   USB Mic                   ▼
   + HomePod          ┌──────────┐
   (same WiFi)        │ Node 2   │
                      │ 10.0.1.2 │
                      └──────────┘
```

---

## 2. Technology Stack

### 2.1 Node 1 — Brain (Orchestration Server)

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **OS** | Ubuntu Server | 24.04 LTS | Base operating system |
| **Runtime** | Python | 3.12+ | Orchestrator, all backend services |
| **MCP Runtime** | Node.js | 22 LTS | Required for npx-based MCP servers (Claude Code agents) |
| **Async Framework** | asyncio + uvloop | latest | High-performance event loop |
| **API Framework** | FastAPI | 0.115+ | REST API + WebSocket server |
| **ASGI Server** | Uvicorn | 0.30+ | Production ASGI server |
| **Database** | PostgreSQL | 16 | State database, all persistent data |
| **DB Driver** | asyncpg | 0.30+ | Async PostgreSQL driver |
| **ORM** | SQLAlchemy | 2.0+ | Schema definition, migrations |
| **Migrations** | Alembic | 1.14+ | Database schema migrations |
| **Scheduler** | APScheduler | 4.0+ | Cron jobs (briefing, email poll, jobs scan) |
| **Telegram** | python-telegram-bot | 21.x | Telegram bot gateway |
| **CalDAV** | caldav (Python) | 1.4+ | iCloud Calendar server-side access |
| **Gmail** | google-api-python-client | 2.x | Gmail API integration |
| **HTTP Client** | httpx | 0.27+ | Async HTTP for external APIs |
| **WebSocket** | FastAPI WebSocket | built-in | Real-time client push |
| **Push Notifications** | PyAPNs2 | 0.9+ | Apple Push Notification Service |
| **Wake Word** | pvporcupine | 3.x | Porcupine wake word detection |
| **Audio Capture** | pyaudio | 0.2+ | USB mic audio stream |
| **STT** | openai-whisper (local) | latest | Speech-to-text (fallback: google-cloud-speech) |
| **TTS** | pyttsx3 or gTTS | latest | Server-side TTS for HomePod audio |
| **AirPlay** | pyatv | 0.14+ | AirPlay 2 audio streaming to HomePod |
| **Secrets** | python-dotenv + sops | latest | Environment variable + encrypted secrets |
| **Redis Client** | redis-py | 5.x | Connect to Redis on Node 2 |
| **Job Queue Client** | bullmq (via redis-py) | custom | Enqueue jobs to Node 2 |
| **Gemini SDK** | google-generativeai | 0.8+ | Gemini Flash/Pro/Vision API |
| **Claude Code** | claude CLI | latest (Max 5x) | Headless subprocess spawning |
| **MCP Servers** | @modelcontextprotocol/* | latest | Claude Code tool access (GitHub, PostgreSQL, Brave Search, filesystem) |
| **Notifications** | apprise | 1.9+ | Unified alert fan-out (Telegram, email, future channels) |
| **Notion** | notion-client | 2.x | Notion API integration |
| **Plaid** | plaid-python | 26.x | Financial transaction access |
| **Weather** | (httpx direct) | — | OpenWeatherMap REST API |
| **Containerization** | Docker + Docker Compose | 27.x / 2.x | Service deployment |
| **Tunnel** | cloudflared | latest | Cloudflare Tunnel daemon |
| **VPN** | tailscale | latest | Mesh VPN |

### 2.2 Node 2 — Muscle (Execution Server)

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **OS** | Ubuntu Server | 24.04 LTS | Base operating system |
| **Runtime** | Python | 3.12+ | Job worker daemon |
| **Redis** | Redis | 7.4+ | Job queue, pub/sub, caching |
| **Vector DB** | ChromaDB | 0.5+ | Semantic memory / vector store |
| **Embeddings** | sentence-transformers | 3.x | all-MiniLM-L6-v2 for embeddings |
| **Docker** | Docker Engine | 27.x | Sandboxed agent containers |
| **Docker Compose** | Docker Compose | 2.x | Service orchestration |
| **Job Worker** | Custom Python (Bull-compatible) | — | Processes jobs from Redis queue |
| **VPN** | tailscale | latest | Mesh VPN |

### 2.3 iOS App

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Language** | Swift | 5.10+ | Primary language |
| **UI Framework** | SwiftUI | iOS 17+ | Declarative UI |
| **Architecture** | MVVM + Repository | — | Clean separation of concerns |
| **Networking** | URLSession + async/await | built-in | REST API + WebSocket |
| **WebSocket** | URLSessionWebSocketTask | built-in | Real-time updates |
| **Push** | UserNotifications + APNs | built-in | Push notifications |
| **Calendar** | EventKit | built-in | iCloud Calendar native access |
| **Health** | HealthKit | built-in | Sleep, steps, workouts |
| **Contacts** | Contacts framework | built-in | Apple Contacts sync |
| **Camera** | AVFoundation + PhotosUI | built-in | Wardrobe / receipt photos |
| **Speech** | Speech framework | built-in | Speech-to-text |
| **TTS** | AVSpeechSynthesizer | built-in | Voice briefing output |
| **Siri** | SiriKit + App Intents | built-in | Siri Shortcuts bridge |
| **Watch** | WatchKit + WatchConnectivity | watchOS 10+ | Apple Watch companion |
| **Widgets** | WidgetKit | built-in | Home screen widgets |
| **Keychain** | Security framework | built-in | Secure credential storage |
| **Distribution** | TestFlight | — | Beta distribution |

### 2.4 External Services & API Keys Required

| Service | Auth Method | Key/Token Type |
|---------|------------|---------------|
| iCloud CalDAV | App-Specific Password | Stored in sops-encrypted env |
| Gmail API (×2) | OAuth 2.0 (offline) | Refresh tokens in encrypted store |
| GitHub API | Personal Access Token | PAT with repo+notifications scope |
| Gemini API | API Key | Google AI Studio key |
| Notion API | Integration Token | Internal integration |
| Plaid API | Client ID + Secret | Plaid Link access token |
| OpenWeatherMap | API Key | Free tier key |
| Picovoice | Access Key | Porcupine license key |
| Telegram Bot | Bot Token | BotFather token |
| APNs | .p8 Key File | Apple Developer key |
| Cloudflare | Tunnel Token | cloudflared service token |
| GHCR | GitHub PAT | packages:write scope |
| SerpAPI (optional) | API Key | Job search / web search |

---

## 3. Project Repository Structure

### 3.1 Monorepo Strategy

Single GitHub repository: `github.com/tasin/tars`

Rationale: Single-user project, unified CI/CD, shared types/constants, simpler deployment coordination between nodes.

### 3.2 Complete Directory Tree

```
tars/
├── .github/
│   └── workflows/
│       ├── build-and-push.yml          # CI: build Docker images, push to GHCR
│       ├── lint.yml                     # PR checks: ruff, mypy, swiftlint
│       └── test.yml                     # Unit + integration tests
│
├── CLAUDE.md                            # Claude Code project context file
├── .mcp.json                            # MCP server config for Claude Code agents
├── README.md                            # Project overview + setup guide
├── LICENSE                              # Private / proprietary
├── .gitignore
├── .env.example                         # Template for all required env vars
│
├── backend/                             # All Python backend code
│   ├── pyproject.toml                   # Python project config (uv / pip)
│   ├── uv.lock                          # Lockfile (if using uv)
│   ├── alembic.ini                      # Alembic migration config
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                    # Migration files
│   │       └── 001_initial_schema.py
│   │
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                      # Entrypoint: starts FastAPI + scheduler + WS
│   │   ├── config.py                    # Settings (pydantic-settings, env loading)
│   │   ├── dependencies.py              # FastAPI dependency injection
│   │   │
│   │   ├── api/                         # REST API layer
│   │   │   ├── __init__.py
│   │   │   ├── router.py               # Main router aggregating all sub-routers
│   │   │   ├── auth.py                 # API key + device token middleware
│   │   │   ├── messages.py             # POST /message
│   │   │   ├── briefings.py            # GET /briefing
│   │   │   ├── schedule.py             # GET /schedule
│   │   │   ├── approvals.py            # GET/POST /approvals
│   │   │   ├── health.py               # GET /health
│   │   │   ├── jobs.py                 # GET /jobs, POST /jobs/:id/action
│   │   │   ├── wardrobe.py             # POST /wardrobe/upload, GET /outfit
│   │   │   ├── finance.py              # GET /finance/summary
│   │   │   ├── config_api.py           # GET/PUT /config
│   │   │   ├── deploy.py               # POST /deploy (self-deploy trigger)
│   │   │   ├── websocket.py            # WS /stream handler
│   │   │   └── schemas.py              # Pydantic request/response models
│   │   │
│   │   ├── orchestrator/               # Core orchestration engine
│   │   │   ├── __init__.py
│   │   │   ├── engine.py               # Main orchestrator loop
│   │   │   ├── intent_classifier.py    # Rule-based intent classification
│   │   │   ├── model_router.py         # Claude vs Gemini vs Local routing
│   │   │   ├── context_builder.py      # Per-agent context scoping
│   │   │   ├── approval_manager.py     # Approval queue CRUD + enforcement
│   │   │   └── response_formatter.py   # Format agent output for clients
│   │   │
│   │   ├── agents/                     # Agent implementations
│   │   │   ├── __init__.py
│   │   │   ├── base.py                 # BaseAgent abstract class
│   │   │   ├── briefing.py             # Morning Briefing Agent
│   │   │   ├── email_classifier.py     # Email Classification Agent
│   │   │   ├── job_search.py           # Job Search Agent (3-tier pipeline)
│   │   │   ├── fashion.py              # Fashion & Outfit Agent
│   │   │   ├── daily_life.py           # Daily Life Manager Agent
│   │   │   ├── health_monitor.py       # System Health Monitor Agent
│   │   │   ├── communication.py        # Communication Drafter Agent
│   │   │   ├── product_research.py     # Product Research Agent
│   │   │   ├── coding.py              # Coding/DevOps Agent (dispatches to Node 2)
│   │   │   ├── research.py            # Research Agent
│   │   │   ├── eod_summary.py         # End-of-Day Summary Agent
│   │   │   ├── finance.py             # Finance Tracking Agent
│   │   │   └── health_fitness.py      # Health & Fitness Agent
│   │   │
│   │   ├── models/                    # AI model clients
│   │   │   ├── __init__.py
│   │   │   ├── claude_spawner.py      # Claude Code headless subprocess
│   │   │   ├── gemini_client.py       # Gemini Flash/Pro/Vision REST client
│   │   │   └── usage_tracker.py       # Token/cost tracking per model
│   │   │
│   │   ├── integrations/             # External service adapters
│   │   │   ├── __init__.py
│   │   │   ├── caldav_client.py      # iCloud CalDAV
│   │   │   ├── gmail_client.py       # Gmail API (2 accounts)
│   │   │   ├── github_client.py      # GitHub REST/GraphQL
│   │   │   ├── notion_client.py      # Notion API
│   │   │   ├── weather_client.py     # OpenWeatherMap
│   │   │   ├── plaid_client.py       # Plaid transactions
│   │   │   ├── grafana_client.py     # Grafana/Loki queries
│   │   │   ├── telegram_bot.py       # Telegram bot handler
│   │   │   ├── apns_client.py        # Apple Push Notifications (rich interactive)
│   │   │   ├── notification_service.py # Unified alert fan-out via Apprise
│   │   │   ├── job_boards/           # Job board adapters (adapter pattern)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py           # JobBoardAdapter abstract
│   │   │   │   ├── linkedin.py
│   │   │   │   ├── indeed.py
│   │   │   │   ├── yc_waaas.py
│   │   │   │   ├── handshake.py
│   │   │   │   └── custom_careers.py
│   │   │   └── airplay_client.py     # AirPlay audio to HomePod
│   │   │
│   │   ├── wake_word/                # Wake word subsystem
│   │   │   ├── __init__.py
│   │   │   ├── listener.py           # Porcupine daemon + audio capture
│   │   │   ├── stt_processor.py      # Whisper STT pipeline
│   │   │   └── tts_output.py         # TTS + AirPlay to HomePod
│   │   │
│   │   ├── db/                       # Database layer
│   │   │   ├── __init__.py
│   │   │   ├── session.py            # async session factory
│   │   │   ├── models.py             # SQLAlchemy ORM models
│   │   │   └── repositories/         # Data access layer
│   │   │       ├── __init__.py
│   │   │       ├── conversations.py
│   │   │       ├── approvals.py
│   │   │       ├── agent_tasks.py
│   │   │       ├── briefings.py
│   │   │       ├── email_classifications.py
│   │   │       ├── job_listings.py
│   │   │       ├── wardrobe.py
│   │   │       ├── config.py
│   │   │       ├── contacts.py
│   │   │       ├── transactions.py
│   │   │       ├── health_data.py
│   │   │       └── model_usage.py
│   │   │
│   │   ├── scheduler/               # Scheduled task definitions
│   │   │   ├── __init__.py
│   │   │   └── jobs.py              # All cron job definitions
│   │   │
│   │   └── utils/                   # Shared utilities
│   │       ├── __init__.py
│   │       ├── logger.py            # Structured logging config
│   │       ├── crypto.py            # Encryption helpers
│   │       └── constants.py         # Shared constants, enums
│   │
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_intent_classifier.py
│   │   ├── test_model_router.py
│   │   ├── test_approval_manager.py
│   │   ├── test_agents/
│   │   │   ├── test_briefing.py
│   │   │   ├── test_email_classifier.py
│   │   │   └── ...
│   │   └── test_integrations/
│   │       ├── test_gmail.py
│   │       ├── test_caldav.py
│   │       └── ...
│   │
│   └── Dockerfile                   # Node 1 backend container
│
├── worker/                          # Node 2 job worker
│   ├── pyproject.toml
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                  # Worker entrypoint
│   │   ├── job_processor.py         # Bull-compatible job consumer
│   │   ├── executors/
│   │   │   ├── __init__.py
│   │   │   ├── code_executor.py     # Clone repo, run Claude Code
│   │   │   ├── research_executor.py
│   │   │   ├── diagnostic_executor.py
│   │   │   ├── job_scraper_executor.py
│   │   │   └── image_processor.py   # Wardrobe image storage/processing
│   │   └── docker_manager.py        # Spawn/manage sandbox containers
│   ├── tests/
│   └── Dockerfile                   # Node 2 worker container
│
├── ios/                             # iOS app (Xcode project)
│   └── TARS/
│       ├── TARS.xcodeproj/
│       ├── TARS/
│       │   ├── TARSApp.swift                # App entry point
│       │   ├── Info.plist
│       │   ├── Assets.xcassets/
│       │   │
│       │   ├── Models/                      # Data models
│       │   │   ├── Message.swift
│       │   │   ├── Briefing.swift
│       │   │   ├── Approval.swift
│       │   │   ├── JobListing.swift
│       │   │   ├── OutfitSuggestion.swift
│       │   │   ├── HealthSummary.swift
│       │   │   └── FinanceSummary.swift
│       │   │
│       │   ├── ViewModels/                  # MVVM view models
│       │   │   ├── ChatViewModel.swift
│       │   │   ├── BriefingViewModel.swift
│       │   │   ├── ApprovalViewModel.swift
│       │   │   ├── ScheduleViewModel.swift
│       │   │   ├── JobsViewModel.swift
│       │   │   ├── WardrobeViewModel.swift
│       │   │   └── SettingsViewModel.swift
│       │   │
│       │   ├── Views/                       # SwiftUI views
│       │   │   ├── MainTabView.swift
│       │   │   ├── Chat/
│       │   │   │   ├── ChatView.swift
│       │   │   │   ├── MessageBubble.swift
│       │   │   │   ├── ApprovalCard.swift
│       │   │   │   ├── JobCard.swift
│       │   │   │   └── OutfitCard.swift
│       │   │   ├── Briefing/
│       │   │   │   ├── BriefingView.swift
│       │   │   │   └── BriefingSectionView.swift
│       │   │   ├── Schedule/
│       │   │   │   └── ScheduleView.swift
│       │   │   ├── Jobs/
│       │   │   │   ├── JobsListView.swift
│       │   │   │   └── JobDetailView.swift
│       │   │   ├── Wardrobe/
│       │   │   │   ├── WardrobeView.swift
│       │   │   │   └── CameraCapture.swift
│       │   │   ├── Alarm/
│       │   │   │   └── AlarmView.swift
│       │   │   └── Settings/
│       │   │       └── SettingsView.swift
│       │   │
│       │   ├── Services/                    # Service layer
│       │   │   ├── APIClient.swift          # REST + WebSocket client
│       │   │   ├── WebSocketManager.swift
│       │   │   ├── PushNotificationManager.swift
│       │   │   ├── EventKitService.swift    # Calendar access
│       │   │   ├── HealthKitService.swift   # Health data access
│       │   │   ├── ContactsService.swift    # Contacts sync
│       │   │   ├── SpeechService.swift      # STT + TTS
│       │   │   ├── AlarmService.swift       # Smart alarm management
│       │   │   └── KeychainService.swift    # Secure storage
│       │   │
│       │   ├── SiriIntents/                 # Siri Shortcuts
│       │   │   ├── TARSIntents.swift
│       │   │   └── IntentHandler.swift
│       │   │
│       │   └── Widgets/                     # Home screen widgets
│       │       ├── ScheduleWidget.swift
│       │       ├── ApprovalsWidget.swift
│       │       ├── HealthWidget.swift
│       │       └── OutfitWidget.swift
│       │
│       ├── TARSWatch/                       # Apple Watch app
│       │   ├── TARSWatchApp.swift
│       │   ├── Views/
│       │   │   ├── WatchMainView.swift
│       │   │   ├── WatchApprovalView.swift
│       │   │   └── WatchGlanceView.swift
│       │   ├── Complications/
│       │   │   └── TARSComplication.swift
│       │   └── WatchConnectivityManager.swift
│       │
│       └── TARSWidgetExtension/             # Widget extension target
│           └── TARSWidgets.swift
│
├── deploy/                               # Deployment configs
│   ├── node1/
│   │   ├── docker-compose.yml            # Node 1 services
│   │   └── .env.production               # (gitignored, template in .env.example)
│   ├── node2/
│   │   ├── docker-compose.yml            # Node 2 services
│   │   └── .env.production
│   ├── scripts/
│   │   ├── deploy.sh                     # Pull + restart script
│   │   ├── backup.sh                     # PostgreSQL + volume backup
│   │   ├── restore.sh                    # Restore from backup
│   │   └── setup-server.sh              # Initial server setup automation
│   └── nginx/                            # (optional) reverse proxy config
│       └── tars.conf
│
├── shared/                              # Shared types and constants
│   ├── constants.py                     # Intent types, risk tiers, model names
│   └── schemas.py                       # Shared Pydantic models (API contracts)
│
└── docs/                                # Documentation
    ├── DESIGN_DOCUMENT.md               # This document
    ├── REQUIREMENTS_v2_1.md             # Locked requirements
    ├── API_REFERENCE.md                 # Auto-generated API docs
    ├── RUNBOOK.md                       # Ops runbook (troubleshooting, backups)
    └── ARCHITECTURE.md                  # Architecture decision records
```

---

## 4. Database Schema

### 4.1 PostgreSQL Database: `tars`

All tables use `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` and `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` / `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` unless otherwise noted.

### 4.2 Complete Schema Definition

```sql
-- ============================================================
-- T.A.R.S. Database Schema
-- PostgreSQL 16
-- ============================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";         -- trigram indexes for text search

-- ============================================================
-- CONVERSATIONS
-- Chat history between user and T.A.R.S.
-- ============================================================
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(20) NOT NULL CHECK (source IN ('ios', 'telegram', 'watch', 'siri', 'wake_word', 'system')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB DEFAULT '{}'::jsonb     -- device info, session context
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content_type    VARCHAR(20) NOT NULL DEFAULT 'text' CHECK (content_type IN ('text', 'card', 'image', 'action', 'approval', 'briefing')),
    content         TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}'::jsonb,     -- rich content payload (cards, buttons, image refs)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at DESC);
CREATE INDEX idx_messages_created ON messages(created_at DESC);

-- ============================================================
-- AGENT TASKS
-- Task queue: pending, running, completed, failed
-- ============================================================
CREATE TYPE task_status AS ENUM ('pending', 'running', 'completed', 'failed', 'cancelled');
CREATE TYPE task_priority AS ENUM ('critical', 'high', 'normal', 'low');

CREATE TABLE agent_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_type      VARCHAR(50) NOT NULL,          -- 'briefing', 'email_classifier', 'job_search', etc.
    model_used      VARCHAR(30),                   -- 'claude', 'gemini_flash', 'gemini_pro', 'gemini_vision', 'local'
    status          task_status NOT NULL DEFAULT 'pending',
    priority        task_priority NOT NULL DEFAULT 'normal',
    input_payload   JSONB NOT NULL,                -- agent input context
    output_payload  JSONB,                         -- agent result
    error_message   TEXT,
    node            VARCHAR(10) DEFAULT 'node1' CHECK (node IN ('node1', 'node2')),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    duration_ms     INTEGER,
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_tasks_status ON agent_tasks(status, priority DESC);
CREATE INDEX idx_agent_tasks_agent_type ON agent_tasks(agent_type, created_at DESC);
CREATE INDEX idx_agent_tasks_created ON agent_tasks(created_at DESC);

-- ============================================================
-- APPROVALS
-- Pending + completed approval requests with decisions
-- ============================================================
CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'edited', 'expired', 'executed');
CREATE TYPE risk_tier AS ENUM ('tier1_autonomous', 'tier2_approval', 'tier3_escalation');

CREATE TABLE approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID REFERENCES agent_tasks(id),
    action_type     VARCHAR(50) NOT NULL,          -- 'send_email', 'create_event', 'create_pr', etc.
    risk_tier       risk_tier NOT NULL,
    status          approval_status NOT NULL DEFAULT 'pending',
    title           VARCHAR(255) NOT NULL,         -- human-readable summary
    preview_payload JSONB NOT NULL,                -- full preview (email draft, event details, etc.)
    edited_payload  JSONB,                         -- user's edited version (if edited)
    decision_source VARCHAR(20),                   -- 'ios', 'telegram', 'watch'
    decided_at      TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL,          -- default: created_at + 1 hour
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_approvals_status ON approvals(status) WHERE status = 'pending';
CREATE INDEX idx_approvals_expires ON approvals(expires_at) WHERE status = 'pending';

-- ============================================================
-- EMAIL CLASSIFICATIONS
-- Email classification history + user feedback corrections
-- ============================================================
CREATE TYPE email_tier AS ENUM ('urgent', 'actionable', 'informational', 'noise');

CREATE TABLE email_classifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gmail_account   VARCHAR(10) NOT NULL CHECK (gmail_account IN ('personal', 'professional')),
    gmail_message_id VARCHAR(255) NOT NULL UNIQUE,
    from_address    VARCHAR(255) NOT NULL,
    from_name       VARCHAR(255),
    subject         VARCHAR(500),
    snippet         TEXT,                          -- first ~200 chars
    classified_tier email_tier NOT NULL,
    confidence      REAL CHECK (confidence >= 0 AND confidence <= 1),
    model_used      VARCHAR(30) NOT NULL,          -- 'gemini_flash', 'claude'
    user_correction email_tier,                    -- null if no correction
    correction_at   TIMESTAMPTZ,
    contact_id      UUID REFERENCES contacts(id),  -- linked Apple Contact (if matched)
    received_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_email_class_account ON email_classifications(gmail_account, received_at DESC);
CREATE INDEX idx_email_class_from ON email_classifications(from_address);
CREATE INDEX idx_email_class_corrections ON email_classifications(user_correction) WHERE user_correction IS NOT NULL;

-- ============================================================
-- BRIEFINGS
-- Generated briefings archive
-- ============================================================
CREATE TABLE briefings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    briefing_type   VARCHAR(20) NOT NULL CHECK (briefing_type IN ('morning', 'end_of_day', 'on_demand')),
    briefing_date   DATE NOT NULL,
    payload         JSONB NOT NULL,                -- full briefing JSON (see requirements doc structure)
    narrative       TEXT NOT NULL,                  -- TTS-ready text narrative
    delivered       BOOLEAN NOT NULL DEFAULT false,
    delivered_at    TIMESTAMPTZ,
    delivered_via   VARCHAR(20),                   -- 'ios_tts', 'telegram', 'homepod', 'watch'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_briefings_date_type ON briefings(briefing_date, briefing_type);

-- ============================================================
-- CONFIG
-- User preferences, briefing config, notification settings
-- ============================================================
CREATE TABLE config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace       VARCHAR(50) NOT NULL,          -- 'morning_briefing', 'email_contacts', 'job_search', 'notifications', 'general'
    key             VARCHAR(100) NOT NULL,
    value           JSONB NOT NULL,
    updated_by      VARCHAR(50) DEFAULT 'system',  -- 'user', 'system', 'agent'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(namespace, key)
);

CREATE INDEX idx_config_namespace ON config(namespace);

-- ============================================================
-- CONTACTS
-- Known contacts with priority hints (synced from Apple Contacts + manual)
-- ============================================================
CREATE TABLE contacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    apple_contact_id VARCHAR(255) UNIQUE,           -- Apple CNContact.identifier
    full_name       VARCHAR(255) NOT NULL,
    email_addresses JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["email1@x.com", "email2@y.com"]
    phone_numbers   JSONB DEFAULT '[]'::jsonb,
    organization    VARCHAR(255),
    job_title       VARCHAR(255),
    relationship    VARCHAR(50),                    -- 'professor', 'advisor', 'recruiter', 'colleague', 'friend', 'family'
    priority_hint   VARCHAR(20) DEFAULT 'normal' CHECK (priority_hint IN ('always_urgent', 'always_actionable', 'normal', 'always_noise')),
    notes           TEXT,
    source          VARCHAR(20) NOT NULL DEFAULT 'apple_contacts' CHECK (source IN ('apple_contacts', 'manual', 'learned')),
    synced_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_emails ON contacts USING GIN (email_addresses jsonb_path_ops);
CREATE INDEX idx_contacts_name ON contacts USING GIN (full_name gin_trgm_ops);

-- ============================================================
-- AGENT OUTPUTS
-- Stored outputs from completed agent tasks
-- ============================================================
CREATE TABLE agent_outputs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    output_type     VARCHAR(50) NOT NULL,          -- 'email_draft', 'job_digest', 'outfit', 'research_brief', etc.
    content         JSONB NOT NULL,
    file_paths      JSONB DEFAULT '[]'::jsonb,     -- references to files on Node 2
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_outputs_task ON agent_outputs(task_id);
CREATE INDEX idx_agent_outputs_type ON agent_outputs(output_type, created_at DESC);

-- ============================================================
-- SYSTEM HEALTH LOG
-- Historical health snapshots
-- ============================================================
CREATE TYPE health_status AS ENUM ('green', 'yellow', 'red', 'unknown');

CREATE TABLE system_health_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target          VARCHAR(50) NOT NULL,          -- 'atlasdesk_api', 'atlasdesk_db', 'tars_node1', 'tars_node2'
    status          health_status NOT NULL,
    response_time_ms INTEGER,
    details         JSONB,                         -- diagnostic payload
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_health_log_target ON system_health_log(target, checked_at DESC);

-- ============================================================
-- FEEDBACK LOG
-- User corrections and feedback for learning
-- ============================================================
CREATE TABLE feedback_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_type   VARCHAR(50) NOT NULL,          -- 'email_correction', 'job_relevance', 'outfit_feedback', 'general'
    entity_type     VARCHAR(50),                   -- 'email_classification', 'job_listing', 'outfit_suggestion'
    entity_id       UUID,                          -- reference to the entity being corrected
    original_value  JSONB,
    corrected_value JSONB,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_feedback_type ON feedback_log(feedback_type, created_at DESC);

-- ============================================================
-- JOB LISTINGS
-- Scanned jobs with scores, status tracking
-- ============================================================
CREATE TYPE job_status AS ENUM ('new', 'saved', 'applying', 'applied', 'interview', 'offer', 'rejected', 'skipped', 'expired');

CREATE TABLE job_listings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(50) NOT NULL,          -- 'linkedin', 'indeed', 'yc_waaas', 'glassdoor', 'handshake', 'custom'
    external_id     VARCHAR(255),                  -- source-specific ID
    title           VARCHAR(500) NOT NULL,
    company         VARCHAR(255) NOT NULL,
    location        VARCHAR(255),
    salary_range    VARCHAR(100),
    description     TEXT,
    url             TEXT NOT NULL,
    status          job_status NOT NULL DEFAULT 'new',
    match_score     INTEGER CHECK (match_score >= 0 AND match_score <= 100),
    match_reasons   JSONB DEFAULT '[]'::jsonb,
    concerns        JSONB DEFAULT '[]'::jsonb,
    flash_screen    BOOLEAN DEFAULT false,         -- passed Gemini Flash initial screen
    pro_evaluated   BOOLEAN DEFAULT false,         -- evaluated by Gemini Pro
    claude_reviewed BOOLEAN DEFAULT false,         -- deep reviewed by Claude
    notion_page_id  VARCHAR(255),                  -- linked Notion tracker page
    applied_at      TIMESTAMPTZ,
    follow_up_at    TIMESTAMPTZ,                   -- reminder date
    scraped_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source, external_id)
);

CREATE INDEX idx_jobs_status ON job_listings(status, match_score DESC);
CREATE INDEX idx_jobs_scraped ON job_listings(scraped_at DESC);
CREATE INDEX idx_jobs_company ON job_listings(company);

-- ============================================================
-- JOB APPLICATIONS
-- Application materials per job
-- ============================================================
CREATE TABLE job_applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_listing_id  UUID NOT NULL REFERENCES job_listings(id) ON DELETE CASCADE,
    cover_letter    TEXT,
    resume_version  TEXT,                          -- description of tailored resume changes
    notes           TEXT,
    status          VARCHAR(30) DEFAULT 'draft' CHECK (status IN ('draft', 'pending_approval', 'approved', 'submitted')),
    approval_id     UUID REFERENCES approvals(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_job_apps_listing ON job_applications(job_listing_id);

-- ============================================================
-- WARDROBE ITEMS
-- Clothing catalog with metadata
-- ============================================================
CREATE TABLE wardrobe_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_type       VARCHAR(30) NOT NULL,          -- 'shirt', 'pants', 'jacket', 'shoes', etc.
    sub_type        VARCHAR(50),                   -- 'v-neck', 'chinos', 'sneakers', etc.
    color           VARCHAR(50) NOT NULL,
    pattern         VARCHAR(30) DEFAULT 'solid',   -- 'solid', 'striped', 'plaid', 'checked'
    seasons         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["spring", "summer", "fall", "winter"]
    formality       VARCHAR(20) NOT NULL CHECK (formality IN ('casual', 'smart-casual', 'business-casual', 'formal')),
    brand           VARCHAR(100),
    image_path      VARCHAR(500) NOT NULL,         -- path on Node 2: /data/wardrobe/xxx.jpg
    image_hash      VARCHAR(64),                   -- SHA-256 of image for dedup
    last_worn       DATE,
    wear_count      INTEGER NOT NULL DEFAULT 0,
    active          BOOLEAN NOT NULL DEFAULT true,  -- false = donated/discarded
    gemini_raw      JSONB,                         -- raw Gemini Vision analysis output
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_wardrobe_type ON wardrobe_items(item_type, active) WHERE active = true;
CREATE INDEX idx_wardrobe_formality ON wardrobe_items(formality) WHERE active = true;

-- ============================================================
-- WARDROBE OUTFITS
-- Suggested and worn outfit history
-- ============================================================
CREATE TABLE wardrobe_outfits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    outfit_date     DATE NOT NULL,
    items           JSONB NOT NULL,                -- array of wardrobe_item IDs
    weather_context JSONB,                         -- temp, conditions at time of suggestion
    calendar_context JSONB,                        -- events that influenced formality
    reasoning       TEXT,                          -- AI reasoning for suggestion
    was_worn        BOOLEAN,                       -- user feedback: did they wear it?
    user_feedback   TEXT,                          -- optional comment
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_outfits_date ON wardrobe_outfits(outfit_date DESC);

-- ============================================================
-- MODEL USAGE
-- AI model call logs for budget tracking
-- ============================================================
CREATE TABLE model_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model           VARCHAR(30) NOT NULL,          -- 'claude_code', 'gemini_flash', 'gemini_pro', 'gemini_vision'
    agent_type      VARCHAR(50) NOT NULL,
    task_id         UUID REFERENCES agent_tasks(id),
    tokens_input    INTEGER NOT NULL DEFAULT 0,
    tokens_output   INTEGER NOT NULL DEFAULT 0,
    estimated_cost  NUMERIC(10, 6) NOT NULL DEFAULT 0,  -- USD
    duration_ms     INTEGER,
    success         BOOLEAN NOT NULL DEFAULT true,
    error_type      VARCHAR(50),                   -- 'rate_limit', 'timeout', 'auth', 'other'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_usage_model ON model_usage(model, created_at DESC);
CREATE INDEX idx_usage_daily ON model_usage(created_at::date, model);

-- ============================================================
-- TRANSACTIONS (Financial)
-- Plaid transaction data
-- ============================================================
CREATE TABLE transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plaid_transaction_id VARCHAR(255) UNIQUE NOT NULL,
    plaid_account_id VARCHAR(255) NOT NULL,
    account_name    VARCHAR(100),                  -- e.g., "Chase Checking", "Amex Card"
    merchant_name   VARCHAR(255),
    amount          NUMERIC(12, 2) NOT NULL,       -- positive = debit/spend, negative = credit/income
    currency        VARCHAR(3) NOT NULL DEFAULT 'USD',
    category        VARCHAR(100),                  -- Plaid primary category
    subcategory     VARCHAR(100),                  -- Plaid detailed category
    custom_category VARCHAR(100),                  -- T.A.R.S. AI-assigned category override
    is_recurring    BOOLEAN DEFAULT false,
    transaction_date DATE NOT NULL,
    authorized_date DATE,
    pending         BOOLEAN NOT NULL DEFAULT false,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_txns_date ON transactions(transaction_date DESC);
CREATE INDEX idx_txns_merchant ON transactions(merchant_name);
CREATE INDEX idx_txns_category ON transactions(category, transaction_date DESC);
CREATE INDEX idx_txns_recurring ON transactions(is_recurring) WHERE is_recurring = true;

-- ============================================================
-- FINANCE SUMMARIES
-- Daily/weekly/monthly spending summaries
-- ============================================================
CREATE TABLE finance_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_type     VARCHAR(10) NOT NULL CHECK (period_type IN ('daily', 'weekly', 'monthly')),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    total_spent     NUMERIC(12, 2) NOT NULL,
    total_income    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    by_category     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- { "Groceries": 312.00, "Dining": 189.00, ... }
    alerts          JSONB DEFAULT '[]'::jsonb,     -- trend alerts generated
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(period_type, period_start)
);

CREATE INDEX idx_finance_period ON finance_summaries(period_type, period_start DESC);

-- ============================================================
-- HEALTH DATA
-- Synced HealthKit data
-- ============================================================
CREATE TABLE health_data (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_type       VARCHAR(30) NOT NULL,          -- 'sleep', 'steps', 'workout', 'heart_rate', 'exercise_minutes'
    value           NUMERIC(12, 2) NOT NULL,       -- duration (hours), count, bpm, minutes
    unit            VARCHAR(20) NOT NULL,           -- 'hours', 'count', 'bpm', 'minutes', 'calories'
    metadata        JSONB DEFAULT '{}'::jsonb,     -- workout type, sleep quality, etc.
    recorded_date   DATE NOT NULL,
    start_time      TIMESTAMPTZ,
    end_time        TIMESTAMPTZ,
    source          VARCHAR(30) DEFAULT 'healthkit',
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_health_type_date ON health_data(data_type, recorded_date DESC);
CREATE INDEX idx_health_date ON health_data(recorded_date DESC);

-- ============================================================
-- APPLE CONTACTS (synced from iOS)
-- (This is the `contacts` table defined above — dual purpose)
-- Apple Contacts sync populates the contacts table.
-- ============================================================

-- ============================================================
-- AUDIT LOG
-- All actions logged for compliance with HC-08
-- ============================================================
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_type     VARCHAR(50) NOT NULL,          -- 'api_call', 'agent_spawn', 'approval_decision', 'config_change', 'deploy', etc.
    actor           VARCHAR(30) NOT NULL,          -- 'user', 'system', 'agent:<name>', 'scheduler'
    target          VARCHAR(100),                  -- what was acted upon
    details         JSONB DEFAULT '{}'::jsonb,
    ip_address      VARCHAR(45),
    source          VARCHAR(20),                   -- 'ios', 'telegram', 'watch', 'system'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_action ON audit_log(action_type, created_at DESC);
CREATE INDEX idx_audit_created ON audit_log(created_at DESC);

-- ============================================================
-- TRIGGERS: auto-update updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to all tables with updated_at
DO $$ 
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY[
        'conversations', 'agent_tasks', 'approvals', 'config',
        'contacts', 'job_listings', 'job_applications',
        'wardrobe_items'
    ]) LOOP
        EXECUTE format('
            CREATE TRIGGER set_updated_at BEFORE UPDATE ON %I
            FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
        ', t);
    END LOOP;
END $$;
```

### 4.3 Entity Relationship Summary

```
conversations 1──∞ messages
agent_tasks 1──1 approvals
agent_tasks 1──∞ agent_outputs
agent_tasks 1──∞ model_usage
job_listings 1──∞ job_applications
job_applications ∞──1 approvals
email_classifications ∞──1 contacts
wardrobe_items ∞──∞ wardrobe_outfits (via JSONB items array)
```

---

## 5. API Contract

### 5.1 Base Configuration

- **Base URL**: `https://tars.{domain}.com/api/v1` (via Cloudflare Tunnel) or `http://10.0.1.1:8000/api/v1` (via Tailscale)
- **Authentication**: `Authorization: Bearer {api_key}` header + `X-Device-Token: {device_token}` header
- **Content-Type**: `application/json`
- **Error Format**: `{ "error": { "code": "ERROR_CODE", "message": "Human-readable message", "details": {} } }`

### 5.2 REST Endpoints

#### 5.2.1 Messages

**POST /api/v1/message**  
Send a message or command to T.A.R.S.

```
Request:
{
    "text": "Schedule gym Thursday at 6pm",
    "source": "ios",                          // "ios" | "telegram" | "watch" | "siri" | "wake_word"
    "conversation_id": "uuid-or-null",        // null creates new conversation
    "attachments": [                          // optional
        {
            "type": "image",                  // "image" | "audio"
            "data": "base64...",
            "mime_type": "image/jpeg"
        }
    ]
}

Response 200:
{
    "conversation_id": "uuid",
    "message_id": "uuid",
    "response": {
        "text": "I'll create a gym event for Thursday at 6 PM. Here's what it looks like:",
        "content_type": "approval",           // "text" | "card" | "approval" | "image" | "briefing"
        "cards": [],                          // rich content cards
        "approval": {                         // present if action needs approval
            "approval_id": "uuid",
            "action_type": "create_event",
            "title": "Create calendar event: Gym",
            "preview": {
                "event_title": "Gym",
                "date": "2026-03-12",
                "time": "18:00",
                "duration": "1h",
                "calendar": "Personal"
            }
        }
    },
    "agent_used": "daily_life",
    "model_used": "gemini_flash"
}

Error 401: { "error": { "code": "UNAUTHORIZED", "message": "Invalid API key" } }
Error 429: { "error": { "code": "RATE_LIMITED", "message": "Too many requests" } }
Error 500: { "error": { "code": "INTERNAL_ERROR", "message": "Agent execution failed" } }
```

#### 5.2.2 Briefings

**GET /api/v1/briefing**  
Fetch current day's briefing.

```
Query params:
  ?type=morning|end_of_day      (default: morning)
  ?date=2026-03-09              (default: today)

Response 200:
{
    "briefing_id": "uuid",
    "type": "morning",
    "date": "2026-03-09",
    "payload": { ... },           // full briefing JSON per requirements spec
    "narrative": "Good morning, Tasin. It's 6 AM, 18°C outside...",
    "created_at": "2026-03-09T05:50:00Z",
    "delivered": true
}

Error 404: { "error": { "code": "NOT_FOUND", "message": "No briefing for this date" } }
```

#### 5.2.3 Schedule

**GET /api/v1/schedule**  
Fetch today's (or specified date's) schedule.

```
Query params:
  ?date=2026-03-09              (default: today)
  ?days=3                       (default: 1, max 7)

Response 200:
{
    "date": "2026-03-09",
    "events": [
        {
            "id": "caldav-event-id",
            "title": "Stand-up with IT team",
            "start": "2026-03-09T08:00:00-05:00",
            "end": "2026-03-09T08:30:00-05:00",
            "location": "Office",
            "calendar": "Work",
            "all_day": false
        }
    ],
    "leave_home_by": "07:35",
    "commute_note": "15-minute drive to office"
}
```

#### 5.2.4 Approvals

**GET /api/v1/approvals**  
List pending approval items.

```
Query params:
  ?status=pending               (default: pending; options: pending, approved, rejected, all)
  ?limit=20                     (default: 20, max 100)
  ?offset=0

Response 200:
{
    "approvals": [
        {
            "id": "uuid",
            "action_type": "send_email",
            "risk_tier": "tier2_approval",
            "status": "pending",
            "title": "Send email to Prof. Sadigh",
            "preview": {
                "to": "sadigh@cs.stanford.edu",
                "subject": "Following up on our conversation",
                "body": "Dear Professor Sadigh, ..."
            },
            "expires_at": "2026-03-09T15:00:00Z",
            "created_at": "2026-03-09T14:00:00Z"
        }
    ],
    "total": 3,
    "pending_count": 2
}
```

**POST /api/v1/approvals/{id}/approve**  
Approve a pending action.

```
Request:
{
    "source": "ios"               // "ios" | "telegram" | "watch"
}

Response 200:
{
    "approval_id": "uuid",
    "status": "approved",
    "executed": true,
    "execution_result": "Email sent successfully to sadigh@cs.stanford.edu"
}

Error 404: Approval not found
Error 409: { "error": { "code": "ALREADY_DECIDED", "message": "Approval already processed" } }
Error 410: { "error": { "code": "EXPIRED", "message": "Approval has expired" } }
```

**POST /api/v1/approvals/{id}/reject**

```
Request:
{
    "source": "ios",
    "reason": "optional rejection reason"
}

Response 200:
{
    "approval_id": "uuid",
    "status": "rejected"
}
```

**POST /api/v1/approvals/{id}/edit**

```
Request:
{
    "source": "ios",
    "edited_payload": {
        "to": "sadigh@cs.stanford.edu",
        "subject": "Updated subject",
        "body": "Edited email body..."
    }
}

Response 200:
{
    "approval_id": "uuid",
    "status": "edited",
    "executed": true,
    "execution_result": "Email sent with edits"
}
```

#### 5.2.5 Health

**GET /api/v1/health**  
System health for T.A.R.S. + AtlasDesk.

```
Response 200:
{
    "tars": {
        "status": "green",
        "node1": { "status": "green", "uptime_hours": 127.4, "cpu_pct": 23, "memory_pct": 61 },
        "node2": { "status": "green", "uptime_hours": 127.4, "cpu_pct": 12, "memory_pct": 45 },
        "postgres": "connected",
        "redis": "connected",
        "chromadb": "connected"
    },
    "atlasdesk": {
        "status": "green",
        "services": {
            "api": { "status": "green", "response_time_ms": 142 },
            "frontend": { "status": "green", "response_time_ms": 89 },
            "database": { "status": "green" }
        },
        "last_checked": "2026-03-09T14:30:00Z"
    },
    "ai_budget": {
        "claude_calls_today": 8,
        "claude_calls_this_week": 34,
        "gemini_calls_today": 47,
        "gemini_estimated_cost_today": 0.02,
        "gemini_estimated_cost_mtd": 0.41
    }
}
```

#### 5.2.6 Jobs

**GET /api/v1/jobs**  
Fetch latest job matches.

```
Query params:
  ?status=new                   (default: new; options: new, saved, applied, interview, all)
  ?limit=20
  ?min_score=70

Response 200:
{
    "jobs": [
        {
            "id": "uuid",
            "title": "ML Research Engineer",
            "company": "Scale AI",
            "location": "San Francisco, CA (Hybrid)",
            "salary_range": "$150K–$200K",
            "match_score": 92,
            "match_reasons": ["Multi-agent systems experience", "Python + ML stack"],
            "concerns": ["Requires 3+ years industry experience"],
            "url": "https://...",
            "status": "new",
            "source": "linkedin",
            "scraped_at": "2026-03-09T02:15:00Z"
        }
    ],
    "total": 12,
    "new_today": 3
}
```

**POST /api/v1/jobs/{id}/action**

```
Request:
{
    "action": "save"              // "save" | "skip" | "apply" | "archive"
}

Response 200:
{
    "job_id": "uuid",
    "new_status": "saved",
    "message": "Job saved. Ready to prepare application materials when you are."
}
```

#### 5.2.7 Wardrobe

**POST /api/v1/wardrobe/upload**  
Upload wardrobe photo for cataloging.

```
Request (multipart/form-data):
  image: <JPEG/PNG file>

Response 200:
{
    "wardrobe_item_id": "uuid",
    "analysis": {
        "item_type": "shirt",
        "sub_type": "v-neck",
        "color": "white",
        "pattern": "solid",
        "seasons": ["spring", "summer", "fall"],
        "formality": "smart-casual",
        "brand": "H&M"
    },
    "message": "White v-neck shirt added to your wardrobe."
}
```

**GET /api/v1/outfit**  
Get today's outfit suggestion.

```
Query params:
  ?date=2026-03-09

Response 200:
{
    "outfit_id": "uuid",
    "date": "2026-03-09",
    "suggestion": "Light pants with your white v-neck and pink shirt",
    "reasoning": "18°C, semi-formal meeting in Cleveland, light colors for warm weather",
    "items": [
        { "id": "uuid", "type": "pants", "color": "light khaki", "image_path": "/wardrobe/023.jpg" },
        { "id": "uuid", "type": "shirt", "color": "white", "image_path": "/wardrobe/011.jpg" },
        { "id": "uuid", "type": "shirt", "color": "pink", "image_path": "/wardrobe/045.jpg" }
    ],
    "weather": { "temp": 18, "conditions": "partly cloudy" }
}
```

#### 5.2.8 Finance

**GET /api/v1/finance/summary**

```
Query params:
  ?period=daily|weekly|monthly   (default: daily)
  ?date=2026-03-09               (default: today)

Response 200:
{
    "period": "daily",
    "date": "2026-03-08",
    "total_spent": 47.23,
    "transactions": [
        { "merchant": "Walmart", "amount": 32.00, "category": "Groceries" },
        { "merchant": "Starbucks", "amount": 15.23, "category": "Dining" }
    ],
    "month_to_date": {
        "total": 1247.00,
        "by_category": { "Groceries": 312.00, "Dining": 189.00 }
    },
    "alerts": ["Dining out up 30% vs February"]
}
```

#### 5.2.9 Config

**GET /api/v1/config**

```
Query params:
  ?namespace=morning_briefing     (optional filter)

Response 200:
{
    "config": {
        "morning_briefing": {
            "time": "05:50",
            "alarm_offset_minutes": 10,
            "voice_enabled": true,
            "sections": [ ... ]
        },
        "notifications": { ... },
        "job_search": { ... }
    }
}
```

**PUT /api/v1/config**

```
Request:
{
    "namespace": "morning_briefing",
    "key": "time",
    "value": "06:00"
}

Response 200:
{
    "updated": true,
    "namespace": "morning_briefing",
    "key": "time",
    "value": "06:00"
}
```

#### 5.2.10 Deploy

**POST /api/v1/deploy**  
Trigger self-deploy (pull latest images, restart).

```
Request:
{
    "confirm": true
}

Response 200:
{
    "status": "deploying",
    "message": "Pulling latest images and restarting. You'll be notified when complete."
}
```

#### 5.2.11 Health Data Sync (iOS → Backend)

**POST /api/v1/health-data/sync**

```
Request:
{
    "data": [
        { "type": "sleep", "value": 7.2, "unit": "hours", "date": "2026-03-09", "metadata": { "quality": "good" } },
        { "type": "steps", "value": 8420, "unit": "count", "date": "2026-03-08" },
        { "type": "workout", "value": 45, "unit": "minutes", "date": "2026-03-07", "metadata": { "type": "strength" } }
    ]
}

Response 200:
{
    "synced": 3,
    "message": "Health data synced successfully"
}
```

#### 5.2.12 Contacts Sync (iOS → Backend)

**POST /api/v1/contacts/sync**

```
Request:
{
    "contacts": [
        {
            "apple_contact_id": "ABC-123",
            "full_name": "Dorsa Sadigh",
            "email_addresses": ["sadigh@cs.stanford.edu"],
            "organization": "Stanford University",
            "job_title": "Professor",
            "relationship": "professor"
        }
    ],
    "full_sync": false              // true = replace all; false = upsert only
}

Response 200:
{
    "synced": 47,
    "created": 2,
    "updated": 45
}
```

### 5.3 WebSocket Protocol

**WS /api/v1/stream**

Connection: `wss://tars.{domain}.com/api/v1/stream?token={api_key}&device={device_token}`

#### Server → Client Messages

```json
// New message from T.A.R.S.
{
    "type": "message",
    "conversation_id": "uuid",
    "message": { "text": "...", "content_type": "text" }
}

// Approval request
{
    "type": "approval_request",
    "approval": { "id": "uuid", "action_type": "send_email", "title": "...", "preview": { ... } }
}

// Agent status update
{
    "type": "agent_status",
    "task_id": "uuid",
    "agent_type": "job_search",
    "status": "running",
    "progress": "Screening 47 listings..."
}

// Notification
{
    "type": "notification",
    "priority": "critical",           // "critical" | "normal" | "low"
    "title": "[ALERT] AtlasDesk API down",
    "body": "API returned 503. Claude analyzing logs...",
    "action_url": "/health"
}

// Briefing ready
{
    "type": "briefing_ready",
    "briefing_id": "uuid",
    "briefing_type": "morning"
}

// Deploy status
{
    "type": "deploy_status",
    "status": "complete",             // "pulling" | "restarting" | "complete" | "failed"
    "message": "Deploy complete. All services healthy."
}
```

#### Client → Server Messages

```json
// Heartbeat / keepalive
{
    "type": "ping"
}

// Typing indicator (optional)
{
    "type": "typing",
    "conversation_id": "uuid"
}
```

---

## 6. Agent System Design

### 6.1 Orchestrator Architecture

```python
# Conceptual structure of the orchestrator engine

class Orchestrator:
    """
    Always-running asyncio daemon on Node 1.
    Central nervous system of T.A.R.S.
    """
    
    def __init__(self):
        self.intent_classifier = IntentClassifier()
        self.model_router = ModelRouter()
        self.context_builder = ContextBuilder()
        self.approval_manager = ApprovalManager()
        self.response_formatter = ResponseFormatter()
        self.scheduler = APScheduler()
        self.redis_client = Redis(host="10.0.1.2")
        self.ws_manager = WebSocketManager()
    
    async def process_message(self, message: UserMessage) -> TARSResponse:
        """Main message processing pipeline."""
        # 1. Classify intent
        intent = self.intent_classifier.classify(message)
        
        # 2. Route to appropriate model/handler
        route = self.model_router.route(intent)
        
        # 3. Build scoped context for the agent
        context = await self.context_builder.build(intent, route)
        
        # 4. Execute agent
        if route.node == "node2":
            result = await self._dispatch_to_node2(route, context)
        elif route.model == "claude":
            result = await self._spawn_claude(route, context)
        elif route.model.startswith("gemini"):
            result = await self._call_gemini(route, context)
        else:
            result = await self._handle_local(route, context)
        
        # 5. Check if approval needed
        if result.has_side_effects:
            approval = await self.approval_manager.create(result)
            return self.response_formatter.format_approval(approval)
        
        # 6. Format and return response
        return self.response_formatter.format(result)
```

### 6.2 Intent Classifier

Rule-based + keyword matching. No AI tokens consumed.

```python
# Intent classification rules (simplified)

INTENT_RULES = {
    # Exact command matches
    "/briefing":     Intent(agent="briefing", action="fetch"),
    "/schedule":     Intent(agent="daily_life", action="show_schedule"),
    "/status":       Intent(agent="health_monitor", action="check_all"),
    "/jobs":         Intent(agent="job_search", action="show_digest"),
    "/config":       Intent(agent="config", action="show"),
    "/outfit":       Intent(agent="fashion", action="suggest"),
    "/deploy":       Intent(agent="system", action="deploy"),
    
    # Keyword-based classification
    "schedule|calendar|event|appointment|meeting|remind": Intent(agent="daily_life"),
    "email|draft|write.*to|reply|respond":                Intent(agent="communication"),
    "job|career|apply|resume|cover.letter|interview":     Intent(agent="job_search"),
    "outfit|wear|clothes|wardrobe|fashion":               Intent(agent="fashion"),
    "find|search|compare|buy|product|recommend|review":   Intent(agent="product_research"),
    "code|implement|fix|debug|deploy|PR|branch":          Intent(agent="coding"),
    "research|paper|thesis|study":                        Intent(agent="research"),
    "atlasdesk|server|down|error|logs|grafana":           Intent(agent="health_monitor"),
    "spend|spent|budget|transaction|bank|money":           Intent(agent="finance"),
    "sleep|steps|workout|gym|exercise|health|fitness":     Intent(agent="health_fitness"),
    "shop|outlet|store|buy.*clothes":                      Intent(agent="fashion", action="shopping_advisor"),
}

class IntentClassifier:
    def classify(self, message: UserMessage) -> Intent:
        text = message.text.strip().lower()
        
        # 1. Check exact command matches
        if text in INTENT_RULES:
            return INTENT_RULES[text]
        
        # 2. Keyword regex matching
        for pattern, intent in INTENT_RULES.items():
            if re.search(pattern, text):
                return intent
        
        # 3. Fallback: general conversation (route to Gemini Flash for lightweight response)
        return Intent(agent="general", model_hint="gemini_flash")
```

### 6.3 Model Router

```python
# Model routing decision matrix

class ModelRouter:
    # Agent → default model mapping
    AGENT_MODEL_MAP = {
        # Always Claude (complex reasoning required)
        "communication":    ModelRoute(model="claude", node="node1"),
        "coding":           ModelRoute(model="claude", node="node2"),
        "research":         ModelRoute(model="claude", node="node1"),
        
        # Always Gemini
        "email_classifier": ModelRoute(model="gemini_flash", node="node1"),
        "fashion":          ModelRoute(model="gemini_vision", node="node1"),
        "health_fitness":   ModelRoute(model="gemini_flash", node="node1"),
        "finance":          ModelRoute(model="gemini_flash", node="node1"),
        
        # Gemini default, Claude escalation
        "briefing":         ModelRoute(model="gemini_pro", node="node1"),
        "job_search":       ModelRoute(model="gemini_flash", node="node1"),  # pipeline uses multiple
        "product_research": ModelRoute(model="gemini_pro", node="node1"),
        "daily_life":       ModelRoute(model="gemini_flash", node="node1"),
        "eod_summary":      ModelRoute(model="gemini_pro", node="node1"),
        
        # Local only (zero AI tokens)
        "health_monitor":   ModelRoute(model="local", node="node1"),
        "config":           ModelRoute(model="local", node="node1"),
        "system":           ModelRoute(model="local", node="node1"),
        "general":          ModelRoute(model="gemini_flash", node="node1"),
    }
    
    def route(self, intent: Intent) -> ModelRoute:
        base_route = self.AGENT_MODEL_MAP.get(intent.agent)
        
        # Override checks
        if intent.requires_vision:
            base_route.model = "gemini_vision"
        if intent.complexity == "high" and base_route.model != "claude":
            base_route.model = "claude"  # escalate
        if intent.needs_docker_sandbox:
            base_route.node = "node2"
        
        return base_route
```

### 6.4 Claude Code Spawning Mechanism (MCP-Enhanced)

Claude Code agents are enhanced with MCP (Model Context Protocol) servers, giving them
direct access to external tools without manual context injection. This is a key
architectural decision — instead of pre-building context in Python and injecting it
into prompts, Claude Code pulls its own context via MCP tools.

**MCP Server Configuration** (`.mcp.json` in project root):

```json
{
    "mcpServers": {
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": { "GITHUB_TOKEN": "${GITHUB_PAT}" }
        },
        "postgres": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-postgres"],
            "env": { "DATABASE_URL": "${DATABASE_URL}" }
        },
        "brave-search": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": { "BRAVE_API_KEY": "${BRAVE_API_KEY}" }
        },
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data/repos", "/data/outputs"]
        }
    }
}
```

**How MCP changes each Claude agent:**

| Agent | Before MCP | After MCP |
|-------|-----------|-----------|
| **Coding/DevOps** | Dispatch to Node 2 → clone repo → inject CLAUDE.md → spawn | Spawn with GitHub MCP + filesystem MCP — Claude clones, reads, writes directly |
| **System Diagnostics** | Query Loki in Python → inject logs into prompt | Claude queries PostgreSQL MCP for health logs, pulls what it needs |
| **Research** | Build search results in Python → inject | Claude uses Brave Search MCP to research directly |
| **Communication** | Query state DB for past drafts → inject context | Claude queries PostgreSQL MCP for recipient history |

**Spawner Implementation:**

```python
import asyncio
import subprocess
import json

class ClaudeSpawner:
    """Spawns Claude Code as a headless subprocess with MCP server access."""
    
    CLAUDE_BINARY = "/usr/local/bin/claude"
    MAX_TIMEOUT = 120  # seconds
    
    # MCP profiles: which servers each agent type needs
    MCP_PROFILES = {
        "coding":         ["github", "filesystem", "postgres"],
        "research":       ["brave-search", "postgres"],
        "diagnostics":    ["postgres", "brave-search"],
        "communication":  ["postgres"],
        "general":        ["brave-search"],
    }
    
    async def spawn(self, prompt: str, context: AgentContext, agent_type: str = "general") -> AgentResult:
        """
        Spawn a single Claude Code process with MCP servers.
        MCP servers give Claude direct access to tools — reducing
        the need for pre-built context injection.
        """
        full_prompt = self._build_prompt(prompt, context)
        mcp_servers = self.MCP_PROFILES.get(agent_type, [])
        
        # Build allowed tools list from MCP profile
        allowed_tools = []
        for server in mcp_servers:
            allowed_tools.append(f"mcp__{server}__*")
        
        cmd = [
            self.CLAUDE_BINARY,
            "--print",                       # output-only mode
            "--output-format", "json",       # structured JSON output
            "--max-turns", "5",              # allow multi-turn for MCP tool use
        ]
        
        # Add allowed MCP tools
        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/data/repos",           # working directory for filesystem MCP
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=full_prompt.encode()),
                timeout=self.MAX_TIMEOUT
            )
            
            if process.returncode != 0:
                raise ClaudeSpawnError(f"Claude exited {process.returncode}: {stderr.decode()}")
            
            result = json.loads(stdout.decode())
            return AgentResult(
                content=result,
                model="claude_code",
                tokens_input=result.get("usage", {}).get("input_tokens", 0),
                tokens_output=result.get("usage", {}).get("output_tokens", 0),
            )
            
        except asyncio.TimeoutError:
            raise ClaudeSpawnError(f"Claude timed out after {self.MAX_TIMEOUT}s")
    
    def _build_prompt(self, prompt: str, context: AgentContext) -> str:
        """Build the full prompt. With MCP, context injection is minimal —
        Claude pulls additional data via MCP tools as needed."""
        return f"""You are T.A.R.S., Tasin's personal AI assistant.

CONTEXT (baseline — use MCP tools to pull additional data as needed):
{context.to_prompt_string()}

TASK:
{prompt}

RULES:
- Use MCP tools to query databases, search the web, or access repos as needed.
- Respond ONLY with valid JSON matching the required output schema.
- All factual claims must be grounded in data (from context or MCP tool results).
- Never fabricate information not present in context or tool results.
- If you cannot complete the task, return {{"error": "reason"}}.
"""
```

**MCP Setup Requirements:**
- Node.js 18+ must be installed on Node 1 (for npx-based MCP servers)
- MCP servers are registered in `.mcp.json` at the project root
- Environment variables for MCP auth tokens stored in the encrypted secrets store
- Claude Code automatically discovers and connects to configured MCP servers

### 6.5 Gemini API Client Pattern

```python
import google.generativeai as genai

class GeminiClient:
    """Unified client for Gemini Flash, Pro, and Vision."""
    
    MODELS = {
        "gemini_flash": "gemini-2.0-flash",
        "gemini_pro": "gemini-2.0-pro",
        "gemini_vision": "gemini-2.0-flash",  # Flash with image input
    }
    
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
    
    async def generate(
        self,
        prompt: str,
        model_tier: str = "gemini_flash",
        images: list[bytes] | None = None,
        response_schema: dict | None = None,
        temperature: float = 0.3,
    ) -> AgentResult:
        model = genai.GenerativeModel(
            self.MODELS[model_tier],
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                response_mime_type="application/json" if response_schema else None,
                response_schema=response_schema,
            ),
        )
        
        content = [prompt]
        if images:
            for img in images:
                content.append({"mime_type": "image/jpeg", "data": img})
        
        response = await model.generate_content_async(content)
        
        return AgentResult(
            content=response.text,
            model=model_tier,
            tokens_input=response.usage_metadata.prompt_token_count,
            tokens_output=response.usage_metadata.candidates_token_count,
        )
```

### 6.6 Approval Queue Flow

```python
class ApprovalManager:
    """Manages the approval lifecycle for all Tier 2 and Tier 3 actions."""
    
    # Risk classification rules
    TIER_MAP = {
        "send_email":       "tier2_approval",
        "create_event":     "tier2_approval",
        "archive_emails":   "tier2_approval",
        "create_notion":    "tier2_approval",
        "create_pr":        "tier2_approval",
        "apply_job":        "tier2_approval",
        "email_professor":  "tier3_escalation",
        "push_production":  "tier3_escalation",
        "delete_data":      "tier3_escalation",
        "modify_infra":     "tier3_escalation",
        # Tier 1 (autonomous) actions are never queued — they execute immediately
    }
    
    DEFAULT_EXPIRY = timedelta(hours=1)
    
    async def create(self, result: AgentResult) -> Approval:
        tier = self.TIER_MAP.get(result.action_type, "tier2_approval")
        
        approval = await self.repo.insert(Approval(
            task_id=result.task_id,
            action_type=result.action_type,
            risk_tier=tier,
            status="pending",
            title=result.approval_title,
            preview_payload=result.preview,
            expires_at=datetime.utcnow() + self.DEFAULT_EXPIRY,
        ))
        
        # Push to all connected clients
        await self.ws_manager.broadcast({
            "type": "approval_request",
            "approval": approval.to_dict()
        })
        
        # Push notification (APNs)
        await self.apns_client.send(
            title=f"T.A.R.S. Approval: {approval.title}",
            body=f"Action: {result.action_type}",
            category="APPROVAL",
            custom_data={"approval_id": str(approval.id)},
        )
        
        return approval
    
    async def process_decision(self, approval_id: UUID, decision: str, source: str, edited_payload: dict = None):
        approval = await self.repo.get(approval_id)
        
        if approval.status != "pending":
            raise ApprovalAlreadyDecidedError()
        if approval.expires_at < datetime.utcnow():
            raise ApprovalExpiredError()
        
        approval.status = decision  # 'approved', 'rejected', 'edited'
        approval.decision_source = source
        approval.decided_at = datetime.utcnow()
        
        if decision in ("approved", "edited"):
            payload = edited_payload or approval.preview_payload
            await self._execute_action(approval, payload)
            approval.status = "executed"
            approval.executed_at = datetime.utcnow()
        
        await self.repo.update(approval)
        await self._log_audit(approval)
```

### 6.7 Inter-Node Job Dispatch via Redis

```python
# Node 1: Enqueue job
class JobDispatcher:
    """Dispatches heavy jobs from Node 1 to Node 2 via Redis queue."""
    
    QUEUE_NAME = "tars:jobs"
    
    async def dispatch(self, job_type: str, payload: dict, priority: int = 0) -> str:
        job_id = str(uuid4())
        job = {
            "id": job_id,
            "type": job_type,       # "code_execution", "research", "diagnostics", "job_scraping", "image_processing"
            "payload": payload,
            "priority": priority,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        # Push to Redis sorted set (priority queue)
        await self.redis.zadd(self.QUEUE_NAME, {json.dumps(job): priority})
        
        # Subscribe to result channel
        result = await self._wait_for_result(job_id, timeout=300)
        return result
    
    async def _wait_for_result(self, job_id: str, timeout: int) -> dict:
        channel = f"tars:results:{job_id}"
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                return json.loads(message["data"])

# Node 2: Process job
class JobWorker:
    """Runs on Node 2, consumes jobs from Redis queue."""
    
    EXECUTORS = {
        "code_execution":   CodeExecutor(),
        "research":         ResearchExecutor(),
        "diagnostics":      DiagnosticExecutor(),
        "job_scraping":     JobScraperExecutor(),
        "image_processing": ImageProcessorExecutor(),
    }
    
    async def run(self):
        while True:
            # Pop highest-priority job
            job_data = await self.redis.zpopmin(QUEUE_NAME)
            if not job_data:
                await asyncio.sleep(1)
                continue
            
            job = json.loads(job_data[0])
            executor = self.EXECUTORS[job["type"]]
            
            try:
                result = await executor.execute(job["payload"])
                await self.redis.publish(
                    f"tars:results:{job['id']}",
                    json.dumps({"status": "completed", "result": result})
                )
            except Exception as e:
                await self.redis.publish(
                    f"tars:results:{job['id']}",
                    json.dumps({"status": "failed", "error": str(e)})
                )
```

---

## 7. Docker Compose Configuration

### 7.1 Node 1 — docker-compose.yml

```yaml
# deploy/node1/docker-compose.yml
version: "3.9"

services:
  # ── T.A.R.S. Backend (API + Orchestrator + Scheduler + Telegram + Wake Word) ──
  tars-backend:
    image: ghcr.io/tasin/tars-backend:latest
    container_name: tars-backend
    restart: unless-stopped
    ports:
      - "8000:8000"                      # REST API + WebSocket
    environment:
      - DATABASE_URL=postgresql+asyncpg://tars:${POSTGRES_PASSWORD}@tars-db:5432/tars
      - REDIS_URL=redis://10.0.1.2:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - GMAIL_PERSONAL_CREDENTIALS=${GMAIL_PERSONAL_CREDENTIALS}
      - GMAIL_PROFESSIONAL_CREDENTIALS=${GMAIL_PROFESSIONAL_CREDENTIALS}
      - ICLOUD_CALDAV_USER=${ICLOUD_CALDAV_USER}
      - ICLOUD_CALDAV_PASSWORD=${ICLOUD_CALDAV_PASSWORD}
      - GITHUB_PAT=${GITHUB_PAT}
      - NOTION_TOKEN=${NOTION_TOKEN}
      - PLAID_CLIENT_ID=${PLAID_CLIENT_ID}
      - PLAID_SECRET=${PLAID_SECRET}
      - PLAID_ACCESS_TOKEN=${PLAID_ACCESS_TOKEN}
      - OPENWEATHERMAP_API_KEY=${OPENWEATHERMAP_API_KEY}
      - PICOVOICE_ACCESS_KEY=${PICOVOICE_ACCESS_KEY}
      - APNS_KEY_ID=${APNS_KEY_ID}
      - APNS_TEAM_ID=${APNS_TEAM_ID}
      - APNS_KEY_PATH=/secrets/apns-key.p8
      - TARS_API_KEY=${TARS_API_KEY}
      - GRAFANA_URL=${GRAFANA_URL}
      - GRAFANA_API_KEY=${GRAFANA_API_KEY}
      - LOKI_URL=${LOKI_URL}
      - SERPAPI_KEY=${SERPAPI_KEY}
      - CLOUDFLARE_TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
      - NODE_ROLE=brain
      - LOG_LEVEL=INFO
    volumes:
      - ./secrets:/secrets:ro              # APNs key, Porcupine model
      - tars-data:/data                    # persistent data
    devices:
      - /dev/snd:/dev/snd                 # USB mic audio access
    depends_on:
      tars-db:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - tars-net
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

  # ── PostgreSQL ──
  tars-db:
    image: postgres:16-alpine
    container_name: tars-db
    restart: unless-stopped
    environment:
      - POSTGRES_DB=tars
      - POSTGRES_USER=tars
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tars -d tars"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - tars-net
    deploy:
      resources:
        limits:
          memory: 2G

  # ── Cloudflare Tunnel ──
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    restart: unless-stopped
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    networks:
      - tars-net
    depends_on:
      - tars-backend

volumes:
  pgdata:
    driver: local
  tars-data:
    driver: local

networks:
  tars-net:
    driver: bridge
```

### 7.2 Node 2 — docker-compose.yml

```yaml
# deploy/node2/docker-compose.yml
version: "3.9"

services:
  # ── Redis (Job Queue + Pub/Sub + Cache) ──
  redis:
    image: redis:7-alpine
    container_name: tars-redis
    restart: unless-stopped
    command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - tars-net
    deploy:
      resources:
        limits:
          memory: 2G

  # ── ChromaDB (Vector Store / Semantic Memory) ──
  chromadb:
    image: chromadb/chroma:0.5.23
    container_name: tars-chromadb
    restart: unless-stopped
    ports:
      - "8200:8000"
    volumes:
      - chroma-data:/chroma/chroma
    environment:
      - ANONYMIZED_TELEMETRY=false
      - CHROMA_SERVER_AUTHN_PROVIDER=chromadb.auth.token_authn.TokenAuthenticationServerProvider
      - CHROMA_SERVER_AUTHN_CREDENTIALS=${CHROMA_AUTH_TOKEN}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/heartbeat"]
      interval: 30s
      timeout: 10s
      retries: 3
    networks:
      - tars-net
    deploy:
      resources:
        limits:
          memory: 2G

  # ── Job Worker (processes jobs from Redis queue) ──
  tars-worker:
    image: ghcr.io/tasin/tars-worker:latest
    container_name: tars-worker
    restart: unless-stopped
    environment:
      - REDIS_URL=redis://redis:6379/0
      - CHROMADB_URL=http://chromadb:8000
      - CHROMA_AUTH_TOKEN=${CHROMA_AUTH_TOKEN}
      - NODE_ROLE=muscle
      - LOG_LEVEL=INFO
    volumes:
      - worker-data:/data                  # wardrobe images, agent outputs, repos, logs
      - /var/run/docker.sock:/var/run/docker.sock  # Docker-in-Docker for sandboxed execution
    depends_on:
      redis:
        condition: service_healthy
      chromadb:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import redis; r=redis.Redis(); r.ping()"]
      interval: 30s
      timeout: 10s
      retries: 3
    networks:
      - tars-net
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

volumes:
  redis-data:
    driver: local
  chroma-data:
    driver: local
  worker-data:
    driver: local

networks:
  tars-net:
    driver: bridge
```

---

## 8. CI/CD Pipeline

### 8.1 GitHub Actions: Build & Push

```yaml
# .github/workflows/build-and-push.yml
name: Build and Push Docker Images

on:
  push:
    branches: [main]
    paths:
      - "backend/**"
      - "worker/**"
      - "deploy/**"
  workflow_dispatch:              # manual trigger

env:
  REGISTRY: ghcr.io
  BACKEND_IMAGE: ghcr.io/${{ github.repository_owner }}/tars-backend
  WORKER_IMAGE: ghcr.io/${{ github.repository_owner }}/tars-worker

jobs:
  build-backend:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build and push backend
        uses: docker/build-push-action@v6
        with:
          context: ./backend
          push: true
          tags: |
            ${{ env.BACKEND_IMAGE }}:latest
            ${{ env.BACKEND_IMAGE }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-worker:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build and push worker
        uses: docker/build-push-action@v6
        with:
          context: ./worker
          push: true
          tags: |
            ${{ env.WORKER_IMAGE }}:latest
            ${{ env.WORKER_IMAGE }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  notify-deploy:
    needs: [build-backend, build-worker]
    runs-on: ubuntu-latest
    steps:
      - name: Notify T.A.R.S. of new build
        run: |
          curl -X POST "https://tars.${{ secrets.DOMAIN }}/api/v1/deploy" \
            -H "Authorization: Bearer ${{ secrets.TARS_API_KEY }}" \
            -H "Content-Type: application/json" \
            -d '{"confirm": false, "sha": "${{ github.sha }}", "notify_only": true}'
```

### 8.2 Deploy Script (on servers)

```bash
#!/bin/bash
# deploy/scripts/deploy.sh
# Run on each node to pull latest images and restart

set -euo pipefail

NODE_DIR="${1:-.}"
cd "$NODE_DIR"

echo "[T.A.R.S. Deploy] Pulling latest images..."
docker compose pull

echo "[T.A.R.S. Deploy] Restarting services..."
docker compose up -d --remove-orphans

echo "[T.A.R.S. Deploy] Waiting for health checks..."
sleep 10

echo "[T.A.R.S. Deploy] Checking service health..."
docker compose ps

echo "[T.A.R.S. Deploy] Done."
```

### 8.3 Backup Script

```bash
#!/bin/bash
# deploy/scripts/backup.sh
# Daily PostgreSQL backup + Docker volume snapshots

set -euo pipefail

BACKUP_DIR="/data/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# PostgreSQL dump
echo "[Backup] Dumping PostgreSQL..."
docker exec tars-db pg_dump -U tars -d tars -Fc > "$BACKUP_DIR/tars_db_${DATE}.dump"

# Keep last 7 daily backups
find "$BACKUP_DIR" -name "tars_db_*.dump" -mtime +7 -delete

echo "[Backup] Complete: tars_db_${DATE}.dump"
```

---

## 9. iOS App Architecture

### 9.1 Architecture Pattern: MVVM + Repository

```
┌─────────────────────────────────────────────────┐
│                   SwiftUI Views                  │
│  ChatView, BriefingView, ScheduleView, etc.     │
└──────────────────────┬──────────────────────────┘
                       │ @StateObject / @ObservedObject
                       ▼
┌─────────────────────────────────────────────────┐
│                  ViewModels                      │
│  ChatViewModel, BriefingViewModel, etc.         │
│  Holds UI state, transforms data for display    │
└──────────────────────┬──────────────────────────┘
                       │ async/await
                       ▼
┌─────────────────────────────────────────────────┐
│              Repository Layer                    │
│  TARSRepository (single source of truth)        │
│  Coordinates API calls + local cache            │
└──────────────────────┬──────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌─────────────┐ ┌────────────┐ ┌─────────────┐
│  APIClient  │ │  WebSocket │ │ Framework   │
│  (REST)     │ │  Manager   │ │ Services    │
│             │ │            │ │ EventKit    │
│             │ │            │ │ HealthKit   │
│             │ │            │ │ Contacts    │
│             │ │            │ │ Speech/TTS  │
└─────────────┘ └────────────┘ └─────────────┘
```

### 9.2 Key Service Implementations

#### APIClient.swift (Core Pattern)

```swift
actor APIClient {
    private let baseURL: URL
    private let apiKey: String
    private let deviceToken: String
    private let session: URLSession
    
    init(baseURL: URL, apiKey: String, deviceToken: String) {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.deviceToken = deviceToken
        self.session = URLSession(configuration: .default)
    }
    
    func request<T: Decodable>(_ endpoint: Endpoint) async throws -> T {
        var request = URLRequest(url: baseURL.appending(path: endpoint.path))
        request.httpMethod = endpoint.method.rawValue
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue(deviceToken, forHTTPHeaderField: "X-Device-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        if let body = endpoint.body {
            request.httpBody = try JSONEncoder().encode(body)
        }
        
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw TARSError.invalidResponse
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            let error = try? JSONDecoder().decode(APIError.self, from: data)
            throw TARSError.api(statusCode: httpResponse.statusCode, error: error)
        }
        
        return try JSONDecoder().decode(T.self, from: data)
    }
}
```

#### HealthKitService.swift (Data Sync Pattern)

```swift
class HealthKitService {
    private let healthStore = HKHealthStore()
    
    // Types we read (never write)
    private let readTypes: Set<HKObjectType> = [
        HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!,
        HKObjectType.quantityType(forIdentifier: .stepCount)!,
        HKObjectType.quantityType(forIdentifier: .activeEnergyBurned)!,
        HKObjectType.quantityType(forIdentifier: .restingHeartRate)!,
        HKObjectType.quantityType(forIdentifier: .appleExerciseTime)!,
        HKObjectType.workoutType(),
    ]
    
    func requestAuthorization() async throws {
        try await healthStore.requestAuthorization(toShare: [], read: readTypes)
    }
    
    func syncToBackend(apiClient: APIClient) async throws {
        let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date())!
        
        let sleep = try await querySleep(since: yesterday)
        let steps = try await querySteps(since: yesterday)
        let workouts = try await queryWorkouts(since: yesterday)
        
        let payload = HealthSyncPayload(data: sleep + steps + workouts)
        try await apiClient.request(.syncHealthData(payload))
    }
}
```

### 9.3 Push Notification Setup

```swift
// APNs registration in TARSApp.swift
class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: ...) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        
        // Register approval action category
        let approveAction = UNNotificationAction(identifier: "APPROVE", title: "Approve", options: [.authenticationRequired])
        let rejectAction = UNNotificationAction(identifier: "REJECT", title: "Reject", options: [.destructive])
        let category = UNNotificationCategory(identifier: "APPROVAL", actions: [approveAction, rejectAction], intentIdentifiers: [])
        UNUserNotificationCenter.current().setNotificationCategories([category])
        
        application.registerForRemoteNotifications()
        return true
    }
    
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
        // Send token to T.A.R.S. backend for APNs push
        Task { await APIClient.shared.registerDevice(token: token) }
    }
    
    // Handle notification actions inline
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard let approvalId = response.notification.request.content.userInfo["approval_id"] as? String else { return }
        
        switch response.actionIdentifier {
        case "APPROVE":
            try? await APIClient.shared.request(.approveAction(id: approvalId))
        case "REJECT":
            try? await APIClient.shared.request(.rejectAction(id: approvalId))
        default: break
        }
    }
}
```

### 9.4 Apple Watch Companion Architecture

```
┌─────────────────────────────┐      ┌─────────────────────────────┐
│        iPhone App            │      │      Apple Watch App         │
│                              │      │                              │
│  WatchConnectivity           │◀────▶│  WatchConnectivity           │
│  (WCSession)                 │      │  (WCSession)                 │
│                              │      │                              │
│  Sends:                      │      │  Receives:                   │
│  - schedule data             │      │  - next event                │
│  - pending approvals         │      │  - pending approval count    │
│  - health status             │      │  - system health color       │
│                              │      │                              │
│  Receives:                   │      │  Sends:                      │
│  - approval decisions        │      │  - approve/reject            │
│  - voice command text        │      │  - voice input               │
└─────────────────────────────┘      └─────────────────────────────┘
                                      │
                                      ├── Complication: next event + approvals count
                                      ├── Notification: inline approve/reject
                                      └── Glance: schedule + health status
```

### 9.5 Siri Shortcuts Setup

```swift
// SiriKit App Intents (iOS 16+ App Intents framework)
struct AskTARSIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask T.A.R.S."
    static var description: IntentDescription = "Send a command to T.A.R.S."
    
    @Parameter(title: "Command")
    var command: String
    
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let response: MessageResponse = try await APIClient.shared.request(
            .sendMessage(text: command, source: "siri")
        )
        return .result(dialog: "\(response.response.text)")
    }
}

// Predefined shortcuts
struct GetBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Get T.A.R.S. Briefing"
    
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let briefing: Briefing = try await APIClient.shared.request(.getBriefing())
        return .result(dialog: "\(briefing.narrative)")
    }
}
```

### 9.6 HomePod AirPlay Integration

The iOS app acts as the bridge for HomePod audio output:

1. **Morning Briefing**: Backend pushes notification "Briefing ready" → iOS app fetches briefing text → AVSpeechSynthesizer generates audio → Routes audio output to HomePod via `AVAudioSession.routeSharingPolicy = .longFormAudio` with AirPlay route selection.

2. **Siri Bridge**: User says "Hey Siri, ask TARS for my briefing" → Siri triggers AskTARSIntent → Intent fetches from backend → Response spoken through HomePod.

3. **Server-side alternative** (Phase 4): Node 1 generates TTS audio file → streams via pyatv AirPlay library → HomePod plays directly. This enables wake-word → response → HomePod without iPhone involvement.

---

## 10. Wake Word System Design

### 10.1 Architecture

```
USB Conference Mic (always listening)
        │
        │ raw PCM audio (16kHz, 16-bit, mono)
        ▼
┌───────────────────────────────────────────────────────┐
│  Porcupine Wake Word Engine (pvporcupine)             │
│  Model: custom "Hey TARS" (.ppn file)                 │
│  Runs on CPU — <5% usage, ~10MB RAM                   │
│  Continuously processes audio frames (512 samples)     │
└───────────────────────┬───────────────────────────────┘
                        │ wake word detected!
                        ▼
┌───────────────────────────────────────────────────────┐
│  Audio Recorder                                        │
│  Records post-wake-word speech until silence detected  │
│  Voice Activity Detection (VAD): WebRTC VAD or        │
│  energy-based silence detection (1.5s silence = stop)  │
└───────────────────────┬───────────────────────────────┘
                        │ recorded audio (WAV)
                        ▼
┌───────────────────────────────────────────────────────┐
│  Speech-to-Text (Whisper — local)                      │
│  Model: whisper-small or whisper-base                  │
│  Runs locally on CPU (~1-3 sec latency for short cmds) │
│  Fallback: Google Cloud Speech-to-Text API             │
└───────────────────────┬───────────────────────────────┘
                        │ transcribed text
                        ▼
┌───────────────────────────────────────────────────────┐
│  Orchestrator (normal message pipeline)                │
│  source = "wake_word"                                  │
│  Processes command → generates response                │
└───────────────────────┬───────────────────────────────┘
                        │ response text
                        ▼
┌───────────────────────────────────────────────────────┐
│  TTS Engine (pyttsx3 or gTTS)                          │
│  Converts response text to audio                       │
└───────────────────────┬───────────────────────────────┘
                        │ audio stream
                        ▼
┌───────────────────────────────────────────────────────┐
│  AirPlay Output (pyatv)                                │
│  Streams audio to HomePod Mini on local network        │
│  Fallback: play through USB speaker if HomePod offline │
└───────────────────────────────────────────────────────┘
```

### 10.2 Implementation

```python
# backend/src/wake_word/listener.py

import pvporcupine
import pyaudio
import struct
import asyncio
from pathlib import Path

class WakeWordListener:
    """Always-on wake word detection daemon."""
    
    SAMPLE_RATE = 16000
    FRAME_LENGTH = 512  # Porcupine frame size
    SILENCE_THRESHOLD = 500  # energy threshold
    SILENCE_DURATION = 1.5  # seconds of silence to stop recording
    MAX_RECORD_DURATION = 15  # max seconds of speech
    
    def __init__(self, access_key: str, model_path: str):
        self.porcupine = pvporcupine.create(
            access_key=access_key,
            keyword_paths=[model_path],    # custom "Hey TARS" .ppn file
            sensitivities=[0.6],           # 0.0-1.0, higher = more sensitive
        )
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(
            rate=self.SAMPLE_RATE,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.FRAME_LENGTH,
            input_device_index=self._find_usb_mic(),
        )
    
    def _find_usb_mic(self) -> int:
        """Find the USB conference mic device index."""
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            if "usb" in info["name"].lower() and info["maxInputChannels"] > 0:
                return i
        raise RuntimeError("USB microphone not found")
    
    async def listen_loop(self, orchestrator):
        """Main detection loop — runs forever."""
        while True:
            pcm = self.stream.read(self.FRAME_LENGTH, exception_on_overflow=False)
            pcm_unpacked = struct.unpack_from("h" * self.FRAME_LENGTH, pcm)
            
            keyword_index = self.porcupine.process(pcm_unpacked)
            
            if keyword_index >= 0:
                # Wake word detected!
                audio_data = await self._record_speech()
                text = await self.stt_processor.transcribe(audio_data)
                
                if text.strip():
                    response = await orchestrator.process_message(
                        UserMessage(text=text, source="wake_word")
                    )
                    await self.tts_output.speak(response.text)
            
            await asyncio.sleep(0)  # yield to event loop
    
    async def _record_speech(self) -> bytes:
        """Record audio until silence detected."""
        frames = []
        silence_frames = 0
        max_frames = int(self.MAX_RECORD_DURATION * self.SAMPLE_RATE / self.FRAME_LENGTH)
        silence_limit = int(self.SILENCE_DURATION * self.SAMPLE_RATE / self.FRAME_LENGTH)
        
        for _ in range(max_frames):
            pcm = self.stream.read(self.FRAME_LENGTH, exception_on_overflow=False)
            frames.append(pcm)
            
            # Check energy for silence detection
            pcm_unpacked = struct.unpack_from("h" * self.FRAME_LENGTH, pcm)
            energy = sum(abs(s) for s in pcm_unpacked) / self.FRAME_LENGTH
            
            if energy < self.SILENCE_THRESHOLD:
                silence_frames += 1
                if silence_frames >= silence_limit:
                    break
            else:
                silence_frames = 0
        
        return b"".join(frames)
```

---

## 11. Integration Patterns

### 11.1 Common Integration Pattern

Every integration follows this adapter pattern:

```python
class BaseIntegration(ABC):
    """Base class for all external service integrations."""
    
    def __init__(self, config: IntegrationConfig):
        self.config = config
        self.http = httpx.AsyncClient(timeout=30)
        self._token = None
        self._token_expires = None
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if service is reachable."""
        pass
    
    async def _ensure_auth(self):
        """Refresh auth token if expired."""
        if self._token and self._token_expires > datetime.utcnow():
            return
        await self._refresh_token()
    
    @abstractmethod
    async def _refresh_token(self):
        pass
```

### 11.2 iCloud CalDAV

```python
class CalDAVClient(BaseIntegration):
    CALDAV_URL = "https://caldav.icloud.com"
    
    async def get_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        client = caldav.DAVClient(
            url=self.CALDAV_URL,
            username=self.config.icloud_user,
            password=self.config.icloud_app_password,  # App-Specific Password
        )
        principal = client.principal()
        calendars = principal.calendars()
        
        events = []
        for cal in calendars:
            results = cal.date_search(start=start, end=end, expand=True)
            for event in results:
                events.append(self._parse_vevent(event))
        
        return sorted(events, key=lambda e: e.start)
    
    async def create_event(self, title: str, start: datetime, end: datetime, calendar_name: str = "Personal") -> str:
        # Creates event via CalDAV — requires approval first
        ...
    
    # Poll interval: 5 minutes (configurable)
    # Auth: iCloud App-Specific Password — no refresh needed, long-lived
```

### 11.3 Gmail API

```python
class GmailClient(BaseIntegration):
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
              "https://www.googleapis.com/auth/gmail.send",
              "https://www.googleapis.com/auth/gmail.modify"]
    
    def __init__(self, account_name: str, credentials_json: str):
        self.account_name = account_name
        self.creds = self._load_credentials(credentials_json)
        self.service = build("gmail", "v1", credentials=self.creds)
    
    async def _refresh_token(self):
        """OAuth 2.0 refresh token flow."""
        if self.creds.expired and self.creds.refresh_token:
            self.creds.refresh(Request())
            self._save_credentials()  # persist refreshed token
    
    async def get_unread(self, since_hours: int = 12) -> list[Email]:
        query = f"is:unread after:{int((datetime.utcnow() - timedelta(hours=since_hours)).timestamp())}"
        results = self.service.users().messages().list(userId="me", q=query).execute()
        ...
    
    async def send_email(self, to: str, subject: str, body: str) -> str:
        """Send email — ONLY called after approval."""
        ...
    
    # Auth: OAuth 2.0 offline access
    # Token refresh: automatic via google-auth library
    # Polling: every 5 minutes or Gmail push notifications (Pub/Sub)
```

### 11.4 GitHub API

```python
class GitHubClient(BaseIntegration):
    BASE_URL = "https://api.github.com"
    
    # Auth: Personal Access Token (PAT) — no refresh needed
    # Scopes: repo, notifications
    # Polling: every 15 minutes + on-demand
    
    async def get_notifications(self, since: datetime = None) -> list[GitHubNotification]:
        headers = {"Authorization": f"Bearer {self.config.github_pat}"}
        params = {"all": "false"}
        if since:
            params["since"] = since.isoformat()
        resp = await self.http.get(f"{self.BASE_URL}/notifications", headers=headers, params=params)
        ...
```

### 11.5 Plaid API

```python
class PlaidClient(BaseIntegration):
    # Auth: Plaid Link OAuth flow (user authenticates with bank directly)
    # T.A.R.S. stores: Plaid access_token (encrypted)
    # Access: READ-ONLY — transactions, balances, account metadata
    # Sync: Daily transaction pull at 5:00 AM + on-demand
    
    async def get_transactions(self, start_date: date, end_date: date) -> list[Transaction]:
        request = TransactionsGetRequest(
            access_token=self.config.plaid_access_token,
            start_date=start_date,
            end_date=end_date,
        )
        response = self.plaid_client.transactions_get(request)
        return [self._map_transaction(t) for t in response.transactions]
    
    # Error handling:
    # - ITEM_LOGIN_REQUIRED → notify user to re-authenticate via Plaid Link
    # - RATE_LIMIT → exponential backoff
    # - TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION → retry
```

### 11.6 Gemini API (see Section 6.5)

### 11.7 Notion API

```python
class NotionClient(BaseIntegration):
    BASE_URL = "https://api.notion.com/v1"
    
    # Auth: Internal integration token — no refresh needed
    # Databases: Tasks, Grocery List, PhD Timeline, Job Tracker, Wardrobe Catalog, Notes, Contact CRM
    
    async def create_page(self, database_id: str, properties: dict) -> str:
        """Create a new page in a Notion database."""
        headers = {
            "Authorization": f"Bearer {self.config.notion_token}",
            "Notion-Version": "2022-06-28",
        }
        payload = {"parent": {"database_id": database_id}, "properties": properties}
        resp = await self.http.post(f"{self.BASE_URL}/pages", headers=headers, json=payload)
        return resp.json()["id"]
```

### 11.8 OpenWeatherMap

```python
class WeatherClient(BaseIntegration):
    BASE_URL = "https://api.openweathermap.org/data/2.5"
    DEFAULT_LOCATION = {"lat": 40.8051, "lon": -81.9351}  # Wooster, Ohio
    
    # Auth: API key query parameter — no refresh needed
    # Free tier: 1,000 calls/day (more than sufficient)
    # Fetch: 5:45 AM for briefing + on-demand
    
    async def get_forecast(self) -> WeatherForecast:
        params = {**self.DEFAULT_LOCATION, "appid": self.config.api_key, "units": "metric"}
        resp = await self.http.get(f"{self.BASE_URL}/forecast", params=params)
        ...
```

### 11.9 Grafana / Loki

```python
class GrafanaClient(BaseIntegration):
    # Auth: Grafana API key
    # Access: via Cloudflare Tunnel from Wooster server
    # Polling: every 5 minutes for health checks
    
    async def query_loki(self, logql: str, start: datetime, end: datetime) -> list[LogEntry]:
        """Query Loki logs via HTTP API."""
        params = {
            "query": logql,
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "limit": 100,
        }
        headers = {"Authorization": f"Bearer {self.config.grafana_api_key}"}
        resp = await self.http.get(f"{self.config.loki_url}/loki/api/v1/query_range", headers=headers, params=params)
        ...
```

### 11.10 Job Board Adapters

```python
class JobBoardAdapter(ABC):
    """Adapter pattern — all job sources implement this interface."""
    
    @abstractmethod
    async def search(self, criteria: JobSearchCriteria) -> list[RawJobListing]:
        pass
    
    @abstractmethod
    def source_name(self) -> str:
        pass

class LinkedInAdapter(JobBoardAdapter):
    """LinkedIn Jobs via SerpAPI."""
    def source_name(self) -> str: return "linkedin"
    
    async def search(self, criteria: JobSearchCriteria) -> list[RawJobListing]:
        params = {
            "engine": "google_jobs",
            "q": " OR ".join(criteria.target_roles),
            "location": ", ".join(criteria.preferred_locations),
            "api_key": self.config.serpapi_key,
        }
        # ... parse results
        
class IndeedAdapter(JobBoardAdapter):
    """Indeed via API or Apify scraper."""
    ...

class YCombinatorAdapter(JobBoardAdapter):
    """Y Combinator Work at a Startup — public API."""
    ...

# New sources added by implementing JobBoardAdapter — zero changes to core
```

---

## 12. Security Design

### 12.1 Credential Management

```
┌──────────────────────────────────────────────┐
│           Credential Hierarchy                │
│                                               │
│  1. Environment Variables (.env files)        │
│     - Loaded by Docker Compose                │
│     - .env.production gitignored              │
│                                               │
│  2. SOPS Encrypted Files (optional upgrade)   │
│     - sops-encrypted YAML in repo             │
│     - Decrypted at container startup          │
│     - Key: age key stored on server only      │
│                                               │
│  3. Docker Secrets (for Swarm, optional)      │
│     - /run/secrets/ mounted read-only         │
│                                               │
│  4. iOS Keychain                              │
│     - API key + device token                  │
│     - Stored via Security framework           │
│     - Never in UserDefaults or plain storage  │
└──────────────────────────────────────────────┘
```

### 12.2 Encryption

| Layer | Method | Detail |
|-------|--------|--------|
| **In transit (external)** | TLS 1.3 | Cloudflare Tunnel terminates TLS. All external API calls over HTTPS. |
| **In transit (internal)** | WireGuard | Tailscale encrypts all inter-node traffic. |
| **At rest (database)** | PostgreSQL SSL | `sslmode=require` on all connections. Sensitive columns (tokens, keys) encrypted with `pgcrypto.pgp_sym_encrypt()`. |
| **At rest (secrets)** | SOPS + age | Secrets encrypted in repo, decrypted only on server. |
| **At rest (iOS)** | Keychain | Hardware-backed keychain for all credentials. |
| **At rest (backups)** | GPG | Backup files encrypted before storage. |

### 12.3 API Authentication

```python
# Simple but effective for single-user system

async def verify_auth(request: Request) -> bool:
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    device_token = request.headers.get("X-Device-Token", "")
    
    if not api_key or api_key != settings.TARS_API_KEY:
        raise HTTPException(401, "Invalid API key")
    
    if device_token and device_token not in settings.ALLOWED_DEVICE_TOKENS:
        raise HTTPException(401, "Unregistered device")
    
    return True

# Rate limiting: 100 req/min per device (safety net, not expected to hit)
# IP allowlist: optional — Tailscale IPs + Cloudflare IPs only
```

### 12.4 Audit Logging

Every action logged per HC-08:

```python
async def log_audit(action_type: str, actor: str, target: str, details: dict, source: str):
    await db.execute(
        audit_log.insert().values(
            action_type=action_type,
            actor=actor,
            target=target,
            details=details,
            source=source,
        )
    )

# Logged events:
# - Every API call (method, path, source, response code)
# - Every agent spawn (agent_type, model, input summary)
# - Every approval decision (approval_id, decision, source)
# - Every config change (key, old_value, new_value, changed_by)
# - Every external action (send_email, create_event, create_pr)
# - Every deploy trigger
# - Every auth failure
```

---

## 13. Monitoring & Observability

### 13.1 Self-Health Monitoring

T.A.R.S. monitors itself via the `/api/v1/health` endpoint and internal health daemon:

```python
class SelfHealthMonitor:
    """Runs every 60 seconds — checks all T.A.R.S. components."""
    
    async def check_all(self) -> SystemHealth:
        checks = await asyncio.gather(
            self._check_postgres(),
            self._check_redis(),
            self._check_chromadb(),
            self._check_node2_reachable(),
            self._check_disk_usage(),
            self._check_memory_usage(),
            self._check_ai_budget(),
            return_exceptions=True,
        )
        
        status = "green"
        if any(isinstance(c, Exception) for c in checks):
            status = "red"
        elif any(c.status == "yellow" for c in checks if not isinstance(c, Exception)):
            status = "yellow"
        
        # Log to system_health_log
        await self._log_health(status, checks)
        
        # Alert if degraded
        if status != "green":
            await self._send_alert(status, checks)
        
        return SystemHealth(status=status, checks=checks)
```

### 13.2 AI Usage Tracking

```python
class UsageTracker:
    """Tracks all AI model usage for budget management."""
    
    CLAUDE_DAILY_LIMIT = 15     # soft limit — alerts before hitting actual cap
    CLAUDE_WEEKLY_LIMIT = 70
    
    async def track(self, model: str, agent_type: str, tokens_in: int, tokens_out: int, duration_ms: int):
        cost = self._estimate_cost(model, tokens_in, tokens_out)
        
        await self.repo.insert(ModelUsage(
            model=model, agent_type=agent_type,
            tokens_input=tokens_in, tokens_output=tokens_out,
            estimated_cost=cost, duration_ms=duration_ms,
        ))
        
        # Budget alerts
        daily_claude = await self.repo.count_today("claude_code")
        if daily_claude >= self.CLAUDE_DAILY_LIMIT:
            await self._alert("Claude daily limit approaching", daily_claude)
    
    def _estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        rates = {
            "gemini_flash":  {"input": 0.10 / 1_000_000, "output": 0.40 / 1_000_000},
            "gemini_pro":    {"input": 1.25 / 1_000_000, "output": 5.00 / 1_000_000},
            "gemini_vision": {"input": 0.10 / 1_000_000, "output": 0.40 / 1_000_000},
            "claude_code":   {"input": 0, "output": 0},  # covered by Max 5x subscription
        }
        r = rates.get(model, {"input": 0, "output": 0})
        return tokens_in * r["input"] + tokens_out * r["output"]
```

### 13.3 Structured Logging

```python
# All services use structured JSON logging

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

# Usage:
log.info("agent_completed", agent="briefing", model="gemini_pro", duration_ms=3200, tokens=1450)
log.error("agent_failed", agent="email_classifier", error="timeout", retries=3)
log.info("approval_decided", approval_id="uuid", decision="approved", source="ios", latency_ms=800)
```

### 13.4 Alerting Rules

| Condition | Severity | Channel |
|-----------|----------|---------|
| Any service unreachable | Critical | Push notification (iOS + Watch) |
| PostgreSQL connection failure | Critical | Push + Telegram |
| Redis connection failure | Critical | Push + Telegram |
| Claude daily limit reached | Warning | Telegram |
| Gemini API error rate >10% | Warning | Telegram |
| Disk usage >85% | Warning | Telegram |
| Memory usage >90% | Warning | Push |
| Morning briefing failed to generate | Critical | Push |
| AtlasDesk service down | Critical | Push + Telegram |
| Backup failed | Warning | Telegram |
| SSL cert expiring <14 days | Warning | Telegram |
| Approval expired (no response) | Info | Telegram |

### 13.5 Notification Service (Apprise)

T.A.R.S. uses [Apprise](https://github.com/caronc/apprise) as a unified notification
fan-out layer for alerts and informational broadcasts. Apprise handles the "send to all
channels" pattern, while custom APNs and Telegram code handle interactive features.

```python
import apprise

class NotificationService:
    """Unified notification dispatch via Apprise.
    
    Used for: system alerts, budget warnings, briefing-ready notifications,
    backup status, informational broadcasts.
    
    NOT used for: interactive approvals (APNs with action buttons),
    Telegram inline keyboards, or rich card messages.
    """
    
    def __init__(self, config: dict):
        self.apobj = apprise.Apprise()
        
        # Add notification channels with tags
        # Telegram (always-on fallback)
        self.apobj.add(f"tgram://{config['telegram_bot_token']}/{config['telegram_chat_id']}", tag="telegram")
        
        # Email (for critical alerts)
        self.apobj.add(f"mailto://{config['alert_email']}", tag="email")
        
        # Future channels added here as single lines:
        # self.apobj.add("discord://webhook_id/webhook_token", tag="discord")
        # self.apobj.add("slack://token_a/token_b/token_c/#alerts", tag="slack")
    
    async def alert(self, title: str, body: str, severity: str = "info", tags: list[str] = None):
        """Send alert to appropriate channels based on severity."""
        notify_type = {
            "critical": apprise.NotifyType.FAILURE,
            "warning":  apprise.NotifyType.WARNING,
            "info":     apprise.NotifyType.INFO,
        }.get(severity, apprise.NotifyType.INFO)
        
        # Critical alerts go everywhere; info only to Telegram
        target_tags = tags or {
            "critical": ["telegram", "email"],
            "warning":  ["telegram"],
            "info":     ["telegram"],
        }.get(severity, ["telegram"])
        
        self.apobj.notify(
            title=f"[T.A.R.S.] {title}",
            body=body,
            notify_type=notify_type,
            tag=target_tags,
        )
```

**Channel routing by severity:**

| Severity | Telegram | Email | APNs Push (custom) |
|----------|----------|-------|--------------------|
| Critical | ✅ (Apprise) | ✅ (Apprise) | ✅ (custom — actionable buttons) |
| Warning | ✅ (Apprise) | — | ✅ (custom — informational) |
| Info | ✅ (Apprise) | — | — |

**Why the split:** Apprise handles broadcast alerts (same message to multiple channels).
Custom APNs code handles rich interactive push notifications (Approve/Reject buttons,
approval cards, inline actions on Apple Watch). Custom Telegram bot code handles the
full interactive interface (inline keyboards, commands, file sharing).

### 13.5 Daily AI Usage Report

Generated at 11:00 PM daily, included in end-of-day summary:

```json
{
    "date": "2026-03-09",
    "claude": {
        "calls": 12,
        "calls_this_week": 48,
        "weekly_limit_pct": 68,
        "top_agents": [
            {"agent": "communication", "calls": 4},
            {"agent": "coding", "calls": 3},
            {"agent": "research", "calls": 2}
        ]
    },
    "gemini": {
        "flash_calls": 42,
        "pro_calls": 8,
        "vision_calls": 3,
        "total_tokens": 124500,
        "estimated_cost_today": 0.03,
        "estimated_cost_mtd": 0.67
    },
    "local": {
        "calls": 187,
        "note": "Health checks, data fetching, scheduling — zero AI cost"
    }
}
```

---

## Appendix A: Environment Variables Reference

```bash
# .env.example — Template for all required environment variables

# ── Database ──
POSTGRES_PASSWORD=<strong-random-password>

# ── Redis ──
# (No auth for local-only Redis on private network)

# ── ChromaDB ──
CHROMA_AUTH_TOKEN=<random-token>

# ── T.A.R.S. API ──
TARS_API_KEY=<strong-random-api-key>
ALLOWED_DEVICE_TOKENS=<comma-separated-device-tokens>

# ── AI Models ──
GEMINI_API_KEY=<google-ai-studio-key>
# Claude Code uses system-level auth (Max 5x plan)

# ── Telegram ──
TELEGRAM_BOT_TOKEN=<botfather-token>

# ── Gmail (OAuth credentials JSON — base64 encoded) ──
GMAIL_PERSONAL_CREDENTIALS=<base64-encoded-oauth-json>
GMAIL_PROFESSIONAL_CREDENTIALS=<base64-encoded-oauth-json>

# ── iCloud CalDAV ──
ICLOUD_CALDAV_USER=<apple-id-email>
ICLOUD_CALDAV_PASSWORD=<app-specific-password>

# ── GitHub ──
GITHUB_PAT=<personal-access-token>

# ── Notion ──
NOTION_TOKEN=<internal-integration-token>

# ── Plaid ──
PLAID_CLIENT_ID=<plaid-client-id>
PLAID_SECRET=<plaid-secret>
PLAID_ACCESS_TOKEN=<plaid-access-token-after-link>
PLAID_ENV=sandbox  # sandbox | development | production

# ── Weather ──
OPENWEATHERMAP_API_KEY=<api-key>

# ── Picovoice / Porcupine ──
PICOVOICE_ACCESS_KEY=<access-key>

# ── Apple Push Notifications ──
APNS_KEY_ID=<key-id>
APNS_TEAM_ID=<team-id>
# APNS key file mounted as Docker secret at /secrets/apns-key.p8

# ── Cloudflare ──
CLOUDFLARE_TUNNEL_TOKEN=<tunnel-token>

# ── Grafana / Loki ──
GRAFANA_URL=<grafana-base-url-via-tunnel>
GRAFANA_API_KEY=<grafana-api-key>
LOKI_URL=<loki-base-url-via-tunnel>

# ── SerpAPI (optional — for job search) ──
SERPAPI_KEY=<serpapi-key>

# ── Brave Search (for Claude Code MCP server) ──
BRAVE_API_KEY=<brave-search-api-key>
```

---

## Appendix B: CLAUDE.md Template

This file lives at the repo root and provides Claude Code with project context:

```markdown
# CLAUDE.md — T.A.R.S. Project Context

## What is T.A.R.S.?
T.A.R.S. (Tasin's Autonomous Resource System) is a personal AI assistant platform.
It runs on two HP Z2 Mini G3 servers (Node 1: Brain, Node 2: Muscle) and is accessed
via a custom iOS app, Telegram bot, Apple Watch, and "Hey TARS" wake word.

## Architecture
- **Node 1 (Brain)**: Python 3.12, FastAPI, PostgreSQL 16, orchestrator, all integrations
- **Node 2 (Muscle)**: Redis 7, ChromaDB, Docker sandbox execution, job worker
- **iOS App**: Swift 5.10+, SwiftUI, MVVM, EventKit/HealthKit/Contacts
- **AI Models**: Claude Code (complex, with MCP servers), Gemini Flash/Pro/Vision (routine), Local (deterministic)

## Key Files
- `backend/src/main.py` — Backend entrypoint
- `backend/src/orchestrator/engine.py` — Central orchestration loop
- `backend/src/agents/` — All agent implementations
- `backend/src/integrations/` — External service adapters
- `backend/src/db/models.py` — SQLAlchemy ORM models
- `deploy/node1/docker-compose.yml` — Node 1 deployment
- `deploy/node2/docker-compose.yml` — Node 2 deployment

## Hard Constraints (NEVER violate)
- HC-01: No outbound communication without explicit user approval
- HC-02: No code pushed to production without approval
- HC-03: No data deletion without confirmation
- HC-04: No financial transactions — read-only Plaid access only
- HC-05: No plaintext credentials
- HC-08: All actions logged and auditable

## Database
PostgreSQL 16 on Node 1. Schema in `backend/alembic/versions/`.
Key tables: conversations, messages, agent_tasks, approvals, email_classifications,
briefings, config, contacts, job_listings, wardrobe_items, model_usage, transactions,
health_data, audit_log.

## Deployment
Mac → GitHub → GitHub Actions → GHCR → Servers pull + docker compose up.
No dev tools on servers — Docker only.

## Testing
Run: `cd backend && python -m pytest tests/`
```

---

*This Design Document is the authoritative technical blueprint for T.A.R.S. v1.0. Combined with the Requirements Document v2.1, it provides Claude Code with comprehensive specifications for implementation. All design decisions trace to requirements defined in the Requirements Document.*
