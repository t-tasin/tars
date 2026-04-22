# T.A.R.S. Feature Requirements & Status

> **Living document.** Every PR must update at least one feature row.
>
> **States:** `PLANNED` → `IN_PROGRESS` → `BUILT` → `TESTED` → `SHIPPED`
>
> **Last updated:** 2026-04-22 (Phase 1 session — queue unified)

---

## Status Legend

- `PLANNED` — approved, not yet started
- `IN_PROGRESS` — branch exists, actively being built
- `BUILT` — code merged to main, basic happy-path tested
- `TESTED` — full test coverage (unit + integration), eval suite passing if applicable, 24h+ soak
- `SHIPPED` — deployed to both nodes, running stable 7+ days, metric captured

## How to Update

After any change on a feature:
1. Update `Status`
2. Add PR link to `Evidence` (format: `#123`, `abc123`, or eval suite name)
3. Update `Last Touched` date
4. If blocked, add note to `Blockers`
5. If newly `SHIPPED`, add launch metric to `Notes`

Claude Code: this doc is the source of truth. Check before picking up work. Update before marking a task done. See `CLAUDE.md §11 Development Workflow`.

---

## Phase 0 — Pre-flight

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P0-01 | Baseline test suite green (backend 991 + worker 20) | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | Gemini model asserts bumped 2.0→2.5, telegram sys.modules shims removed, test_telegram_handlers patches now match src-prefixed imports |
| P0-02 | Create `docs/journal/` daily log convention | PLANNED | Tasin | — | 2026-04-21 | — | First entry will be 2026-04-21.md |
| P0-03 | Coverage report baseline | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | Backend 66% (9322 stmts / 3202 miss), worker 36% (601 / 386). htmlcov/ gitignored. |
| P0-04 | Prometheus + Grafana on Node 1 | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | prometheus v2.55 @9090 scrapes host.docker.internal:8000/metrics every 15s; grafana 11.3 @3000 w/ Prometheus datasource auto-provisioned. Node 1 smoke pending. |
| P0-05 | `fastapi-instrumentator` wired | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | prometheus-fastapi-instrumentator>=7.0 added to backend/pyproject.toml; /metrics exposed from src/main.py before router mount; 2 route-inspection tests (no lifespan). Full suite 993 passed. |
| P0-06 | Power meter reading capture | PLANNED | Tasin | — | 2026-04-21 | — | Need Kill-A-Watt or similar |
| P0-07 | Branch protection rules on main | PLANNED | Tasin | — | 2026-04-21 | — | Require PR + CI green + 1 review |
| P0-08 | GitHub Projects board | PLANNED | Tasin | — | 2026-04-21 | — | Columns per phase |
| P0-09 | Archive old CLAUDE.md, adopt new | PLANNED | Tasin | — | 2026-04-21 | — | mv CLAUDE.md.new CLAUDE.md |
| P0-10 | Delete ChromaDB stack, worker references | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | Stripped backend config + health_monitor + api/health, worker config, node2 compose, env templates. `grep -rn chroma` clean across backend/worker/deploy/env.example. |
| P0-11 | Run tars-probe on both nodes, capture baseline | BUILT | Tasin | logs collected 2026-04-21 | 2026-04-21 | — | i7-6700, 16GB DDR4-2133, Quadro M620 2GB, NVMe 256GB |
| P0-12 | Update architecture/model_tiers/tech_stack docs to match real hardware | BUILT | Claude | this session | 2026-04-21 | depends P0-11 | 16GB not 32GB; i7-6700 not 7700T; GPU exists |
| P0-13 | Move Redis to Node 1 in deploy/node1/docker-compose.yml | BUILT | Claude | phase-0-green branch | 2026-04-21 | Qdrant still deferred to Phase 3 | redis:7-alpine service added to node1 compose; removed from node2 compose (worker now expects external REDIS_URL). Backend default redis_url → redis://localhost:6379/0 (colocated). Both composes validate. |
| P0-14 | Install lm-sensors on both nodes, baseline thermals | PLANNED | Tasin | — | 2026-04-21 | — | `sudo apt install lm-sensors && sudo sensors-detect --auto` |
| P0-15 | Install CUDA toolkit on Node 2 (for Quadro M620) | TESTED | Tasin | runfile 12.2.2 + driver 535.288.01 + g++-12 host; sm_50 smoke kernel printed `gpu alive` 2026-04-22 | 2026-04-22 | — | CUDA 12.2 @ /usr/local/cuda-12.2; driver held; `CUDAHOSTCXX=/usr/bin/g++-12` in ~/.bashrc; llama.cpp/whisper.cpp builds must pass `-DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 -DCMAKE_CUDA_ARCHITECTURES=50` |
| P0-16 | Storage cleanup Node 2 (36GB used vs Node 1 17GB) | PLANNED | Tasin | — | 2026-04-21 | — | `docker system prune -af` after audit |
| P0-17 | Wire Node 2 worker to point at Node 1 Redis (100.94.4.103:6379) | BUILT | Claude | phase-0-green branch | 2026-04-21 | — | worker/src/config.py redis_url default → redis://100.94.4.103:6379/0 (Node 1 tailscale IP). node2 compose already resolves REDIS_URL via env override. 20 worker tests pass. |

---

## Phase 1 — Foundation Fix

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P1-01 | `integrations/job_queue.py` w/ JobQueue class | BUILT | Claude | bd4937d, test_job_queue.py 10/10 | 2026-04-22 | — | ZADD on tars:jobs:queue, pubsub await on tars:jobs:results, subscribe-before-enqueue |
| P1-02 | Refactor `agents/coding.py` to use JobQueue | BUILT | Claude | bd4937d, test_coding_agent.py 8/8 | 2026-04-22 | — | `_QUEUE_KEY="tars:jobs:code"` removed; result read from message["result"] |
| P1-03 | Refactor `agents/fashion.py` to use JobQueue | BUILT | Claude | bd4937d, test_fashion.py::TestFashionImageDispatch 2/2 | 2026-04-22 | — | LPUSH to "tars:jobs" ripped; worker gained `save_image` task_type for image persistence |
| P1-04 | E2E test: distributed job round-trip | BUILT | Claude | bd4937d, test_queue_e2e.py 3/3 | 2026-04-22 | — | fakeredis.FakeServer shared between backend JobQueue + worker JobProcessor; covers happy path, unknown-type failure, priority ordering |
| P1-05 | Remove ChromaDB from `backend/src` | BUILT | Claude | bd4937d (audit clean after P0-10), env.example + README cleanup | 2026-04-22 | — | No ChromaDB imports in backend/src; `CHROMA_AUTH_TOKEN` stripped from .env.example |
| P1-06 | Remove ChromaDB from `deploy/node2/docker-compose.yml` | BUILT | Claude | bd4937d (audit clean after P0-10) | 2026-04-22 | — | deploy/ clean; Qdrant added in P3 |
| P1-07 | Tag release `v0.1-distributed-real` | PLANNED | Tasin | — | 2026-04-22 | depends P1-01..6 PR #2 merge | Tasin cuts tag after PR #2 lands on main |

---

## Phase 2 — Local Inference Tier

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P2-01 | llama.cpp built on Node 2 with AVX2+FMA | PLANNED | Tasin | — | 2026-04-21 | — | `make -j8 LLAMA_AVX2=1 LLAMA_FMA=1` |
| P2-02 | Download Qwen3-1.7B-Instruct Q4_K_M GGUF (L0) | PLANNED | Tasin | — | 2026-04-21 | — | ~1.2GB |
| P2-03 | Download Qwen3-8B-Instruct-2507 Q4_K_M GGUF (L1) | PLANNED | Tasin | — | 2026-04-21 | — | ~5GB |
| P2-04 | systemd unit `llama-l0` on port 8001 (Qwen3-1.7B) | PLANNED | Tasin | — | 2026-04-21 | depends P2-01,2 | Always-on, auto-restart |
| P2-05 | systemd unit `llama-l1` on port 8002 (Qwen3-8B) | PLANNED | Tasin | — | 2026-04-21 | depends P2-01,3 | Always-on |
| P2-05a | (stretch) bench Qwen3-30B-A3B Q4_K_M mmap'd | PLANNED | Tasin | — | 2026-04-21 | depends P2-04..5 | Only promote if ≥6 tok/s sustained |
| P2-05b | systemd unit `llama-embed` on port 8003 (Qwen3-Embedding-0.6B) | PLANNED | Tasin | — | 2026-04-21 | depends P2-01 | ~0.6GB |
| P2-05c | Whisper.cpp w/ CUDA backend on Quadro M620 | PLANNED | Tasin | — | 2026-04-21 | depends P0-15 | Whisper-small.en, ~500MB VRAM |
| P2-05d | 3-way L1 bench: Qwen3-8B vs Gemma 4 12B vs Qwen3-30B-A3B-mmap | PLANNED | Tasin | — | 2026-04-21 | depends P2-04,5 | 50 real prompts; measure tok/s + quality + tool-use accuracy; pick L1 winner empirically |
| P2-05e | Pull Gemma 4 12B Q4_K_M GGUF (candidate) | PLANNED | Tasin | — | 2026-04-21 | — | ~7.5GB |
| P2-06 | `backend/src/models/local_client.py` | PLANNED | Claude | — | 2026-04-21 | — | OpenAI-compat httpx |
| P2-07 | Add `LOCAL_REFLEX`/`LOCAL_BRAIN`/`LOCAL_EMBED` to `ModelName` | PLANNED | Claude | — | 2026-04-21 | — | shared/constants.py |
| P2-08 | `orchestrator/signal_detector.py` | PLANNED | Claude | — | 2026-04-21 | — | `EscalationSignal` enum + detect() |
| P2-09 | Rewrite `model_router.py` around signals | PLANNED | Claude | — | 2026-04-21 | depends P2-07..8 | Local default, escalate on signal |
| P2-10 | Feature flag `FEATURE_NEW_ROUTER` | PLANNED | Claude | — | 2026-04-21 | depends P2-09 | Dark ship 1 week |
| P2-11 | Fallback chain extended (local → gemini → claude) | PLANNED | Claude | — | 2026-04-21 | depends P2-09 | Engine update |
| P2-12 | L2 self-escalation JSON protocol | PLANNED | Claude | — | 2026-04-21 | — | System prompt + engine detection |
| P2-13 | Router unit tests (30+) | PLANNED | Claude | — | 2026-04-21 | depends P2-09 | Every signal combo |
| P2-14 | Cost/tier tracking in `model_usage` | PLANNED | Claude | — | 2026-04-21 | depends P2-06 | Update usage_tracker |
| P2-15 | Tag release `v0.2-local-first` | PLANNED | Tasin | — | 2026-04-21 | depends P2-01..14 | — |

---

## Phase 3 — Sovereign Data Layer

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P3-01 | Qdrant in docker-compose Node 2 | PLANNED | Claude | — | 2026-04-21 | depends P1-06 | v1.11+ |
| P3-02 | Qwen3-Embedding-0.6B llama.cpp on port 8003 | PLANNED | Tasin | — | 2026-04-21 | depends P2-01 | Embedding mode |
| P3-03 | `integrations/qdrant_client.py` | PLANNED | Claude | — | 2026-04-21 | depends P3-01 | async qdrant-client |
| P3-04 | `models/embedding_client.py` | PLANNED | Claude | — | 2026-04-21 | depends P3-02 | Local embed via httpx |
| P3-05 | `wiki/` directory scaffolded | PLANNED | Tasin | — | 2026-04-21 | — | 20 hand-seeded files |
| P3-06 | `wiki/identity/tars_persona.md` v1 | PLANNED | Tasin | — | 2026-04-21 | depends P3-05 | Voice guide from §5 |
| P3-07 | `sensors/wiki_watcher.py` (watchdog) | PLANNED | Claude | — | 2026-04-21 | depends P3-03..4 | Auto-re-embed on change |
| P3-08 | Qdrant collection `tasin_wiki` | PLANNED | Claude | — | 2026-04-21 | depends P3-03,7 | Ingest seed files |
| P3-09 | `context_builder.retrieve_wiki(query, k=8)` | PLANNED | Claude | — | 2026-04-21 | depends P3-08 | Every AgentContext gets chunks |
| P3-10 | Postgres `wiki_proposals` + `wiki_index` tables | PLANNED | Claude | — | 2026-04-21 | — | Alembic migration |
| P3-11 | `web/` Next.js 15 scaffold | PLANNED | Claude | — | 2026-04-21 | — | App Router + Tailwind |
| P3-12 | Vercel deploy `tars.<domain>` | PLANNED | Tasin | — | 2026-04-21 | depends P3-11 | DNS + tunnel |
| P3-13 | `/api/v1/public/stream` SSE endpoint | PLANNED | Claude | — | 2026-04-21 | — | Event sanitizer + auth |
| P3-14 | Sanitizer fuzz tests | PLANNED | Claude | — | 2026-04-21 | depends P3-13 | 10k random events, no PII leak |
| P3-15 | Dashboard MVP: feed + today's numbers | PLANNED | Claude | — | 2026-04-21 | depends P3-11..13 | Text dashboard, 3D later |
| P3-16 | Tag release `v0.3-sovereign-memory` | PLANNED | Tasin | — | 2026-04-21 | depends P3-01..15 | — |

---

## Phase 4 — Autonomy Engine

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P4-01 | `sensors/base.py` BaseSensor abstract | PLANNED | Claude | — | 2026-04-21 | — | publish to `tars:world:*` |
| P4-02 | Location sensor via iOS Shortcut POST | PLANNED | Tasin+Claude | — | 2026-04-21 | depends P4-01 | /api/v1/sensors/location |
| P4-03 | Mac activity sensor (Hammerspoon) | PLANNED | Tasin+Claude | — | 2026-04-21 | depends P4-01 | hash titles, no content |
| P4-04 | Spotify sensor via spotipy | PLANNED | Claude | — | 2026-04-21 | depends P4-01 | 30s cadence |
| P4-05 | Git activity sensor | PLANNED | Claude | — | 2026-04-21 | depends P4-01 | cron 5min |
| P4-06 | HealthKit sensor migration to pipeline | PLANNED | Claude | — | 2026-04-21 | depends P4-01 | already ingested, wire to world_state |
| P4-07 | Weather sensor migration | PLANNED | Claude | — | 2026-04-21 | depends P4-01 | 15min cadence |
| P4-08 | Network presence (Tailscale API) | PLANNED | Claude | — | 2026-04-21 | depends P4-01 | — |
| P4-09 | Postgres `world_state` monthly-partitioned | PLANNED | Claude | — | 2026-04-21 | — | Alembic w/ partman |
| P4-10 | `orchestrator/trigger_engine.py` | PLANNED | Claude | — | 2026-04-21 | depends P4-01,9 | Pub/sub subscriber + pattern matcher |
| P4-11 | `shared/constants.py` AutonomyClass enum | PLANNED | Claude | — | 2026-04-21 | — | READ/WRITE_LOCAL/WRITE_SELF/WRITE_WORLD/WRITE_INFRA |
| P4-12 | AgentResult.autonomy_class required field | PLANNED | Claude | — | 2026-04-21 | depends P4-11 | Test fails if missing |
| P4-13 | `orchestrator/autonomy_budget.py` | PLANNED | Claude | — | 2026-04-21 | depends P4-11..12 | Daily cap tracker |
| P4-14 | Postgres `autonomy_budget` table | PLANNED | Claude | — | 2026-04-21 | depends P4-13 | Alembic |
| P4-15 | Trigger: evening_wind_down | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | location + time + calendar |
| P4-16 | Trigger: meeting_prep | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | 30min before, if no prep doc |
| P4-17 | Trigger: commit_streak_remind | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | 24h no commits on active project |
| P4-18 | Trigger: sleep_recovery | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | HealthKit score <70 |
| P4-19 | Trigger: unused_clothes_remind | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | wardrobe_outfits last_worn >90d |
| P4-20 | Trigger: email_batch | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | inbox >20 unread |
| P4-21 | Trigger: subscription_audit | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | recurring transaction pattern |
| P4-22 | Trigger: atlasdesk_watchdog | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | health_monitor degraded → diagnose |
| P4-23 | Trigger: focus_mode | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | Xcode/IDE >2h → mute non-critical |
| P4-24 | Trigger: arrived_home | PLANNED | Claude | — | 2026-04-21 | depends P4-10 | location + dark + greeting |
| P4-25 | Tag release `v0.4-proactive` | PLANNED | Tasin | — | 2026-04-21 | depends P4-01..24 | — |

---

## Phase 5 — Persona + LoRA

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P5-01 | tars_persona.md voice guide finalized | PLANNED | Tasin | — | 2026-04-21 | — | Sample dialog pairs |
| P5-02 | Persona prefix injection in LocalClient | PLANNED | Claude | — | 2026-04-21 | depends P5-01 | Load once, cache |
| P5-03 | Persona prefix injection in GeminiClient | PLANNED | Claude | — | 2026-04-21 | depends P5-01 | — |
| P5-04 | Persona prefix injection in ClaudeSpawner | PLANNED | Claude | — | 2026-04-21 | depends P5-01 | — |
| P5-05 | `orchestrator/tone_state_machine.py` | PLANNED | Claude | — | 2026-04-21 | — | `Tone` enum + select_tone() |
| P5-06 | Tone postfix injection all three clients | PLANNED | Claude | — | 2026-04-21 | depends P5-05 | — |
| P5-07 | Gmail sent export (last 3y) | PLANNED | Tasin | — | 2026-04-21 | — | Takeout, clean, dedupe |
| P5-08 | Hand-label 500 persona dialog pairs | PLANNED | Tasin | — | 2026-04-21 | depends P5-01,7 | JSONL format |
| P5-09 | Unsloth LoRA training script | PLANNED | Claude | — | 2026-04-21 | — | scripts/lora_finetune.py |
| P5-10 | Train Qwen3-1.7B-tars-v1 LoRA | PLANNED | Tasin | — | 2026-04-21 | depends P5-08..9 | 3 epochs, free Colab T4 or Mac MLX |
| P5-11 | Export + deploy merged GGUF | PLANNED | Tasin | — | 2026-04-21 | depends P5-10 | Swap into L0 on Node 2 |
| P5-12 | Voice consistency eval suite | PLANNED | Claude | — | 2026-04-21 | — | 200 prompts × 4 tones |
| P5-13 | Claude Opus judge wiring | PLANNED | Claude | — | 2026-04-21 | depends P5-12 | 1-5 scoring |
| P5-14 | Voice eval gate (≥4.2 avg to promote) | PLANNED | Claude | — | 2026-04-21 | depends P5-12..13 | Rollback if regression |
| P5-15 | Tag release `v0.5-persona` | PLANNED | Tasin | — | 2026-04-21 | depends P5-01..14 | — |

---

## Phase 6 — Public Dashboard Polish

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P6-01 | r3f 3D two-node scene | PLANNED | Claude | — | 2026-04-21 | depends P3-15 | Ambient rotation + particles |
| P6-02 | Tailscale link particle flow | PLANNED | Claude | — | 2026-04-21 | depends P6-01 | Per active job |
| P6-03 | LLM-call orb glow on nodes | PLANNED | Claude | — | 2026-04-21 | depends P6-01 | Color per tier |
| P6-04 | Live activity feed panel | PLANNED | Claude | — | 2026-04-21 | depends P3-15 | Terminal aesthetic |
| P6-05 | Model routing treemap | PLANNED | Claude | — | 2026-04-21 | — | Tremor, today's dist |
| P6-06 | Agent ring (14 badges) | PLANNED | Claude | — | 2026-04-21 | — | Pulse on active |
| P6-07 | System pulse panel | PLANNED | Claude | — | 2026-04-21 | depends P0-04 | CPU/RAM/temp/power |
| P6-08 | Today's numbers banner | PLANNED | Claude | — | 2026-04-21 | — | Sticky bottom |
| P6-09 | "Now working on" tile | PLANNED | Claude | — | 2026-04-21 | — | Redacted task |
| P6-10 | Mobile-responsive layout | PLANNED | Claude | — | 2026-04-21 | — | iPhone + iPad + desktop |
| P6-11 | Opening line hero copy | PLANNED | Tasin+Claude | — | 2026-04-21 | — | "I am T.A.R.S..." |
| P6-12 | `/og-image.png` + meta preview tags | PLANNED | Claude | — | 2026-04-21 | — | LinkedIn/Twitter cards |
| P6-13 | Lighthouse perf > 90 | PLANNED | Claude | — | 2026-04-21 | depends P6-01..10 | Optimize bundle |
| P6-14 | About drawer | PLANNED | Claude | — | 2026-04-21 | — | Arch diagram + repo + LinkedIn |
| P6-15 | Tag release `v0.6-public-alive` | PLANNED | Tasin | — | 2026-04-21 | depends P6-01..14 | — |

---

## Phase 7 — Eval Harness

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P7-01 | Postgres `evals` table | PLANNED | Claude | — | 2026-04-21 | — | Alembic |
| P7-02 | Eval suite: intent_classifier | PLANNED | Claude | — | 2026-04-21 | — | 200 labeled messages |
| P7-03 | Eval suite: email_classifier | PLANNED | Claude | — | 2026-04-21 | — | 500 × 4 tiers, F1 |
| P7-04 | Eval suite: briefing | PLANNED | Claude | — | 2026-04-21 | — | 50 golden, Claude judge |
| P7-05 | Eval suite: voice_consistency | PLANNED | Claude | — | 2026-04-21 | depends P5-12 | reuses P5-12..14 |
| P7-06 | Eval suite: routing_precision | PLANNED | Claude | — | 2026-04-21 | — | 100 msgs w/ ideal tier |
| P7-07 | Eval suite: wiki_retrieval | PLANNED | Claude | — | 2026-04-21 | depends P3-08..9 | 50 queries recall@8 |
| P7-08 | Nightly runner `scripts/eval_nightly.py` | PLANNED | Claude | — | 2026-04-21 | depends P7-02..7 | cron 02:00 |
| P7-09 | Grafana eval-health dashboard | PLANNED | Claude | — | 2026-04-21 | depends P7-08 | Pass rate time series |
| P7-10 | Regression alert (>5% drop) | PLANNED | Claude | — | 2026-04-21 | depends P7-08..9 | Apprise + Telegram |
| P7-11 | 7-day green streak | PLANNED | Tasin | — | 2026-04-21 | depends P7-08..10 | observation |
| P7-12 | Tag release `v0.7-measured` | PLANNED | Tasin | — | 2026-04-21 | depends P7-01..11 | — |

---

## Phase 8 — Demo Polish

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P8-01 | USB mic + Picovoice key on Node 1 | PLANNED | Tasin | — | 2026-04-21 | — | hardware |
| P8-02 | Train "Hey TARS" wake word model | PLANNED | Tasin | — | 2026-04-21 | depends P8-01 | Picovoice console |
| P8-03 | Wake-word E2E on real hw | PLANNED | Tasin | — | 2026-04-21 | depends P8-02 | voice → TTS → HomePod |
| P8-04 | Capture 60-sec demo video | PLANNED | Tasin | — | 2026-04-21 | depends P8-03 | unedited single take |
| P8-05 | Mac menubar app | PLANNED | Tasin | — | 2026-04-21 | — | SwiftUI MenuBarExtra |
| P8-06 | iOS Live Activity for tasks | PLANNED | Tasin | — | 2026-04-21 | — | Dynamic Island support |
| P8-07 | Architecture diagram (excalidraw) | PLANNED | Tasin | — | 2026-04-21 | — | Custom icons |
| P8-08 | 30-day metrics snapshot | PLANNED | Tasin | — | 2026-04-21 | depends most | Numbers for blog |
| P8-09 | Public writeup blog post | PLANNED | Tasin | — | 2026-04-21 | depends P8-08 | ~1500 words |
| P8-10 | Tag release `v1.0-ship-ready` | PLANNED | Tasin | — | 2026-04-21 | depends P8-01..13 | — |
| P8-11 | iOS App Intent `AskTARSIntent` | PLANNED | Tasin | — | 2026-04-21 | — | "Hey Siri, TARS" activation |
| P8-12 | iPhone Action Button shortcut "Open TARS + record" | PLANNED | Tasin | — | 2026-04-21 | depends P8-11 | Fast fallback |
| P8-13 | Apple Watch TARS App Intent + double-tap | PLANNED | Tasin | — | 2026-04-21 | depends P8-11 | Wrist activation |

---

## Phase 9 — Ship

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P9-01 | Repo public, README rewritten | PLANNED | Tasin | — | 2026-04-21 | — | No PII |
| P9-02 | `.env.example` clean | PLANNED | Tasin | — | 2026-04-21 | — | Audit |
| P9-03 | LinkedIn post drafted + scheduled | PLANNED | Tasin | — | 2026-04-21 | depends all prior | Tue 9am ET |
| P9-04 | HN Show HN post | PLANNED | Tasin | — | 2026-04-21 | — | Tue 10am ET |
| P9-05 | /r/LocalLLaMA crosspost | PLANNED | Tasin | — | 2026-04-21 | — | — |
| P9-06 | X thread | PLANNED | Tasin | — | 2026-04-21 | — | — |
| P9-07 | DM response SLA | PLANNED | Tasin | — | 2026-04-21 | — | <4h business hours |

---

## Agents Registry (Cross-Phase)

Reference list of all agents + their autonomy class + default tier.

| Agent | Autonomy | Default Tier | Wiki Use | Escalation |
|-------|----------|--------------|----------|------------|
| BriefingAgent | READ | L3 Deep (Gemini Pro) | Heavy | — |
| EmailClassifierAgent | WRITE_LOCAL | L1 Brain (Qwen3-30B-A3B) | Light | Gemini Flash if uncertain |
| CommunicationAgent | WRITE_WORLD | L0 Reflex (Qwen3-4B-tars) | Heavy (relationships + voice) | Claude Sonnet for professors |
| DailyLifeAgent | WRITE_LOCAL | L1 Brain | Medium | — |
| JobSearchAgent | READ | L1 Brain | Heavy | Gemini Flash for eval |
| FashionAgent | WRITE_LOCAL | Gemini Vision | Medium (preferences) | — |
| ProductResearchAgent | READ | L3 Deep | Light | — |
| CodingAgent | WRITE_WORLD | Claude Sonnet | Heavy | Opus for infra |
| ResearchAgent | READ | L2/L3 Gemini | Heavy | — |
| HealthMonitorAgent | READ | Local (no LLM) | None | Claude for diag |
| FinanceAgent | READ | L1 Brain | Light | — |
| HealthFitnessAgent | WRITE_SELF | L1 Brain | Medium | — |
| EODSummaryAgent | WRITE_LOCAL | L3 Deep | Heavy | — |
| WorkoutTrackerAgent | WRITE_LOCAL | L0 Reflex | Light | — |
| **CuratorAgent** (new) | WRITE_LOCAL (proposals only) | L1 Brain | Core mechanism | — |
| **AnomalyDetectorAgent** | READ | L0 Reflex | Light | Claude on detected |

---

## Appendix: How Claude Updates This Doc

After merging any PR that moves feature status:

```
1. Open docs/FEATURES.md
2. Find the relevant row(s) by ID
3. Update Status column
4. Append to Evidence (PR link, SHA, eval suite name)
5. Update Last Touched to today's date (YYYY-MM-DD)
6. If blocked, describe Blocker in plain language
7. If newly SHIPPED, add a metric/observation to Notes
8. Commit this update in the same PR as the work itself
```

If Tasin adds a new feature not listed here:
1. Insert new row in appropriate phase section (or new phase)
2. ID format: `PN-XX` (N = phase number, XX = sequential)
3. Status starts as `PLANNED`
4. Fill Owner, Blockers, Notes
5. Open PR just for the row addition before implementing
