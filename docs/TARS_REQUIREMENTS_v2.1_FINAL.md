# Project Requirements Document
## T.A.R.S. — Tasin's Autonomous Resource System

**Version:** 2.1 (FINAL — LOCKED)
**Author:** Tasin (KM Khalid Saifullah) & Claude
**Date:** March 9, 2026
**Status:** LOCKED — Approved for Design Phase. No further requirements changes until Phase 1 exit criteria met.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Vision & Success Criteria](#2-vision--success-criteria)
3. [User Profile & Context](#3-user-profile--context)
4. [Infrastructure & Hardware](#4-infrastructure--hardware)
5. [Multi-Model AI Architecture](#5-multi-model-ai-architecture)
6. [Client Interfaces](#6-client-interfaces)
7. [Core Agent System](#7-core-agent-system)
8. [Integration Requirements](#8-integration-requirements)
9. [Autonomy & Approval Model](#9-autonomy--approval-model)
10. [Data Architecture & Memory](#10-data-architecture--memory)
11. [Networking & Security](#11-networking--security)
12. [Non-Functional Requirements](#12-non-functional-requirements)
13. [Hard Constraints & Dealbreakers](#13-hard-constraints--dealbreakers)
14. [Scope Boundaries](#14-scope-boundaries)
15. [Phased Delivery Plan](#15-phased-delivery-plan)
16. [Ideas Backlog (Future Features)](#16-ideas-backlog-future-features)
17. [Open Questions & Risks](#17-open-questions--risks)
18. [Glossary](#18-glossary)

---

## 1. Executive Summary

### 1.1 What We Are Building

**T.A.R.S.** — **T**asin's **A**utonomous **R**esource **S**ystem — is a personal AI assistant platform inspired by J.A.R.V.I.S. from Iron Man. T.A.R.S. operates as an always-on, intelligent life management system that wakes before the user, organizes the day, eliminates noise, and handles tasks autonomously — with explicit user approval for all outbound actions.

The system runs on two dedicated HP Z2 Mini G3 workstations at the user's home, employs a **multi-model AI architecture** (Claude Code for complex reasoning, Google Gemini for high-volume routine tasks and vision capabilities), and is accessed primarily through a custom-built iOS application with a Telegram bot as a fallback interface.

T.A.R.S. handles daily life management, job searching, proactive monitoring and alerting, communication drafting, fashion/outfit advice, product research, finance tracking, health/fitness monitoring, coding/DevOps automation, and research assistance. At home, the user interacts with T.A.R.S. via a custom wake word ("Hey TARS") through a dedicated USB microphone on the server, with audio output through a HomePod Mini — creating a true ambient AI presence in the room.

### 1.2 Why This Exists

The core problem is **cognitive overhead**. Every day requires manually checking calendars, triaging emails across multiple accounts, monitoring server infrastructure, tracking deadlines, searching for jobs, organizing priorities, and making dozens of small decisions. T.A.R.S. eliminates that overhead by acting as an intelligent second brain that:

- Aggregates information from all sources into a single, prioritized stream
- Filters noise (promotional emails, low-priority notifications) automatically
- Proactively alerts on things that matter (deadlines, system issues, schedule conflicts)
- Actively searches for job opportunities matching the user's profile
- Executes tasks on the user's behalf with explicit approval
- Sees and understands visual context (wardrobe, receipts, screenshots) via Gemini vision
- Tracks health, fitness, and sleep data to inform daily recommendations
- Passively monitors spending via read-only bank access for financial awareness
- Learns and adapts to evolving priorities over time

### 1.3 Core Design Philosophy

- **Multi-model intelligence**: Claude handles complex reasoning, strategy, and nuanced communication. Gemini handles high-volume routine tasks, classification, and vision. The right model for the right job — maximizing capability while minimizing cost.
- **Intelligence on demand**: AI models are invoked only when reasoning is needed. All deterministic operations (API calls, data fetching, scheduling) run locally on the Z2 Minis without consuming AI tokens.
- **Client-agnostic backend**: The orchestration layer exposes a REST API + WebSocket interface. Any client (iOS app, Telegram bot, Apple Watch, HomePod via Siri, future web dashboard) connects to the same backend.
- **Ambient presence**: T.A.R.S. exists in the user's physical space (HomePod audio + USB mic wake word), on the wrist (Apple Watch), and in the pocket (iPhone) — not just as an app but as an environmental intelligence.
- **Single user, maximum depth**: This is not a multi-tenant SaaS. It is a deeply personalized system for one person, allowing aggressive optimization for the user's specific workflows, preferences, and schedule.
- **Progressive autonomy**: The system starts conservative (approval for everything) and, as trust is established, can be configured to act autonomously on low-risk operations.
- **Dev → Ship → Deploy**: All development on Mac, pushed to GitHub, built as Docker images via GitHub Container Registry, pulled and deployed on the Z2 Mini servers. The servers are production-only — no development tooling installed.

### 1.4 The Name

**T.A.R.S.** — **T**asin's **A**utonomous **R**esource **S**ystem

Inspired by TARS from Christopher Nolan's *Interstellar* — loyal, capable, intelligent, operates autonomously in extreme environments, and always has your back. The name also carries the user's initial, making it deeply personal.

---

## 2. Vision & Success Criteria

### 2.1 The Vision (3-Month Target)

> "I wake up to T.A.R.S.'s alarm. When I dismiss it, T.A.R.S. starts talking — telling me my schedule for the day, when I need to leave for the office, today's appointments, when gym is, whether I need groceries, what's happening in my email. It tells me if I need an umbrella, or if it's a sunny weekend and I should go get a car wash. It says 'Hey Tasin, today is 18°C and you have an appointment in Cleveland — you should wear light pants with your white v-neck and pink shirt.' By the time I'm out of bed, my entire day is organized. I never waste time digging through apps. The noise is removed and my productivity is at 100%."

> "I say 'Hey TARS, find me a good quality minoxidil' and it searches the web, compares options, and presents the best choices so I can just order."

> "Every day, T.A.R.S. has already scanned job boards for roles that match my profile. I get a digest of the best matches with one-tap save or apply actions."

### 2.2 Measurable Success Criteria

| Criteria | Target | Measurement |
|----------|--------|-------------|
| Morning briefing delivered before wake-up | 100% of days | Briefing arrives by 5:55 AM |
| Zero missed calendar events or deadlines | 0 misses in 90 days | Compared to actual calendar |
| Email triage accuracy | >90% classification accuracy | User feedback on misclassifications |
| System health alert response time | <5 minutes from incident | Time between error and notification |
| Daily time saved on organization | >45 minutes/day | User self-assessment |
| Assistant uptime | >99% during waking hours (6 AM–11 PM) | Monitoring logs |
| Approval-to-action latency | <30 seconds after user approves | Timestamp delta |
| Job match relevance | >70% of surfaced jobs are genuinely interesting | User feedback (save/skip ratio) |
| Outfit suggestion acceptance rate | >50% of suggestions worn | User feedback |
| Claude token efficiency | <40% of total AI calls use Claude | Model usage logs |

### 2.3 User Stories

**US-01 (Alarm & Briefing)**: As the user, I want to wake up to an intelligent alarm that adjusts based on my first calendar event, commute, and weather. When I dismiss it, T.A.R.S. speaks my daily briefing — schedule, weather, outfit suggestion, emails, tasks — so I absorb my plan while getting ready.

**US-02 (Email Triage)**: As the user, I want promotional and low-priority emails filtered automatically across both Gmail accounts, with only urgent and actionable items surfaced, so I don't waste time on noise.

**US-03 (Natural Language Scheduling)**: As the user, I want to say "schedule gym on Thursday at 6pm" and have T.A.R.S. create the calendar event and confirm, so I manage my schedule hands-free.

**US-04 (System Monitoring)**: As the user, I want T.A.R.S. to monitor my AtlasDesk infrastructure and alert me immediately if any service goes down, with a diagnostic report and recommended fix, so I respond before users are impacted.

**US-05 (Job Search)**: As the user, I want T.A.R.S. to scan job boards daily for roles matching my profile (software engineering, AI/ML research, PhD positions), present a ranked digest, and help me prepare applications, so my job search runs in the background without consuming my active time.

**US-06 (Fashion Advisor)**: As the user, I want T.A.R.S. to suggest outfits based on weather, my calendar (formal vs. casual), and what's in my wardrobe, so I look appropriate without spending time deciding.

**US-07 (Product Research)**: As the user, I want to ask T.A.R.S. to find and compare products, so I make purchase decisions quickly without manual research.

**US-08 (Communication Drafting)**: As the user, I want all draft emails and outbound communications reviewed by me before sending, so I maintain full control over professional communications.

**US-09 (Learning & Adaptation)**: As the user, I want T.A.R.S. to learn my priorities over time via feedback, so its recommendations and classifications improve continuously.

**US-10 (End-of-Day Summary)**: As the user, I want an end-of-day summary of what was accomplished, what's pending, what's coming tomorrow, and a suggested wake-up time, so I can mentally close out the workday.

**US-11 (Shopping Advisor)**: As the user, I want to say "I'm thinking of shopping at Tanger Outlets, what should I buy under $100?" and get recommendations based on my wardrobe gaps, current trends, and what stores are there.

**US-12 (Configuration)**: As the user, I want to configure and adjust T.A.R.S.'s behavior (briefing priorities, quiet hours, notification preferences, job search criteria) via natural language commands.

---

## 3. User Profile & Context

### 3.1 About the User

- **Name**: Tasin (KM Khalid Saifullah)
- **Role**: IT/Technology Services at The College of Wooster; Founder & Lead Software Engineer of AtlasDesk (enterprise ITSM SaaS platform)
- **Education**: B.A. Computer Science (Honors) & Business Economics, College of Wooster (May 2025)
- **Current Focus**: AtlasDesk development (March 15, 2026 beta deadline), PhD applications for Fall 2026 (multi-agent systems, AI coordination), active job search
- **Technical Proficiency**: Full-stack engineer, comfortable with systems programming, Linux, Docker, Kubernetes, NestJS, Next.js, PostgreSQL, Python, Swift (learning)
- **Research Interests**: Multi-agent systems, autonomous vehicle coordination, routing loops, Object Memory Management (OMM)

### 3.2 Daily Schedule

| Time | Activity |
|------|----------|
| 5:50 AM | T.A.R.S. prepares morning briefing |
| 5:55 AM | Briefing ready, alarm set |
| 6:00 AM | Wake up — alarm + voice briefing + outfit suggestion |
| 6:00–7:45 AM | Morning routine (briefing consumed, quick task approvals) |
| 8:00 AM–5:00 PM | Work hours (Wooster IT + AtlasDesk development) |
| 5:00–6:00 PM | Transition / gym |
| 6:00–11:00 PM | Personal time / AtlasDesk / research / PhD prep |
| 10:30 PM | End-of-day summary |
| 11:00 PM | Wind down — T.A.R.S. sets next-day alarm |

### 3.3 Devices

| Device | Role | Interaction Type |
|--------|------|-----------------|
| iPhone | Primary interface | iOS app (full interaction, voice, approvals, camera for wardrobe) |
| Apple Watch | Notifications + quick approvals | Push notifications, inline approve/reject, glance at schedule |
| HomePod Mini | Voice-in-the-room | Morning briefing audio output, Siri Shortcuts bridge, ambient audio |
| USB Conference Mic (on Node 1) | Wake word detection | Always-on "Hey TARS" listener via Porcupine, far-field voice capture |
| Z2 Mini Node 1 | Brain server | No direct interaction (headless) |
| Z2 Mini Node 2 | Execution server | No direct interaction (headless) |

---

## 4. Infrastructure & Hardware

### 4.1 Hardware Specifications (Per Node — Verified)

| Component | Verified Spec | Recommended Upgrade |
|-----------|--------------|-------------------|
| Model | HP Z2 Mini G3 (Z2D60UT#ABA) | — |
| Processor | Intel Core i7-6700 (4C/8T, 3.4–4.0 GHz, 8MB Cache) | No change needed |
| RAM | **16 GB DDR4-2400** (verified) | Upgrade to 32 GB (2×16GB SODIMM) when budget allows |
| Storage | **238.5 GB NVMe SSD** (verified) | Add external NVMe via USB 3.1-C for Node 2 bulk storage |
| GPU | NVIDIA Quadro M620 (2 GB GDDR5) | No change needed (not used for LLM inference) |
| Networking | Gigabit Ethernet RJ-45 | No change needed |
| Power | 280W PSU, 85% efficiency | No change needed |
| Ports | 4× USB 3.0, 2× USB 3.1 Type-C, 3× DisplayPort 1.2 | — |

**Hardware Action Items:**
- [x] Verify actual RAM: **16 GB confirmed**
- [x] Verify storage: **238.5 GB NVMe confirmed**
- [ ] Check with Wooster hardware technician for spare RAM/SSD (scheduled next week)
- [ ] Purchase 2× 32GB DDR4 SODIMM kits if needed and no spares available (~$40–50/machine)
- [ ] Consider USB 3.1-C external NVMe for Node 2 bulk storage (Docker images, repos, logs)
- [ ] Purchase HomePod Mini ($99 — covered by existing Apple gift card)
- [ ] Purchase USB conference speakerphone/mic for Node 1 wake word detection (~$20–30 used, e.g., Jabra Speak 410)
- [ ] Purchase Apple Developer Account ($99/year)

### 4.2 Node Roles

**Node 1 — "Brain" (Orchestration Server)**
- Operating System: Ubuntu Server 24.04 LTS
- Responsibilities:
  - T.A.R.S. Orchestrator daemon (master process)
  - iOS app backend API (REST + WebSocket)
  - Telegram bot gateway
  - **Wake word detection ("Hey TARS") via USB mic + Porcupine**
  - **Speech-to-text processing for voice commands received via wake word**
  - Task router / intent classifier
  - Claude Code spawner (headless subprocess)
  - Gemini API client
  - State database (PostgreSQL)
  - Scheduler daemon (cron + dynamic scheduling)
  - Approval queue management
  - Integration layer (all external API connections)
- Always-on services: API server, WebSocket server, Telegram long-polling, cron scheduler, health monitor, **Porcupine wake word listener**

**Development & Deployment Workflow:**
```
Mac (development) → GitHub → GitHub Actions → GitHub Container Registry (ghcr.io)
                                                        ↓
                              Node 1 & Node 2 pull Docker images → docker compose up
```
- All code developed and tested on Mac
- Pushed to GitHub, CI/CD builds Docker images
- Servers pull images and restart via `docker compose pull && docker compose up -d`
- Self-deploy command: user sends `/deploy` via Telegram or iOS app → T.A.R.S. pulls latest images and restarts itself
- Servers have NO development tools — only Docker, Docker Compose, and persistent data volumes

**Node 2 — "Muscle" (Execution Server)**
- Operating System: Ubuntu Server 24.04 LTS
- Responsibilities:
  - Docker engine for sandboxed agent execution (code tasks, research)
  - Redis (job queue + pub/sub between nodes)
  - ChromaDB (semantic memory / vector store)
  - Code execution sandbox (isolated Docker containers for coding agents)
  - Bulk storage for agent outputs, logs, repo clones, wardrobe image catalog
  - Monitoring cron jobs
- Always-on services: Docker daemon, Redis, ChromaDB

### 4.3 Inter-Node Communication

- **Physical**: Gigabit Ethernet (direct cable or through home router/switch)
- **Network**: Private subnet with static IPs (e.g., 10.0.1.1 for Node 1, 10.0.1.2 for Node 2)
- **Authentication**: SSH key-based auth between nodes (no passwords)
- **Job Dispatch**: Node 1 → Redis queue (Bull/BullMQ) → Node 2 picks up jobs, executes in Docker, returns results via Redis pub/sub
- **Overlay Network**: Tailscale mesh VPN across both nodes + user's iPhone for remote access anywhere

---

## 5. Multi-Model AI Architecture

### 5.1 Design Rationale

Rather than routing all AI tasks through a single model (burning expensive Claude tokens on routine work), T.A.R.S. employs a **multi-model strategy** — the right model for the right job. This maximizes capability while minimizing cost, extending the Claude Max 5x budget by an estimated 3–4×.

### 5.2 Model Roles

#### Claude Code (via Max 5x Plan — $100/month)
**Role**: The Strategist — handles tasks requiring complex reasoning, nuanced judgment, and sophisticated language generation.

| Use Case | Why Claude |
|----------|-----------|
| Communication drafting (emails to professors, advisors, recruiters) | Nuanced tone, context awareness, high-stakes writing |
| System diagnostics (analyzing Loki logs, proposing fixes) | Complex multi-step reasoning |
| Code generation and architecture decisions | Deep technical reasoning, codebase understanding |
| Job application materials (cover letters, resume tailoring) | Persuasive, personalized writing |
| Decision advising (complex tradeoffs) | Structured analytical reasoning |
| Interview preparation | Complex scenario generation |
| Research synthesis (papers, technical analysis) | Deep comprehension and cross-referencing |
| Negotiation strategy (job offers) | Strategic communication |

**Invocation**: Spawned as headless subprocess via `claude` CLI with scoped prompts. One process per task, stateless.

**Estimated daily usage**: 10–15 calls (down from 30–50 without Gemini offloading)

#### Gemini Flash / Pro (via Google AI API — pay-per-token)
**Role**: The Workhorse — handles high-volume routine tasks that need intelligence but not Claude-level reasoning.

| Use Case | Why Gemini | Model Tier |
|----------|-----------|-----------|
| Email classification (urgent/actionable/noise) | High volume, simple classification | Flash |
| Job listing initial screening (keyword + criteria matching) | High volume, pattern matching | Flash |
| Morning briefing composition (from pre-structured data) | Template-driven summarization | Flash |
| Simple scheduling logic ("find free slots") | Structured data processing | Flash |
| Basic Q&A and information retrieval | Fast, cheap responses | Flash |
| Job listing detailed evaluation (fit scoring) | Moderate reasoning needed | Pro |
| Meeting prep summaries | Moderate summarization | Pro |
| Product research synthesis | Web search + comparison | Pro |
| Shopping recommendations | Multi-factor analysis | Pro |

**Estimated daily usage**: 30–60 calls (absorbs the bulk of routine intelligence work)

**Cost estimate**: Gemini 2.0 Flash at ~$0.10/1M input tokens → even heavy daily usage costs pennies

#### Gemini with Vision (Nano/Flash with image input)
**Role**: The Eyes — handles all visual understanding tasks.

| Use Case | Description |
|----------|------------|
| Wardrobe cataloging | User photographs clothing items; Gemini catalogs with metadata (color, type, season, formality, brand) |
| Daily outfit suggestion | Cross-references wardrobe catalog + weather + calendar event types to recommend outfits |
| Receipt/document reading | Extract data from photographed receipts, bills, documents |
| Fridge inventory | User snaps a photo of fridge contents; Gemini catalogs for meal planning |
| Visual context | Analyze screenshots, photos, or visual information the user shares |
| Shopping advisor | "What should I buy at Tanger Outlets?" — analyzes user's wardrobe gaps + store inventory |

### 5.3 Model Router Logic

The orchestrator on Node 1 includes a **Model Router** that decides which model handles each task:

```
User Request
    ↓
Intent Classifier (local rule-based + lightweight)
    ↓
┌─────────────────────────────────────────────────┐
│ Model Router Decision Matrix                     │
│                                                  │
│ IF task involves:                                │
│   complex reasoning, code, diagnostics,          │
│   high-stakes communication, strategy            │
│   → Route to Claude Code                         │
│                                                  │
│ IF task involves:                                │
│   classification, filtering, summarization,      │
│   simple Q&A, scheduling, routine analysis       │
│   → Route to Gemini Flash/Pro                    │
│                                                  │
│ IF task involves:                                │
│   image analysis, visual understanding,          │
│   wardrobe, receipts, photos                     │
│   → Route to Gemini Vision                       │
│                                                  │
│ IF task is deterministic:                        │
│   API calls, data fetching, cron jobs            │
│   → Handle locally (no AI tokens)                │
└─────────────────────────────────────────────────┘
```

### 5.4 Token Budget Management

| Resource | Budget | Strategy |
|----------|--------|----------|
| Claude Max 5x | $100/month (shared with claude.ai + Claude Code) | Reserve for high-complexity tasks only. Target: <40% of total AI calls. |
| Gemini API | Pay-per-token (very low cost) | Absorb 60–70% of routine AI workload. Monitor monthly spend. |
| Local processing | Free (runs on Z2 Minis) | Handle all deterministic work locally. |

**Monitoring**: T.A.R.S. tracks its own AI usage — daily and weekly reports on Claude vs. Gemini call counts, token usage, and cost estimates. Alerts if approaching Claude weekly limits.

---

## 6. Client Interfaces

### 6.1 Primary: Custom iOS Application — "T.A.R.S."

**Platform**: iOS 17+ (SwiftUI)
**Distribution**: TestFlight via Apple Developer Account ($99/year)
**Architecture**: Thin client — all intelligence lives on the backend. The iOS app is a UI + notification + audio + camera layer.

#### 6.1.1 Core iOS App Features

**Alarm & Wake System**
- Smart alarm adjusts wake time based on first calendar event + commute time + weather conditions
- When dismissed, triggers voice briefing using iOS AVSpeechSynthesizer (initially; upgradeable to ElevenLabs custom voice)
- Alarm configuration managed by T.A.R.S. (user can override anytime)
- "Hey Tasin, good morning. It's 6 AM, 18°C outside, partly cloudy. You have a meeting at 9, so leave by 8:15..."

**Voice Interaction**
- Speech-to-text for hands-free commands (iOS Speech framework)
- Text-to-speech for T.A.R.S. responses and briefings
- Push-to-talk button for voice input
- **At home**: Custom wake word "Hey TARS" via USB mic on Node 1 (Porcupine by Picovoice) — no Siri needed
- **On mobile**: Siri Shortcuts bridge — "Hey Siri, TARS [command]"

**HomePod Mini Integration**
- Primary audio output for morning briefing — T.A.R.S. speaks through the room via AirPlay
- Siri Shortcuts bridge: "Hey Siri, ask TARS for my briefing" → calls T.A.R.S. API → speaks result
- Ambient voice commands throughout the room (via Siri bridge)
- Combined with USB mic on Node 1, creates a complete room-based voice interface
- Future: intercom capability with additional HomePods in other rooms

**Conversational Interface**
- Chat-style message thread with T.A.R.S.
- Rich message types:
  - Text messages
  - Cards (email summaries, job listings, product comparisons, outfit suggestions)
  - Action buttons (Approve / Reject / Edit / Save / Skip)
  - Image messages (outfit photos, product images, wardrobe suggestions)
- Inline approval flow for all outbound actions

**Camera Integration**
- Photograph wardrobe items for cataloging
- Snap fridge contents for meal planning
- Capture receipts/documents for data extraction
- All images processed via Gemini Vision

**Push Notifications**
- Actionable notifications with approve/reject buttons
- Priority levels:
  - **Critical**: System down, urgent email, deadline today
  - **Normal**: Task reminders, briefing ready, job matches found
  - **Low**: Informational updates, suggestions
- Apple Watch mirror with inline actions

**Home Screen Widgets**
- Today's schedule at a glance
- Pending approvals count
- AtlasDesk system health (green/yellow/red)
- Next upcoming event
- Weather + outfit suggestion

**Siri Shortcuts Integration**
- "Hey Siri, ask TARS to schedule gym tomorrow"
- "Hey Siri, what's my day look like?"
- Bridges Siri commands into T.A.R.S. agent pipeline

#### 6.1.2 Apple Watch Companion App

- Complication: next event + pending approvals count
- Notification mirroring with inline approve/reject
- Glance view: today's schedule, system health status
- Quick voice command input

### 6.2 Fallback: Telegram Bot — "@TarsBot"

**Purpose**: Available immediately while iOS app is in development. Permanent fallback accessible from any device.

**Features**:
- Text and voice message input (voice transcribed server-side)
- Inline keyboard buttons for approvals
- File/image sharing (photos for wardrobe, product results, reports)
- Command shortcuts: `/briefing`, `/schedule`, `/status`, `/jobs`, `/config`, `/outfit`
- Full feature parity with iOS app (minus alarm, camera, widgets)

### 6.3 Backend API (Client-Agnostic)

**Protocol**: REST API + WebSocket (real-time updates)
**Authentication**: API key + device token (single user)
**Core Endpoints** (detailed in Design Doc):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/message` | Send a message/command to T.A.R.S. |
| GET | `/briefing` | Fetch current day's briefing |
| GET | `/schedule` | Fetch today's schedule |
| GET | `/approvals` | List pending approval items |
| POST | `/approvals/:id/approve` | Approve a pending action |
| POST | `/approvals/:id/reject` | Reject a pending action |
| POST | `/approvals/:id/edit` | Edit and approve a pending action |
| GET | `/health` | System health (T.A.R.S. + AtlasDesk) |
| GET | `/jobs` | Fetch latest job matches |
| POST | `/wardrobe/upload` | Upload wardrobe photo for cataloging |
| GET | `/outfit` | Get today's outfit suggestion |
| WS | `/stream` | Real-time updates (notifications, agent status) |

---

## 7. Core Agent System

### 7.1 Architecture Overview

T.A.R.S. follows a **master-worker** pattern with multi-model routing:

```
User Input (iOS App / Telegram / Siri / Cron Trigger)
    ↓
┌─── Orchestrator (Python asyncio, always-on, Node 1) ───┐
│                                                          │
│   Intent Classifier (rule-based + lightweight)           │
│       ↓                                                  │
│   Model Router → Claude Code (complex tasks)             │
│               → Gemini Flash/Pro (routine tasks)         │
│               → Gemini Vision (visual tasks)             │
│               → Local handler (deterministic tasks)      │
│       ↓                                                  │
│   Approval Queue (if action has external side effects)   │
│       ↓                                                  │
│   Execution Engine → Direct (Node 1)                     │
│                    → Docker dispatch (Node 2 via Redis)  │
│       ↓                                                  │
│   Response Formatter → iOS App / Telegram / Watch        │
└──────────────────────────────────────────────────────────┘
```

- **Orchestrator**: Always-running master daemon on Node 1. Routes tasks, manages state, enforces approval policies.
- **Claude Code Agents**: Spawned as headless subprocesses. Stateless workers — receive context, return output, terminate.
- **Gemini Agents**: Called via REST API. Fast, cheap, high-volume.
- **Local Workers**: Deterministic tasks (API calls, data fetching, cron jobs). Zero AI token cost.

### 7.2 Agent Catalog

#### 7.2.1 Morning Briefing Agent

**Trigger**: Cron at 5:50 AM daily (configurable)
**AI Model**: Gemini Pro (composition) — Claude only if complex prioritization needed
**Process**:
1. Local workers fetch all data in parallel (zero AI tokens):
   - iCloud Calendar events for today and tomorrow (CalDAV)
   - Unread emails from both Gmail accounts (last 12 hours)
   - Weather forecast (Wooster, Ohio — OpenWeatherMap API)
   - AtlasDesk system health (Grafana API)
   - Pending tasks (Notion API)
   - GitHub notifications
   - New job matches from overnight scan
   - Wardrobe catalog + weather data (for outfit suggestion)
   - **HealthKit data via iOS app sync: last night's sleep duration/quality, yesterday's steps, recent workout history**
   - **Recent transactions summary from Plaid (last 24 hours spending)**
2. All raw data compiled into structured payload
3. Gemini Vision generates outfit suggestion from wardrobe catalog + weather + calendar
4. Gemini Pro composes the briefing narrative from structured data
5. Briefing stored in state DB + pushed to iOS app
6. When user dismisses alarm at 6:00 AM, app reads briefing via TTS

**Output Structure**:
```json
{
  "greeting": "Good morning, Tasin.",
  "weather": {
    "summary": "18°C, partly cloudy, no rain expected.",
    "needs_umbrella": false,
    "suggestion": "Great weather — sunny weekend, good day for a car wash."
  },
  "outfit": {
    "suggestion": "Light pants with your white v-neck and pink shirt — appropriate for your Cleveland appointment.",
    "reasoning": "18°C, semi-formal meeting, light colors for warm weather.",
    "image_refs": ["wardrobe_item_023", "wardrobe_item_011", "wardrobe_item_045"]
  },
  "schedule": [
    {"time": "8:00 AM", "event": "Stand-up with IT team", "location": "Office"},
    {"time": "11:00 AM", "event": "Cleveland appointment", "location": "Cleveland, OH"},
    {"time": "6:00 PM", "event": "Gym", "location": "Rec Center"}
  ],
  "leave_home_by": "7:35 AM",
  "commute_note": "Allow extra time — Cleveland appointment requires 1.5hr drive.",
  "email_digest": {
    "urgent": [{"from": "Prof. Sadigh", "subject": "...", "summary": "..."}],
    "actionable": [{"from": "GitHub", "subject": "...", "summary": "..."}],
    "informational_count": 12,
    "noise_filtered_count": 34
  },
  "system_health": {"status": "green", "details": "All services operational."},
  "tasks_due_today": ["Review PR #47", "Submit Berkeley SOP draft"],
  "job_matches": {"new_today": 3, "top_match": "ML Engineer @ Scale AI — 92% match"},
  "health": {
    "sleep": "7.2 hours (good — above your 7hr target)",
    "steps_yesterday": 8420,
    "last_workout": "2 days ago — consider gym today",
    "note": "You slept well. Good energy day."
  },
  "finance": {
    "yesterday_spending": "$47.23 (2 transactions)",
    "month_to_date": "$1,247.00",
    "note": "Dining out is up 30% vs last month."
  },
  "proactive_suggestions": ["Sunny weekend — good day for car wash.", "Grocery list has 4 items — Walmart is on your route home.", "You haven't been to gym in 2 days — free at 6pm today."]
}
```

**Configurability**: User adjusts via natural language ("Move jobs to top of my briefing", "Disable outfit suggestions"):

```yaml
morning_briefing:
  time: "05:50"
  alarm_offset_minutes: 10  # alarm rings 10 min after briefing is ready
  voice_enabled: true
  sections:
    - name: weather
      priority: 1
      enabled: true
      include_outfit: true
    - name: schedule
      priority: 2
      enabled: true
      include_commute: true
      include_leave_time: true
    - name: email_digest
      priority: 3
      enabled: true
      filter: "urgent+actionable"
    - name: tasks_due
      priority: 4
      enabled: true
    - name: job_matches
      priority: 5
      enabled: true
      show_top_n: 3
    - name: system_health
      priority: 6
      enabled: true
    - name: github_notifications
      priority: 7
      enabled: true
    - name: health_summary
      priority: 8
      enabled: true
      include_sleep: true
      include_steps: true
      include_workout_reminder: true
    - name: finance_summary
      priority: 9
      enabled: true
      show_yesterday: true
      show_mtd: true
    - name: proactive_suggestions
      priority: 10
      enabled: true
```

#### 7.2.2 Email Classifier Agent

**Trigger**: Polling every 5 minutes (or Gmail push notification webhook)
**AI Model**: Gemini Flash (primary classification), Claude (if user disputes a classification)
**Scope**: 2 Gmail accounts — personal + PhD/professional
**Classification Tiers**:

| Tier | Definition | Action |
|------|-----------|--------|
| **Urgent** | Requires immediate attention. Professors, advisors, critical contacts. Time-sensitive. | Push notification immediately |
| **Actionable** | Needs response or action within 24 hours. Meeting invites, PR reviews, professional requests, job-related emails. | Included in next briefing or digest |
| **Informational** | FYI only. Newsletters the user reads, order confirmations, receipts. | Available on request, not pushed |
| **Noise** | Promotional, marketing, automated notifications. | Silently archived, count shown in briefing |

**Learning Loop**:
1. User marks misclassifications via iOS app ("This was urgent" / "This was noise")
2. Feedback stored in `email_classifications` table with correction
3. Future Gemini Flash prompts include the 20 most recent corrections as examples
4. Over weeks, accuracy improves toward the >90% target

**Apple Contacts Enrichment**:
- iOS app syncs the user's Apple Contacts database to the backend via the Contacts framework
- Every incoming email is cross-referenced with Contacts data: name, organization, relationship labels, notes
- This auto-enriches classification: an email from someone labeled "Advisor" in Contacts is automatically prioritized
- Eliminates the need to manually maintain the contact priority list for known contacts
- Manual overrides (below) still apply for domain-level rules and unknown senders

**Contact Priority List** (maintained in config, editable via natural language — supplements Apple Contacts):
```yaml
email_contacts:
  always_urgent:
    - "*@stanford.edu"
    - "sadigh@cs.stanford.edu"
    - "pliang@cs.stanford.edu"
    - "*advisor*"
  always_actionable:
    - "*@github.com"
    - "*@wooster.edu"
  always_noise:
    - "*@marketing.*"
    - "*promo*"
    - "*unsubscribe*"
```

#### 7.2.3 Job Search Agent

**Trigger**: Daily automated scan at 2:00 AM + on-demand
**AI Models**: Gemini Flash (initial screening), Gemini Pro (fit evaluation), Claude (top-match deep review + application materials)

**Job Profile Configuration**:
```yaml
job_search:
  target_roles:
    - "Software Engineer (Full-Stack, Backend)"
    - "AI/ML Research Engineer"
    - "AI/ML Research Scientist"
    - "PhD Research Assistant"
    - "Machine Learning Engineer"
  preferred_locations:
    - "Remote"
    - "Bay Area, CA"
    - "New York, NY"
    - "Boston, MA"
    - "Any major tech hub"
  salary_minimum: null  # to be configured
  company_preferences:
    preferred_size: ["startup", "mid-size", "large-tech"]
    industries: ["AI/ML", "tech", "research", "education"]
    exclude: []  # e.g., defense contractors
  skills_highlight:
    - "Multi-agent systems"
    - "Full-stack (NestJS, Next.js, React)"
    - "Python, TypeScript"
    - "Kubernetes, Docker, DevOps"
    - "PostgreSQL, SQL Server"
    - "LLM evaluation, NLP"
  education: "B.A. Computer Science (Honors), Business Economics"
  dealbreakers: []  # e.g., "must sponsor visa"
  scan_sources:
    - "LinkedIn Jobs"
    - "Indeed"
    - "Glassdoor"
    - "Y Combinator Work at a Startup"
    - "Handshake"
    - "University career boards"
    - "Specific company career pages (configurable list)"
```

**Three-Tier Filtering Pipeline**:

| Stage | Model | Input | Output | Volume |
|-------|-------|-------|--------|--------|
| 1. Collection | Local scraper/API | Job board APIs, RSS feeds | Raw job listings | ~500–1000/day |
| 2. Initial Screen | Gemini Flash | Raw listings + user criteria | Filtered list (criteria match) | ~50–100 |
| 3. Fit Evaluation | Gemini Pro | Filtered listings + user profile | Scored + summarized shortlist | ~10–20 |
| 4. Deep Review | Claude (on-demand) | Top matches + user context | Ranked list with fit analysis, concerns, and application strategy | ~3–5 |

**Daily Job Digest** (delivered in morning briefing + available via `/jobs`):
```json
{
  "date": "2026-03-09",
  "new_matches": 3,
  "top_matches": [
    {
      "title": "ML Research Engineer",
      "company": "Scale AI",
      "location": "San Francisco, CA (Hybrid)",
      "salary_range": "$150K–$200K",
      "match_score": 92,
      "match_reasons": ["Multi-agent systems experience", "Python + ML stack", "Research-oriented"],
      "concerns": ["Requires 3+ years industry experience"],
      "url": "https://...",
      "actions": ["Save", "Apply", "Skip"]
    }
  ]
}
```

**Application Pipeline** (triggered when user taps "Apply"):
1. Job moves to Notion tracker with status "Applying"
2. Claude drafts tailored cover letter based on job description + user's resume/profile
3. Claude suggests resume bullet adjustments for this specific role
4. Materials sent to user for approval
5. User approves → status moves to "Applied", deadline tracked
6. Reminder to follow up if no response in 2 weeks

**Interview Prep** (triggered when user updates job status to "Interview"):
1. Claude researches the company (recent news, tech stack, culture, Glassdoor reviews)
2. Generates likely interview questions based on role + company
3. Prepares talking points that connect user's experience to the role
4. Delivers a briefing the night before the interview

#### 7.2.4 Fashion & Outfit Agent

**Trigger**: Daily at 5:45 AM (before morning briefing) + on-demand
**AI Model**: Gemini Vision (wardrobe analysis + outfit generation)

**Wardrobe Catalog System**:
- User photographs individual clothing items via the iOS app camera
- Gemini Vision catalogs each item:
  ```json
  {
    "id": "wardrobe_047",
    "type": "shirt",
    "sub_type": "v-neck",
    "color": "white",
    "pattern": "solid",
    "season": ["spring", "summer", "fall"],
    "formality": "smart-casual",
    "brand": "H&M",
    "image_path": "/wardrobe/047.jpg",
    "last_worn": "2026-03-05",
    "wear_count": 12
  }
  ```
- Catalog stored in state DB with image references on Node 2

**Daily Outfit Suggestion**:
- Inputs: weather forecast, calendar events (formality level), wardrobe catalog, recently worn items
- Output: 1–2 outfit options with reasoning
- Included in morning briefing voice narrative

**Shopping Advisor** (on-demand):
- User: "I'm going to Tanger Outlets, what should I buy under $100?"
- T.A.R.S. analyzes wardrobe gaps (e.g., "You have no tan/brown casual shirts")
- Gemini researches stores at that specific outlet mall
- Cross-references gaps with available stores + season + budget
- Returns: "It's fall — pick up a tan casual shirt from H&M ($29.99) and dark brown chinos from Gap ($44.99). They'll pair well with your navy jacket and white sneakers."

#### 7.2.5 Daily Life Manager Agent

**Trigger**: On-demand + proactive suggestions
**AI Model**: Gemini Flash (scheduling logic), Claude (complex planning)
**Capabilities**:

- **Natural language scheduling**: "Schedule dentist Friday 2pm" → creates iCloud Calendar event via CalDAV/EventKit, confirms
- **Smart scheduling**: "Find me 2 hours for AtlasDesk work this week" → analyzes calendar, proposes blocks
- **Reminder creation**: "Remind me to follow up with Prof. Liang on Thursday" → Notion task or calendar reminder
- **Grocery/errand tracking**: "Add milk to my grocery list" → Notion database
- **Commute planning**: Calculates leave-home time based on event + distance + traffic (Google Maps Directions API)
- **Context-aware suggestions**: Sunny weekend → car wash. Grocery list has items → remind near store. Haven't been to gym in 3 days → suggest a slot.

#### 7.2.6 System Health Monitor Agent

**Trigger**: Continuous polling every 5 minutes
**AI Model**: Local monitoring (zero AI tokens) for detection; Claude (on-demand) for diagnostics
**Scope**: AtlasDesk backend infrastructure ONLY — system health, not business metrics

**Monitoring Checks**:

| Check | Source | Alert Threshold |
|-------|--------|----------------|
| Service availability | Grafana API / health endpoints | Any service non-200 or timeout |
| Error rate | Loki log queries (LogQL) | Error count >2× baseline in 15-min window |
| Container restarts | Docker/K8s metrics via Grafana | Any unexpected restart |
| Response latency | Grafana dashboards | P95 latency >2s |
| SSL certificate expiry | Direct TLS check | <14 days until expiration |
| Disk usage | Node metrics | >85% utilization |

**Alert Flow**:
1. Local monitor detects anomaly (no AI tokens)
2. Severity assessed locally (down vs. degraded vs. warning)
3. If anomaly confirmed → spawn Claude diagnostic agent with recent Loki logs
4. Claude analyzes logs, identifies root cause, proposes remediation
5. Push notification: "[ALERT] AtlasDesk API restarted — OOM kill detected. Recommend: increase memory limit to 512MB. Apply fix?"
6. User approves/rejects via iOS app or Apple Watch

**Log Access Architecture**:
- Promtail (already on Wooster server) ships logs to Loki
- Loki HTTP API exposed via Cloudflare Tunnel from Wooster server (outbound-only, no SSH needed)
- T.A.R.S. on Node 1 queries Loki API over the tunnel
- **Action Item**: Confirm with Wooster IT that `cloudflared` can be installed for Loki/Grafana tunnel

#### 7.2.7 Communication Drafter Agent

**Trigger**: On-demand ("Draft an email to Prof. Sadigh about...")
**AI Model**: Claude (always — high-stakes communication)
**Scope**: Email drafts via Gmail (send-on-behalf with approval)

**Process**:
1. User describes communication intent
2. Claude generates draft with context from state DB (past drafts to this recipient, relationship history, tone preferences)
3. Draft presented in iOS app / Telegram with full preview
4. User: **Approve** (sends immediately) / **Edit** (opens editor) / **Reject** (discards)
5. **HARD RULE**: NO communication sent without explicit approval

**Future**: LinkedIn message drafting (deferred, depends on API complexity)

#### 7.2.8 Product Research Agent

**Trigger**: On-demand ("Find me a good quality minoxidil")
**AI Model**: Gemini Pro (web search + comparison), Claude (if complex evaluation needed)

**Process**:
1. Gemini Pro with web search researches the product
2. Compares options across Amazon, retailers, review sites
3. Returns structured comparison: name, price, rating, pros/cons, purchase link
4. User reviews and clicks to purchase (T.A.R.S. NEVER makes purchases)

**Output**:
```json
{
  "query": "good quality minoxidil",
  "results": [
    {
      "name": "Kirkland Minoxidil 5% Solution",
      "price": "$24.99 (6-month supply)",
      "rating": "4.5/5 (12,000+ reviews)",
      "pros": ["Best value", "Proven formula", "Same active ingredient as Rogaine"],
      "cons": ["Liquid applicator less precise than foam"],
      "url": "https://amazon.com/..."
    }
  ],
  "recommendation": "Kirkland is the best value — same active ingredient as name brands at 1/3 the price."
}
```

#### 7.2.9 Coding/DevOps Agent

**Trigger**: On-demand ("Implement email notification service for AtlasDesk")
**AI Model**: Claude Code (always — code generation requires deep reasoning)

**Process**:
1. Orchestrator on Node 1 dispatches to Node 2 via Redis
2. Node 2 spins up Docker container, clones relevant repo
3. Injects project CLAUDE.md for codebase context
4. Spawns Claude Code headless to implement feature/fix
5. Claude Code writes code, runs tests, validates
6. Creates branch, pushes, opens PR
7. Sends diff summary to user for approval
8. **HARD RULE**: Never pushes to main/production without explicit approval

#### 7.2.10 Research Agent

**Trigger**: On-demand ("What's new in multi-agent coordination since my thesis?")
**AI Model**: Claude (deep comprehension + cross-referencing with user's work)

**Process**:
1. Claude Code with web search researches the topic
2. Cross-references with user's known work (OMM, routing loops — context from state DB)
3. Returns structured brief with summaries, relevance to user's research, and source links

#### 7.2.11 End-of-Day Summary Agent

**Trigger**: Cron at 10:30 PM (configurable, adjusts to user activity patterns)
**AI Model**: Gemini Pro (summarization)

**Output**:
- Tasks completed today
- Tasks rolling to tomorrow
- Tomorrow's schedule preview
- Any overnight monitoring alerts expected
- Suggested wake-up time (based on first event + commute)
- T.A.R.S. sets the alarm accordingly

#### 7.2.12 Finance Tracking Agent

**Trigger**: Daily automatic sync + on-demand queries
**AI Model**: Gemini Flash (categorization, trend detection), Claude (complex financial analysis on-demand)
**Data Source**: Plaid API (read-only bank/card transaction access)

**Plaid Integration**:
- Read-only connection to user's bank accounts/credit cards via Plaid Link OAuth flow
- T.A.R.S. NEVER has write access — cannot move money, make payments, or modify accounts
- Plaid credentials handled via Plaid's secure OAuth — T.A.R.S. never sees bank passwords
- Transaction data synced daily and stored in local PostgreSQL (never sent to third parties beyond Plaid)

**Capabilities**:
- **Daily spend summary**: "You spent $47.23 yesterday — $32 at Walmart (groceries), $15.23 at Starbucks."
- **Monthly tracking**: Running total, category breakdowns (food, transport, subscriptions, shopping)
- **Trend detection**: "Dining out is up 30% vs. last month" or "Your AWS bill jumped from $45 to $89"
- **Subscription tracking**: Identifies recurring charges, tracks when they hit, alerts on price increases
- **Unusual charge alerts**: "New charge of $299 at Best Buy — expected?"
- **Budget vs. actual** (if user sets budgets): "You've used 78% of your dining budget with 10 days left"
- **Included in morning briefing**: Yesterday's spending + month-to-date summary

**Estimated Plaid cost**: Free tier covers initial 200 API calls. Ongoing: ~$1–5/month for single-user personal transaction access on pay-as-you-go.

**Output Example**:
```json
{
  "period": "2026-03-08",
  "daily_total": 47.23,
  "transactions": [
    {"merchant": "Walmart", "amount": 32.00, "category": "Groceries"},
    {"merchant": "Starbucks", "amount": 15.23, "category": "Dining"}
  ],
  "month_to_date": {
    "total": 1247.00,
    "by_category": {
      "Groceries": 312.00,
      "Dining": 189.00,
      "Subscriptions": 147.00,
      "Transport": 95.00,
      "Shopping": 234.00,
      "Other": 270.00
    }
  },
  "alerts": ["Dining out up 30% vs February", "AWS bill increased to $89"]
}
```

#### 7.2.13 Health & Fitness Agent

**Trigger**: Daily at 5:45 AM (incorporated into morning briefing) + on-demand
**AI Model**: Gemini Flash (pattern detection, recommendations)
**Data Source**: Apple HealthKit via iOS app

**HealthKit Data Accessed**:

| Data Type | Use |
|-----------|-----|
| Sleep duration & quality | Morning briefing: "You slept 7.2 hours — good energy day" |
| Step count | Daily activity tracking, sedentary alerts |
| Workouts (type, duration, calories) | Gym frequency tracking, workout reminders |
| Heart rate (resting) | General health trend monitoring |
| Exercise minutes | Activity goal tracking |

**Capabilities**:
- **Morning health summary**: Sleep quality + yesterday's activity in briefing narrative
- **Gym reminders**: "You haven't worked out in 3 days. You're free at 6pm — want me to block it?"
- **Activity nudges**: "You've only walked 2,000 steps today. Consider a walk after lunch."
- **Trend tracking**: Weekly/monthly fitness summaries on demand
- **Calendar-aware**: Won't suggest gym on days with back-to-back meetings or travel

**Data Flow**: HealthKit data is read by the iOS app via HealthKit framework, synced to the T.A.R.S. backend via the REST API, and stored in the state DB for trend analysis. Data stays local — never sent to third-party services.

---

## 8. Integration Requirements

### 8.1 iCloud Calendar (Dual Approach)

| Context | Method | Library/Framework | Operations |
|---------|--------|-------------------|-----------|
| iOS App | **EventKit** (native Apple framework) | EventKit (Swift) | Read, create, modify, delete events. Access to all calendars including synced Outlook. |
| Backend (Node 1) | **CalDAV** (RFC 4791) | `caldav` (Python) | Read events for briefing. Create events for scheduling. |
| Auth | App-Specific Password via Apple ID settings | — | Required for CalDAV server-side access |
| Sync | EventKit: real-time on device. CalDAV: poll every 5 minutes + on-demand | — | — |

**Key Insight**: User's iCloud Calendar is already synced with work Outlook calendar. This gives T.A.R.S. access to work schedule without needing Microsoft Graph API.

### 8.2 Gmail (2 Accounts)

| Attribute | Detail |
|-----------|--------|
| Account 1 | Personal (connected with everything) |
| Account 2 | PhD/professional (job search, academic contacts) |
| API | Gmail API (REST) via Google Cloud project |
| Auth | OAuth 2.0 (offline access with refresh token) |
| Operations | Read, list, label, archive, send (with approval) |
| Scope | `gmail.readonly` + `gmail.send` + `gmail.modify` |
| Sync | Gmail push notifications (Pub/Sub) or polling every 5 minutes |

### 8.3 GitHub

| Attribute | Detail |
|-----------|--------|
| Scope | Personal repositories only |
| API | GitHub REST API v3 + GraphQL API v4 |
| Auth | Personal Access Token (PAT) — repo + notifications scope |
| Operations | Read notifications, list PRs, create branches, open PRs, read issues |
| Sync | Polling every 15 minutes + on-demand |

### 8.4 Grafana / Loki (AtlasDesk Monitoring)

| Attribute | Detail |
|-----------|--------|
| Grafana | HTTP API for dashboard queries and alerts |
| Loki | HTTP API for log queries (LogQL) |
| Promtail | Partially configured on Wooster server — needs completion |
| Scope | System health ONLY — service status, errors, container health, latency |
| Access | Loki/Grafana HTTP API exposed via Cloudflare Tunnel from Wooster server |
| Sync | Poll every 5 minutes + on-demand diagnostic queries |

**Action Item**: Ask Wooster IT about installing `cloudflared` for outbound tunnel.

### 8.5 Notion

| Attribute | Detail |
|-----------|--------|
| Account | New personal account (to be created) |
| API | Notion API (REST) |
| Auth | Internal integration token |
| Databases to Create | Tasks, Grocery List, PhD Timeline, Job Tracker, Wardrobe Catalog, Notes, Contact CRM |
| Operations | Create pages, query databases, update properties |

### 8.6 Google Gemini API

| Attribute | Detail |
|-----------|--------|
| Models | Gemini 2.0 Flash, Gemini Pro, Gemini Vision |
| API | Google AI Studio / Vertex AI REST API |
| Auth | API key or service account |
| Use Cases | Email classification, job screening, briefing composition, outfit suggestions, product research, image analysis |
| Cost | Pay-per-token (Flash: ~$0.10/1M input, ~$0.40/1M output) |

### 8.7 Weather

| Attribute | Detail |
|-----------|--------|
| Provider | OpenWeatherMap API (free tier: 1,000 calls/day) |
| Location | Wooster, Ohio (default), adjustable |
| Data | Current conditions, hourly forecast, daily forecast, severe weather alerts |
| Sync | Fetch at 5:45 AM for briefing + on-demand |

### 8.8 Web Search (Product & Job Research)

| Attribute | Detail |
|-----------|--------|
| For Claude agents | Built-in Claude Code web search |
| For Gemini agents | Google Search via Gemini's grounding capability or SerpAPI |
| Use Cases | Product research, job listing verification, company research, current events |

### 8.9 Apple Contacts (via iOS App)

| Attribute | Detail |
|-----------|--------|
| Framework | Contacts (Swift) |
| Access | Read-only (request permission on first launch) |
| Data | Names, email addresses, phone numbers, organization, relationship labels, notes |
| Sync | iOS app syncs contact data to backend periodically + on-demand |
| Use Case | Auto-enriches email classification with sender identity and relationship context |

### 8.10 Apple HealthKit (via iOS App)

| Attribute | Detail |
|-----------|--------|
| Framework | HealthKit (Swift) |
| Access | Read-only (request permission for specific data types) |
| Data Types | Sleep analysis, step count, workouts, resting heart rate, exercise minutes |
| Sync | iOS app syncs health data to backend daily (early morning before briefing) |
| Use Case | Morning health summary, gym reminders, activity tracking, trend analysis |
| Privacy | Data stored locally on Node 1 only. Never sent to third-party services. |

### 8.11 Plaid (Finance / Transaction Data)

| Attribute | Detail |
|-----------|--------|
| API | Plaid API (REST) |
| Auth | Plaid Link OAuth flow (user authenticates directly with their bank) |
| Access | **Read-only** — transactions, balances, account metadata. NO write/transfer capability. |
| Operations | List transactions, get balances, list accounts |
| Accounts | User's bank accounts and credit/debit cards (user chooses which to link) |
| Sync | Daily transaction pull + on-demand |
| Cost | Free tier (200 API calls) → Pay-as-you-go (~$1–5/month for personal use) |
| Privacy | Transaction data stored locally on Node 1 PostgreSQL. Never shared beyond Plaid's secure connection. |

### 8.12 Porcupine / Wake Word Detection

| Attribute | Detail |
|-----------|--------|
| Library | Porcupine by Picovoice (free tier supports custom wake words) |
| Hardware | USB conference speakerphone/mic connected to Node 1 |
| Wake Word | "Hey TARS" (custom trained via Picovoice console) |
| Runtime | Always-on daemon on Node 1, listening on USB audio input |
| Process | Wake word detected → start recording → silence detection → STT → T.A.R.S. orchestrator |
| STT Engine | Whisper (local, OpenAI open-source) or Google Speech-to-Text API |
| CPU Impact | Minimal — Porcupine is designed for low-resource always-on detection |

### 8.13 HomePod Mini (Audio Output)

| Attribute | Detail |
|-----------|--------|
| Connection | AirPlay 2 from Node 1 or via iOS app relay |
| Use Cases | Morning briefing audio, T.A.R.S. voice responses to wake word commands, alerts |
| Integration | Siri Shortcuts on HomePod can call T.A.R.S. API endpoints |
| Setup | HomePod on same WiFi network as Node 1 |
| Audio Output | T.A.R.S. generates TTS audio (iOS AVSpeechSynthesizer or server-side TTS) → streams to HomePod via AirPlay |

### 8.14 Job Board APIs / Scrapers

| Source | Access Method |
|--------|--------------|
| LinkedIn Jobs | LinkedIn API (limited) or SerpAPI LinkedIn Jobs scraper |
| Indeed | Indeed API or Apify scraper |
| Glassdoor | SerpAPI or web scraping |
| Y Combinator WaaS | Public API |
| Handshake | API (if available for alumni) or scraping |
| Company career pages | Custom scrapers per company (configurable list) |

**Note**: Job board access methods will be validated during Phase 2. Some may require paid scraping services. T.A.R.S. should be designed with an adapter pattern so job sources can be swapped easily.

---

## 9. Autonomy & Approval Model

### 9.1 Risk Classification Framework

Every action is classified into one of three risk tiers:

#### Tier 1: Autonomous (No Approval Needed)

Read-only, informational, or no external side effects.

| Action Category | Examples |
|----------------|---------|
| Data fetching | Calendar events, emails, weather, GitHub notifications, job listings, system health |
| Classification | Email triage, job matching, intent recognition |
| Internal processing | Briefing generation, outfit suggestion, product comparison, summarization |
| Internal storage | State DB writes, vector memory updates, wardrobe catalog updates |
| User-requested config changes | "Move jobs to top of briefing" (explicit user command) |

#### Tier 2: Approval Required (Propose → Preview → Approve/Reject)

External side effects, low-to-medium risk.

| Action | Preview Shown |
|--------|--------------|
| Creating/modifying calendar events | Event details (title, time, duration) |
| Sending email responses | Full draft with recipient, subject, body |
| Archiving/labeling emails | List of emails affected |
| Creating Notion tasks/pages | Task/page details |
| Creating GitHub branches/PRs | Branch name, diff summary |
| Posting to any external platform | Full content preview |
| Applying to jobs (moving to "Applied" status) | Cover letter + materials preview |

**Approval Flow**:
1. Agent prepares action + generates preview
2. Preview pushed to iOS app / Telegram: **Approve** / **Edit** / **Reject**
3. Apple Watch: simplified **Approve** / **Reject** (edit requires phone)
4. Timeout: if no response in 1 hour (configurable), action expires + user notified
5. All decisions logged for audit

#### Tier 3: Escalation (Full Review Required)

High-risk or irreversible actions.

| Action | Additional Safeguards |
|--------|---------------------|
| Emails to professors / professional contacts | Full draft + relationship context + past correspondence summary |
| Pushing code to production / main branch | Full diff + test results |
| Deleting any data | Explicit "confirm delete" interaction |
| Modifying production infrastructure | Detailed change plan |
| Any action involving money | **NEVER allowed** (hard dealbreaker) |

### 9.2 Master Dealbreaker Rule

> **NO email, message, response, or external action is EVER executed without the user's explicit verbal or text approval. This includes "small" actions. Zero exceptions. This is the system's #1 invariant.**

---

## 10. Data Architecture & Memory

### 10.1 State Database (PostgreSQL — Node 1)

Central source of truth for T.A.R.S.'s memory and configuration.

**Core Tables**:

| Table | Purpose |
|-------|---------|
| `conversations` | Chat history between user and T.A.R.S. |
| `agent_tasks` | Task queue: pending, running, completed, failed |
| `approvals` | Pending + completed approval requests with decisions |
| `email_classifications` | Email classification history + user feedback corrections |
| `briefings` | Generated briefings archive |
| `config` | User preferences, briefing config, notification settings, job search criteria |
| `contacts` | Known contacts with priority hints (professor, recruiter, noise) |
| `agent_outputs` | Stored outputs from completed agent tasks |
| `system_health_log` | Historical health snapshots |
| `feedback_log` | User corrections and feedback for learning |
| `job_listings` | Scanned jobs with scores, status (new, saved, applied, interview, offer, rejected) |
| `job_applications` | Application materials (cover letters, resume versions) per job |
| `wardrobe_items` | Clothing catalog with metadata (type, color, season, formality, brand, image_ref) |
| `wardrobe_outfits` | Suggested and worn outfit history |
| `model_usage` | AI model call logs (model, tokens, cost, task type) for budget tracking |
| `transactions` | Financial transactions from Plaid (merchant, amount, date, category) |
| `finance_summaries` | Daily/weekly/monthly spending summaries and trend data |
| `health_data` | Synced HealthKit data (sleep, steps, workouts, heart rate) |
| `apple_contacts` | Synced Apple Contacts for email enrichment (name, email, org, relationship) |

### 10.2 Semantic Memory (ChromaDB — Node 2)

Vector store for semantic search across accumulated knowledge.

**Use Cases**:
- "What did I decide about AtlasDesk pricing?" → searches past conversations
- Provides relevant context to agents without full history injection
- Email classification benefits from semantic similarity to past decisions
- Job matching benefits from semantic understanding of user's experience

### 10.3 Job Queue (Redis — Node 2)

- **Purpose**: Async job dispatch from Node 1 to Node 2
- **Pattern**: Bull/BullMQ with priority levels
- **Job Types**: code-execution, diagnostics, research, job-scraping, image-processing
- **Results**: Returned via Redis pub/sub

### 10.4 File Storage (Node 2)

- Wardrobe images
- Agent output files (diffs, reports, research briefs)
- Repository clones (for coding agent)
- Docker images

### 10.5 Context Window Management

Each AI invocation gets ONLY the context it needs:

| Agent Type | Context Provided |
|------------|-----------------|
| Email Classifier (Gemini) | Batch of new emails + classification rules + 20 recent corrections + Apple Contacts data for sender |
| Morning Briefing (Gemini) | Structured data payload (calendar, email, weather, health, finance, jobs) + briefing config |
| Job Screener (Gemini Flash) | Batch of job listings + user profile criteria |
| Job Evaluator (Gemini Pro) | Filtered listings + full user profile |
| Outfit Advisor (Gemini Vision) | Weather + calendar + wardrobe catalog subset |
| Communication Drafter (Claude) | Recipient context + past drafts + tone preferences |
| System Diagnostics (Claude) | Last 50 relevant log lines + service architecture |
| Coding Agent (Claude) | CLAUDE.md + relevant source files + task description |
| Product Research (Gemini) | User query + budget constraints |
| Finance Tracker (Gemini) | Recent transactions + spending history + category patterns |
| Health Agent (Gemini) | HealthKit data (sleep, steps, workouts) + recent activity trends |

**Anti-Hallucination Rules**:
- Agents NEVER answer from "memory" — all facts explicitly provided via API data
- Structured JSON output enforced — no free-form prose that needs parsing
- For critical actions, a second model call verifies the first agent's output
- All factual claims grounded in real data (API responses, log files, database records)

### 10.6 Token Efficiency Strategy

| Strategy | Implementation |
|----------|---------------|
| Batch operations | Morning briefing = ONE AI call with all data |
| Local-first processing | Python handles all API calls, formatting, filtering before any AI invocation |
| Model routing | Gemini handles 60–70% of calls; Claude reserved for complex tasks |
| Structured output | JSON schemas enforced — no wasted tokens on formatting |
| Caching | Repeated queries served from state DB if data <5 minutes old |
| Model selection per task | Gemini Flash for simple, Pro for moderate, Claude for complex |
| Usage monitoring | T.A.R.S. tracks daily/weekly AI call counts and estimated costs |

---

## 11. Networking & Security

### 11.1 Network Topology

```
Internet (T-Mobile Home Internet — CGNAT, no static IP)
    │
    ├── Cloudflare Tunnel (from Node 1)
    │   └── Exposes: T.A.R.S. API, Telegram webhook (if using webhook mode)
    │
    ├── Cloudflare Tunnel (from Wooster Server)
    │   └── Exposes: Loki API, Grafana API (outbound-only, no SSH needed)
    │
    └── Tailscale Mesh VPN
        ├── Node 1 "Brain" (10.0.1.1)
        ├── Node 2 "Muscle" (10.0.1.2)
        ├── iPhone (user device — access from anywhere)
        └── Wooster Server (optional direct access)
```

### 11.2 T-Mobile CGNAT Solution

T-Mobile home internet uses Carrier-Grade NAT — no inbound connections, no static IP.

**Cloudflare Tunnel** (`cloudflared`): Creates outbound-only encrypted tunnels. Provides stable hostname (e.g., `tars.yourdomain.com`), automatic TLS, DDoS protection, no port forwarding needed.

**Tailscale**: WireGuard-based mesh VPN. Private network between all nodes + iPhone. Direct device-to-device connections regardless of NAT. Remote access from anywhere.

**Strategy**: Cloudflare Tunnel for external endpoints (API, webhooks). Tailscale for internal communication (inter-node, remote access).

### 11.3 Security Requirements

| Requirement | Implementation |
|-------------|---------------|
| Credential storage | Environment variables or encrypted secrets (`pass` or `sops`). NEVER plain text. |
| Database encryption | PostgreSQL SSL connections. Sensitive fields encrypted at rest. |
| API authentication | iOS app → backend via API key + device token. Single-user lightweight auth. |
| Network encryption | Tailscale (WireGuard) for inter-node. HTTPS/TLS for all external. |
| Audit logging | All agent actions, approvals, config changes logged with timestamps. |
| Least privilege | Minimum API scopes per integration. Gmail: readonly+send+modify. GitHub: repo+notifications. |
| No financial access | T.A.R.S. NEVER has payment methods, bank accounts, or financial service access. |
| Gemini API security | API key stored as secret. Requests use HTTPS. No sensitive data sent unnecessarily. |
| Wardrobe images | Stored locally on Node 2 only. Never uploaded to third-party services beyond Gemini API for analysis. |
| Plaid financial data | Transaction data stored locally on Node 1 PostgreSQL only. Plaid OAuth handles bank authentication — T.A.R.S. never sees bank credentials. Read-only access only. |
| HealthKit data | Synced from iOS app to Node 1 only. Never shared with third-party services. User grants explicit HealthKit permission on iOS. |
| Apple Contacts data | Synced from iOS app to Node 1 for email enrichment. Never shared externally. |
| Wake word audio | Audio from USB mic processed locally on Node 1. Only post-wake-word speech sent to STT. No always-on recording stored. |

---

## 12. Non-Functional Requirements

### 12.1 Performance

| Metric | Target |
|--------|--------|
| Briefing generation | <45 seconds (data fetch + AI composition + delivery) |
| Simple query response | <3 seconds (Gemini Flash) |
| Complex query response | <15 seconds (Claude Code) |
| System health alert latency | <5 minutes from incident to notification |
| Approval-to-action execution | <30 seconds after approve |
| iOS app cold launch | <3 seconds to usable state |
| Job scan completion | <10 minutes for full daily scan |

### 12.2 Reliability

| Metric | Target |
|--------|--------|
| System uptime (6 AM–11 PM) | >99% |
| Morning briefing delivery | 100% (fallback: raw data if AI fails) |
| Data loss prevention | PostgreSQL WAL + daily backups to external storage |
| Graceful degradation | If Claude unavailable → Gemini handles what it can, local workers continue |
| If Gemini unavailable | Claude handles critical tasks, local workers continue |
| If both AI unavailable | Local workers continue (health checks, calendar sync, raw data delivery) |

### 12.3 Maintainability

- All services containerized (Docker) for updates and rollbacks
- Configuration via state DB (natural language adjustable)
- Logs centralized on Node 2
- T.A.R.S. self-health endpoint (meta-monitoring)
- CLAUDE.md in project repo for Claude Code context
- Modular agent design — add new agents without modifying core orchestrator

### 12.4 Scalability

Not a primary concern (single user, fixed hardware). However:
- Agent system supports horizontal addition of new agent types
- Integration layer uses adapter pattern — new services added without core changes
- Config-driven behavior enables new features without code changes
- Multi-model architecture allows adding new AI models (e.g., local Llama for offline fallback)

---

## 13. Hard Constraints & Dealbreakers

Non-negotiable rules. Violation = critical defect.

| # | Constraint |
|---|-----------|
| **HC-01** | No email, message, or external communication sent without explicit user approval. Zero exceptions. |
| **HC-02** | No code pushed to production or main branches without explicit approval. |
| **HC-03** | No data deleted without explicit confirmation. |
| **HC-04** | T.A.R.S. may NEVER initiate financial transactions, make purchases, move money, or modify any financial account. T.A.R.S. MAY passively observe transaction history via Plaid read-only API for expense tracking. All financial data stored locally — never sent to third parties beyond Plaid's secure connection. |
| **HC-05** | No credentials stored in plain text anywhere. |
| **HC-06** | No direct modification of AtlasDesk production databases. |
| **HC-07** | T.A.R.S. never impersonates the user. Automated messages must be user-approved. |
| **HC-08** | All actions logged and auditable. |
| **HC-09** | Graceful degradation if any AI model unavailable — local workers continue. |
| **HC-10** | User can disable any agent or integration at any time via simple command. |
| **HC-11** | Wardrobe images stored locally only — never persisted on third-party services. |
| **HC-12** | T.A.R.S. AI usage stays within budget — alerts before hitting Claude weekly limits. |

---

## 14. Scope Boundaries

### 14.1 In Scope (v1.0 — 3-Month Target)

- Custom iOS app with alarm, voice briefing, chat, camera, and approval interface
- Apple Watch companion (notifications + quick approvals)
- **HomePod Mini integration (morning briefing audio, Siri bridge, ambient voice)**
- **Custom wake word "Hey TARS" via USB mic + Porcupine on Node 1 (home)**
- **Siri Shortcuts bridge for mobile voice commands**
- Telegram bot fallback
- Multi-model AI architecture (Claude + Gemini)
- Morning and end-of-day briefings with outfit suggestions
- Email classification and triage (2 Gmail accounts)
- **Apple Contacts integration (auto-enriches email classification)**
- iCloud Calendar integration (EventKit + CalDAV, including synced work calendar)
- Job search agent (multi-source scanning, three-tier filtering, application pipeline)
- Fashion/outfit agent (wardrobe catalog, daily suggestions, shopping advisor)
- Natural language scheduling and task management
- Notion workspace setup and integration (tasks, lists, timeline, job tracker)
- AtlasDesk system health monitoring via Grafana/Loki
- GitHub notifications and PR workflow
- Product research agent
- Communication drafting with approval flow
- **Finance tracking agent via Plaid (read-only transaction monitoring, spending insights)**
- **Health & fitness agent via HealthKit (sleep, steps, workouts in briefing)**
- Weather-aware recommendations
- Configurable preferences via natural language
- Two-node infrastructure (Brain + Muscle)
- AI usage monitoring and budget tracking
- **Docker-based deployment (Mac → GitHub → GHCR → servers)**
- **Self-deploy command (/deploy via Telegram or iOS app)**

### 14.2 Out of Scope (v1.0) — Future Enhancements

| Feature | Reason | Timeline |
|---------|--------|----------|
| Work Outlook email (direct) | Calendar already synced via iCloud. Email access unclear with Wooster IT. | v1.1 if permitted |
| AtlasDesk app-level metrics | System health sufficient for now | v1.1 |
| LinkedIn integration | API complexity, rate limits | v1.2 |
| Location-based triggers | iOS background location, battery impact | v1.2 |
| Voice wake-word on iOS ("Hey TARS" everywhere) | iOS platform restriction — no always-on mic for third-party apps | Siri bridge on mobile; true wake word at home via USB mic |
| Smart home integration | Out of scope for productivity assistant | v2.0 |
| Local LLM inference | Hardware insufficient | Until hardware upgrade |
| Multi-user support | Single-user by design | Never (unless commercialized) |
| Automatic purchases | Hard dealbreaker | Never |

### 14.3 Dependencies & Prerequisites

| Dependency | Status | Owner |
|------------|--------|-------|
| Apple Developer Account ($99/year) | **To purchase** | Tasin |
| HomePod Mini ($99 — covered by Apple gift card) | **To purchase** | Tasin |
| USB conference speakerphone/mic (~$20–30) | **To purchase** | Tasin |
| Ubuntu Server 24.04 on both nodes | Not started | Tasin + Claude Code |
| iCloud App-Specific Password | Not started | Tasin |
| Google Cloud project + OAuth (2 Gmail accounts) | Not started | Tasin |
| Google AI Studio API key (Gemini) | Not started | Tasin |
| GitHub Personal Access Token | Not started | Tasin |
| Notion account + workspace | Not started | Tasin |
| Plaid account + Link setup | Not started | Tasin |
| Picovoice account (Porcupine wake word) | Not started | Tasin |
| Grafana/Loki config completion on Wooster server | Partially done | Tasin |
| Cloudflare account + domain | Not started | Tasin |
| Cloudflare Tunnel on Wooster server (for Loki) | Needs IT approval | Tasin → Wooster IT |
| Tailscale account | Not started | Tasin |
| Telegram Bot (BotFather) | Not started | Tasin |
| OpenWeatherMap API key | Not started | Tasin |
| Check with Wooster IT for spare RAM/SSD | Scheduled next week | Tasin |

---

## 15. Phased Delivery Plan

### Phase 0: Infrastructure Setup (Days 1–3)

**Goal**: Both Z2 Minis running, networked, externally accessible, and all accounts/credentials ready.

- Install Ubuntu Server 24.04 LTS on both nodes
- Configure static IPs on private subnet
- Install Tailscale on both nodes + iPhone
- Install Cloudflare Tunnel on Node 1
- Install Docker + Docker Compose on both nodes
- Install PostgreSQL on Node 1
- Install Redis on Node 2
- Install ChromaDB on Node 2
- Set up SSH key auth between nodes
- Set up GitHub Container Registry (ghcr.io) + GitHub Actions CI/CD pipeline
- Connect USB conference mic to Node 1, verify audio input
- Set up HomePod Mini on same WiFi network, verify AirPlay from Node 1
- Create Telegram bot via BotFather
- Set up Google Cloud project + OAuth for both Gmail accounts
- Generate iCloud App-Specific Password
- Set up Google AI Studio API key for Gemini
- Create Notion workspace with initial database schemas
- Create Plaid account + connect bank accounts via Plaid Link
- Create Picovoice account + generate custom "Hey TARS" wake word model
- Verify all external API credentials

### Phase 1: Telegram MVP + Core Intelligence (Days 4–14)

**Goal**: Working assistant via Telegram with multi-model intelligence.

- Orchestrator daemon (Python asyncio) on Node 1
- Model Router (Claude vs. Gemini routing logic)
- Claude Code spawner (subprocess wrapper)
- Gemini API client
- Telegram bot gateway (python-telegram-bot)
- Basic message routing: user → intent classifier → model router → response
- State DB schema + CRUD operations
- iCloud Calendar integration (CalDAV — read events)
- Gmail integration (read, classify via Gemini Flash)
- Weather integration (OpenWeatherMap)
- Basic morning briefing (text-only, Telegram delivery)
- Email classification with feedback loop
- Basic approval system (Telegram inline keyboards)
- End-of-day summary
- **Porcupine wake word daemon ("Hey TARS") on Node 1 with USB mic**
- **Basic voice command pipeline: wake word → STT → orchestrator → TTS → HomePod AirPlay**

**Exit Criteria**: User can message Telegram bot, get morning briefing, see classified emails, approve actions, and receive end-of-day summary. "Hey TARS" wake word works at home with HomePod audio output. Gemini handles routine tasks, Claude handles complex queries.

### Phase 2: Job Search + Fashion + Deep Agents (Days 15–28)

**Goal**: Job search pipeline, wardrobe system, and all major agents operational.

- Job search agent (multi-source scanning + three-tier filtering)
- Job listing Notion database + tracker
- Application pipeline (Claude drafts cover letters)
- Fashion/outfit agent (Gemini Vision)
- Wardrobe catalog system (photo upload → Gemini cataloging)
- Daily outfit suggestions in briefing
- Product research agent
- Communication drafter with approval
- Calendar event creation with approval
- Notion integration (tasks, grocery list, PhD timeline)
- GitHub notifications integration
- Grafana/Loki integration (AtlasDesk health monitoring)
- System health alerting with Claude diagnostic agent
- Smart scheduling ("find me 2 free hours this week")
- Briefing configuration system (natural language adjustment)
- AI usage monitoring dashboard
- **Finance tracking agent (Plaid integration, daily spending, monthly summaries)**
- **Apple Contacts sync for email classifier enrichment**

**Exit Criteria**: All core agents operational. Job search runs daily. Outfit suggestions in briefing. Finance tracking active. Full approval workflow via Telegram.

### Phase 3: iOS App (Days 29–49)

**Goal**: Primary interface shifts to custom iOS app.

- Backend REST API + WebSocket server on Node 1
- SwiftUI iOS app:
  - Chat interface with rich message types (cards, images, action buttons)
  - Approval flow (approve/edit/reject)
  - Push notifications (APNs) with actionable buttons
  - Smart alarm with calendar-aware wake time
  - TTS morning briefing (AVSpeechSynthesizer)
  - Speech-to-text input (Speech framework)
  - Camera integration (wardrobe photos, receipts)
  - EventKit calendar integration
  - **Apple Contacts framework integration (auto-sync to backend)**
  - **HealthKit integration (sleep, steps, workouts synced to backend)**
  - Home screen widgets (schedule, approvals, health, outfit)
- Apple Watch companion:
  - Notification mirroring + inline approve/reject
  - Complication (next event, pending approvals)
  - Glance view
- Siri Shortcuts integration
- **HomePod Mini: morning briefing audio output via AirPlay**
- **Siri Shortcuts on HomePod: "Hey Siri, TARS [command]"**
- TestFlight deployment

**Exit Criteria**: User wakes up to T.A.R.S. alarm, hears voice briefing through HomePod with outfit and health summary. Manages day via iOS app. "Hey TARS" works at home. Apple Watch shows notifications with approve/reject. Telegram remains as fallback.

### Phase 4: Polish & Optimization (Days 50–65)

**Goal**: Production-grade reliability and refined user experience.

- Coding/DevOps agent pipeline (Node 2 Docker execution)
- Research agent
- Interview prep agent (triggered from job tracker)
- Shopping advisor ("what to buy at Tanger Outlets")
- Token usage optimization
- Comprehensive error handling and graceful degradation
- Logging and audit trail completion
- Backup and recovery procedures
- Performance tuning (response times, briefing generation)
- iOS app UX refinements based on daily usage
- Documentation (maintenance guide, troubleshooting, CLAUDE.md)

### Phase 5: Advanced Features (Ongoing)

- Voice wake-word exploration
- Location-based reminders and suggestions
- Meal planning agent
- Fitness tracking integration (Apple Health)
- Bill/subscription tracker
- Travel planning agent
- Meeting prep agent
- Network maintenance agent (relationship tracking)
- Voice journal
- Personal wiki / knowledge base
- ElevenLabs custom voice

---

## 16. Ideas Backlog (Future Features)

These ideas were brainstormed and deemed valuable but deferred beyond the initial 3-month build. They are ordered by estimated impact.

### 16.1 High Impact

| Feature | Description | AI Model |
|---------|------------|----------|
| **Meal Planning Agent** | Weekly meal planning based on dietary goals, budget, fridge contents (photo → Gemini Vision). Auto-generates grocery list in Notion. | Gemini Vision + Pro |
| **Meeting Prep Agent** | 15 min before meetings, sends brief: who, what you last discussed, suggested talking points, pending deliverables. | Gemini Pro + Claude |
| **Network Maintenance Agent** | Tracks last-contact dates with important people. "You haven't emailed Prof. Liang in 45 days." Suggests check-ins. | Gemini Flash |
| **Bill & Subscription Tracker** | Catalogs recurring charges from Plaid transaction data. Monthly spending summary. Alerts on price increases. (Builds on Finance Tracking Agent.) | Gemini Flash |

*Note: Fitness Tracker has been moved to in-scope (v1.0) as the Health & Fitness Agent (Section 7.2.13).*

### 16.2 Medium Impact

| Feature | Description | AI Model |
|---------|------------|----------|
| **Travel Agent** | "3 days in NYC, $1500 budget" → researches flights, hotels, itinerary. Creates calendar events + packing list. | Gemini Pro + Claude |
| **Learning Agent** | Study plans, spaced repetition, daily practice scheduling. Quizzes via app. | Gemini (routine) + Claude (complex) |
| **Conference/Event Scanner** | Scans for relevant conferences, paper deadlines, hackathons. "AAAI deadline in 3 weeks — interested?" | Gemini Flash |
| **Email Negotiator** | Job offer negotiation assistance. Market data + strategic response drafting. | Claude |
| **Code Review Buddy** | Auto-reviews PRs when pushed. Gemini for style, Claude for architecture. | Gemini + Claude |

### 16.3 Experimental

| Feature | Description | AI Model |
|---------|------------|----------|
| **Voice Journal** | Nightly 2-min voice memo → transcribed, extracted, searchable life log. "What was I working on in April?" | Gemini Flash |
| **Decision Advisor** | Structured analysis of big decisions: pros/cons, second-order effects, blind spots. | Claude |
| **Personal Wiki** | Auto-maintained knowledge base about the user — preferences, decisions, history. Digital twin context. | Gemini + Claude |
| **Smart Home Integration** | Lights, thermostat, smart locks coordinated with schedule. | Local + Gemini |
| **Local LLM Fallback** | Run small open-source model locally for offline / zero-cost basic queries. | Local (Llama/Mistral) |

---

## 17. Open Questions & Risks

### 17.1 Open Questions

| # | Question | Impact | Status |
|---|----------|--------|--------|
| OQ-01 | Can `cloudflared` be installed on the Wooster server? | Critical for Loki/Grafana access | Tasin to ask IT |
| OQ-02 | Does Wooster IT have spare RAM/SSD? | Cost savings | Check next week |
| OQ-03 | Is Grafana/Loki fully configured? What endpoints work? | Monitoring scope | Partially done |
| OQ-04 | What TTS voice for T.A.R.S.? | UX quality | Start with iOS built-in, explore ElevenLabs later |
| OQ-05 | Exact Claude Max 5x weekly limits in practice? | Capacity planning | Monitor during Phase 1 |
| OQ-06 | Best job board access methods (APIs vs. scrapers)? | Job search agent feasibility | Research during Phase 2 |
| OQ-07 | iCloud CalDAV push notifications vs. polling? | Calendar sync latency | Research during Phase 1 |
| OQ-08 | Gemini API rate limits on free/paid tiers? | Capacity planning | Research during setup |
| OQ-09 | User's job search salary range and location hard constraints? | Job search config | Tasin to define |

### 17.2 Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| R-01 | Claude Max 5x budget insufficient | Medium | High | Multi-model architecture (Gemini offloads 60–70%). Monitor usage. Upgrade to 20x or API billing if needed. |
| R-02 | iCloud CalDAV limitations | Low | High | App-specific password well-documented. Fallback: ICS feed. iOS app uses EventKit directly. |
| R-03 | T-Mobile internet instability | Medium | Medium | Tailscale auto-reconnects. Cloudflare Tunnel reconnects. Briefing caches locally. |
| R-04 | Hardware failure (Z2 Minis ~8 years old) | Medium | High | Daily backups. System runs in degraded mode on single node. |
| R-05 | iOS app development takes longer | High | Medium | Telegram bot is fully functional fallback. iOS delivered incrementally. |
| R-06 | Grafana/Loki not accessible from home | Medium | Low | Only affects monitoring. Core assistant unaffected. Add later. |
| R-07 | Job board scraping blocked or rate-limited | Medium | Medium | Multiple sources for redundancy. Adapter pattern allows source swapping. Some paid APIs as backup. |
| R-08 | Scope creep | High | High | Strict phase gates. No Phase N+1 until Phase N exit criteria met. Ideas backlog captures future work without derailing current phase. |
| R-09 | Gemini API pricing changes | Low | Medium | Token-efficient design. Can shift some Gemini tasks to Claude or local processing. |
| R-10 | Wardrobe catalog accuracy (Gemini Vision) | Medium | Low | User corrects misclassifications. Feedback loop improves over time. Low-risk feature — wrong outfit suggestion is inconvenient, not harmful. |

---

## 18. Glossary

| Term | Definition |
|------|-----------|
| **T.A.R.S.** | Tasin's Autonomous Resource System — the assistant being built |
| **Agent** | An AI model invocation spawned to perform a specific task. Receives context, returns output, terminates. |
| **Orchestrator** | Always-running Python daemon on Node 1 that routes tasks, manages state, and coordinates agents. |
| **Model Router** | Logic layer that decides which AI model (Claude, Gemini Flash, Gemini Pro, Gemini Vision) handles each task. |
| **Approval Queue** | System for buffering proposed actions until user explicitly approves or rejects. |
| **Briefing** | Structured summary of the user's day generated from all integrations. |
| **CalDAV** | Open protocol for calendar access (RFC 4791). Used for iCloud Calendar server-side. |
| **EventKit** | Apple's native framework for calendar/reminder access on iOS/macOS. |
| **CGNAT** | Carrier-Grade NAT. T-Mobile's network architecture preventing inbound connections. |
| **ChromaDB** | Open-source vector database for semantic search and memory. |
| **Cloudflare Tunnel** | Service creating outbound-only encrypted tunnels. Enables access without static IPs. |
| **Claude Code** | Anthropic's terminal-based AI coding assistant. Used headless as the complex intelligence layer. |
| **CLAUDE.md** | Project context file that Claude Code reads for codebase understanding. |
| **Gemini** | Google's AI model family. Used for routine tasks (Flash), moderate tasks (Pro), and vision tasks. |
| **Graceful Degradation** | System continues with reduced capabilities when a component fails. |
| **Loki** | Log aggregation system by Grafana Labs. Queries AtlasDesk server logs. |
| **Node 1 / Brain** | Primary Z2 Mini — orchestrator, API server, state database. |
| **Node 2 / Muscle** | Secondary Z2 Mini — Docker, Redis, execution workloads, storage. |
| **Risk Tier** | Action classification: Autonomous, Approval Required, or Escalation. |
| **Tailscale** | WireGuard-based mesh VPN connecting devices across NATs. |
| **TestFlight** | Apple's beta testing platform for iOS app distribution. |
| **Three-Tier Filtering** | Job search pipeline: Flash (bulk screen) → Pro (evaluate) → Claude (deep review). |
| **TTS** | Text-to-Speech. Converts T.A.R.S. text responses to spoken audio. |
| **Wardrobe Catalog** | Database of user's clothing items with metadata, built from photos via Gemini Vision. |
| **Porcupine** | Wake word detection engine by Picovoice. Runs locally on CPU, listens for custom wake words. |
| **Plaid** | Financial data API providing read-only access to bank transaction history. |
| **HealthKit** | Apple's framework for reading health and fitness data (sleep, steps, workouts) on iOS. |
| **EventKit** | Apple's framework for calendar and reminder access on iOS. |
| **AirPlay** | Apple's protocol for wireless audio/video streaming. Used to play T.A.R.S. voice through HomePod. |
| **HomePod Mini** | Apple's smart speaker. Provides room-based audio output and Siri bridge for T.A.R.S. |
| **GHCR** | GitHub Container Registry. Hosts Docker images built by CI/CD for deployment to servers. |

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-03-08 | Tasin & Claude | Initial requirements document |
| 2.0 | 2026-03-09 | Tasin & Claude | Major update: renamed to T.A.R.S., added multi-model AI architecture (Claude + Gemini), added Job Search Agent with three-tier filtering pipeline, added Fashion/Outfit Agent with wardrobe catalog system, added Shopping Advisor, updated hardware specs (16GB RAM, 238.5GB NVMe verified), added iCloud Calendar dual approach (EventKit + CalDAV), added Ideas Backlog section (15+ future features), added Gemini Vision integration (wardrobe, receipts, fridge), updated token budget strategy for multi-model, added AI usage monitoring requirement, expanded phased delivery plan, added job board integration requirements, updated risk matrix for multi-model architecture |
| 2.1 | 2026-03-09 | Tasin & Claude | **FINAL LOCKED VERSION.** Added HomePod Mini integration (voice-in-the-room, AirPlay briefing). Added custom wake word "Hey TARS" via USB mic + Porcupine on Node 1. Added Apple Contacts framework integration for email classifier enrichment. Added HealthKit integration (sleep, steps, workouts in briefing). Added Finance Tracking Agent via Plaid (read-only transaction access). Revised HC-04 to allow passive financial observation. Added development workflow (Mac → GitHub → GHCR → Docker deploy). Added self-deploy command. Added Siri bridge for mobile voice commands. Updated all phases, dependencies, scope, and security requirements. |

---

*This document is the authoritative and LOCKED requirements specification for T.A.R.S. v1.0. No further requirements changes until Phase 1 exit criteria are met. All design and implementation decisions must trace back to requirements defined herein. The next deliverable is the Design Document, which will detail technical architecture, API contracts, database schemas, file structures, and implementation specifics that Claude Code will execute against.*
