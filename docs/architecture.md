# Architecture

## Hardware (Verified 2026-04-21)

Both nodes — **identical** HP Z2 Mini G3:
- Intel Core i7-6700 (Skylake desktop, 4C/8T, 3.4GHz base, 4.0GHz turbo, 65W TDP)
- 16GB DDR4-2133 (2× 8GB SODIMM, max 64GB but **no upgrade — constraint thesis**)
- NVIDIA Quadro M620 Mobile (Maxwell, 2GB GDDR5, 384 CUDA cores)
- Samsung NVMe 256GB (Node 1: 77GB free; Node 2: 58GB free)
- Ubuntu 24.04.4 LTS, kernel 6.8
- Docker 29.3.0 + Compose v5.1.0
- Tailscale-meshed (`tars-brain` 100.94.4.103, `tars-muscle` 100.119.114.125)
- CPU flags: `aes avx avx2 bmi f16c fma sse4_2` — **no AVX-512**

llama.cpp build flags: `-DLLAMA_AVX2=ON -DLLAMA_FMA=ON -DLLAMA_F16C=ON -DLLAMA_CUDA=ON` (CUDA for Quadro M620 / Whisper acceleration).

## Topology

```
CLIENT LAYER
  iOS App (SwiftUI) ─── REST/WS ──┐
  Apple Watch ─── WatchConnectivity ──┤
  Telegram Bot ─── HTTP Long Poll ────┤
  Siri Shortcuts ─── App Intents ─────┤
  HomePod Mini ─── AirPlay ───────────┤
  Mac menubar (SwiftUI) ──────────────┤
  Hey TARS (USB mic on Node 1) ──────┤
                                      ▼
NODE 1 — "BRAIN" (tars-brain, 100.94.4.103)
  FastAPI (REST + WebSocket + APNs push)
  Orchestrator (asyncio + uvloop)
    ├── IntentClassifier        rule-based, zero tokens
    ├── SignalDetector          escalation triggers
    ├── ModelRouter             local-first, escalate on signal
    ├── ContextBuilder          wiki retrieval + scoped context
    ├── ToneStateMachine        playful | neutral | serious | urgent
    ├── AutonomyBudget          5-class action authorization
    ├── ApprovalManager         Tier 1/2/3 (HC-01)
    ├── TriggerEngine           subscribes world events, fires agents
    └── Scheduler               APScheduler cron
  PostgreSQL 16                  state DB
  Redis 7                        queue + pub/sub + cache  (moved from Node 2)
  Qdrant 1.11+                   tasin_wiki, wardrobe, email_threads  (moved from Node 2)
  Prometheus + Grafana           metrics + dashboards
  Porcupine Wake Word Daemon     USB mic → Whisper STT (GPU-accelerated)
  Telegram Bot Gateway
  Sensor Collectors              location, HealthKit, mac, spotify, git, weather
  Integration Layer              CalDAV, Gmail, GitHub, Notion, OpenWeather, Teller, Grafana/Loki
  Cloudflare Tunnel (×2)         private API + public dashboard
  Tailscale                      mesh VPN
      │
      │ httpx → llama endpoints
      │ Redis sorted-set queue + pub/sub (Redis on Node 1, worker on Node 2)
      ▼
NODE 2 — "MUSCLE" (tars-muscle, 100.119.114.125)
  llama.cpp L0 Reflex            port 8001 — Qwen3-1.7B-tars (LoRA persona) Q4_K_M
  llama.cpp L1 Brain             port 8002 — Qwen3-8B-Instruct-2507 Q4_K_M
  llama.cpp Embeddings           port 8003 — Qwen3-Embedding-0.6B
  Job Worker Daemon              Redis job consumer (consumes from Node 1 Redis)
  Docker Engine                  sandboxed executor containers
  Persistent Volumes             /data/{wardrobe,outputs,repos,logs,models}
```

## Data Flow — User Message End-to-End

```
Telegram/iOS/Voice → /api/v1/messages
  ↓
save Conversation + Message rows (Postgres)
  ↓
IntentClassifier (regex, zero tokens) → Intent
  ↓
SignalDetector → set[EscalationSignal]
  ↓
ModelRouter → ModelRoute(tier, node, mcp_profile)
  ↓
ToneStateMachine → Tone
  ↓
ContextBuilder
  ├── parallel fetch: calendar, emails, weather, health
  ├── retrieve top-8 wiki chunks via Qdrant
  └── assemble AgentContext
  ↓
Agent.execute(context)   [or fallback: direct model call]
  ↓
UsageTracker.track()     model_usage row
  ↓
if result.autonomy_class in {WRITE_WORLD, WRITE_INFRA}:
    ApprovalManager.create()   Tier 1/2/3
    push to iOS APNs + Watch + Telegram + WS
    wait for decision
  ↓
ResponseFormatter → dict matching TARSResponse schema
  ↓
save assistant Message row + AuditLog row
  ↓
publish sanitized event to tars:public:events    (public dashboard SSE)
  ↓
return response to caller
```

## Distributed Job Flow (Node 1 → Node 2 → Node 1)

```
Node 1 Agent → JobQueue.enqueue(job_type, payload, priority)
  ↓ Redis ZADD tars:jobs:queue (score = priority + epoch)
  ↓ Node 1 subscribes pub/sub tars:jobs:results:{job_id}
  ↓
Node 2 JobProcessor.poll → ZPOPMIN tars:jobs:queue
  ↓ dispatch to executor (code / research / diagnostic / job_scraper / image)
  ↓ Docker sandbox container for code/research
  ↓
Node 2 publish → tars:jobs:results:{job_id}
  ↓ Node 1 receives
  ↓ return result to agent
```

## Inter-Node Communication

- Gigabit Ethernet + Tailscale mesh
- Subnet 10.0.1.0/24
- Redis sorted-set queue: `tars:jobs:queue` (authoritative)
- Pub/sub results: `tars:jobs:results:{job_id}` (per-job ephemeral)
- World events: `tars:world:<source>` (sensors → TriggerEngine)
- Public events: `tars:public:events` (sanitized → SSE)
- Local inference: httpx from Node 1 → Node 2 port 8001/8002/8003

## External Access

- **Cloudflare Tunnel A**: outbound-only, private FastAPI endpoints (iOS, Telegram callbacks)
- **Cloudflare Tunnel B**: public `tars.<domain>` dashboard, read-only sanitized
- Tailscale: device mesh (iPhone, Mac, Apple Watch)

## Storage

- **Postgres 16** (Node 1): all state — see `docs/db_conventions.md`
- **Redis 7** (Node 2): queue + pub/sub + cache
- **Qdrant 1.11+** (Node 2): vectors (tasin_wiki, wardrobe, email_threads)
- **Filesystem** (Node 2): `/data/wardrobe` (images), `/data/outputs` (drafts), `/data/repos` (code sandboxes), `/data/logs`, `/data/models` (GGUFs)

## Resource Accounting (16GB per node)

### Node 1 — All State + Orchestrator
| Service | RAM | Notes |
|---|---|---|
| FastAPI + orchestrator | 1.0GB | uvicorn + asyncio |
| Postgres 16 | 2.0GB | shared_buffers tuned to 512MB |
| Redis 7 | 2.0GB | maxmemory 1.5GB, allkeys-lru |
| Qdrant 1.11+ | 2.0GB | typical w/ 100k vectors |
| APScheduler | 0.3GB | |
| Sensor daemons | 0.3GB | |
| Wake word + Whisper (GPU) | 0.5GB CPU + 0.5GB VRAM | Whisper-small.en on Quadro M620 |
| Prometheus + Grafana | 0.5GB | |
| OS + buffers | 2.0GB | |
| **Total** | **~10.6GB** | Free: ~5GB headroom |

### Node 2 — Pure Inference
| Service | RAM | VRAM | Notes |
|---|---|---|---|
| llama-l0 (Qwen3-1.7B Q4_K_M) | 1.2GB | optional `-ngl 8` 0.8GB | always hot, persona-LoRA |
| llama-l1 (Qwen3-8B Q4_K_M) | 5.0GB | optional `-ngl 12` 1.0GB | always hot |
| llama-embed (Qwen3-Embedding-0.6B) | 0.6GB | — | always hot |
| Worker daemon | 0.5GB | — | |
| Docker sandboxes | 2.0GB | — | burst peak (code/research) |
| OS + buffers | 2.0GB | — | |
| **Total** | **~11.3GB** | ~1.8GB / 2GB | Free: ~4.7GB headroom |

**No swap path for inference.** Models always RAM-resident (mmap = page-cache backed but pinnable). Swap = latency death.

### Stretch Bench (Phase 2 spike)
Optional: Qwen3-30B-A3B Q4_K_M (18GB) **mmap'd from NVMe** — exceeds 16GB, MoE pages cold experts on demand. Estimated 4-8 tok/s sustained. Bench-only; not default.

## Constraint Thesis

Two HP Z2 Mini G3. Intel i7-6700 (Skylake desktop, 2015). **16GB RAM each.** Quadro M620 mobile GPU (2GB VRAM). 256GB SSD. 65W TDP per node. Used market value ~$300-350 each.

**Total compute budget for full personal AI stack: under $700 of decade-old silicon.**

No hardware upgrades — constraint is the engineering thesis.

See LinkedIn writeup `docs/writeup.md`.
