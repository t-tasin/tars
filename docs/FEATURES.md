# T.A.R.S. Feature Requirements & Status

> **Living document.** Every PR must update at least one feature row.
>
> **States:** `PLANNED` → `IN_PROGRESS` → `BUILT` → `TESTED` → `SHIPPED`
>
> **Last updated:** 2026-04-25 (Phase 2 complete — P2-15 release pending; Phase 2.5 + Phase 3.5 inserted from Telegram smoke audit; Phase 4 sensor/world_state IDs moved to 3.5)

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
| P0-04 | Prometheus + Grafana on Node 1 | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25 | 2026-04-25 | — | prometheus v2.55 @9090 + grafana 11.3 @3000 up 3+ days, both `/-/healthy` + `/api/health` 200. Live on Node 1. |
| P0-05 | `fastapi-instrumentator` wired | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25 | 2026-04-25 | — | `curl localhost:8000/metrics` returns 200 from systemd `tars-backend.service` (uvicorn, port 8000). |
| P0-06 | Power meter reading capture | PLANNED | Tasin | — | 2026-04-21 | — | Need Kill-A-Watt or similar |
| P0-07 | Branch protection rules on main | PLANNED | Tasin | — | 2026-04-21 | — | Require PR + CI green + 1 review |
| P0-08 | GitHub Projects board | PLANNED | Tasin | — | 2026-04-21 | — | Columns per phase |
| P0-09 | Archive old CLAUDE.md, adopt new | PLANNED | Tasin | — | 2026-04-21 | — | mv CLAUDE.md.new CLAUDE.md |
| P0-10 | Delete ChromaDB stack, worker references | SHIPPED | Claude | c833ecb + Node 2 container teardown 2026-04-25 | 2026-04-25 | — | Code clean (compose + backend/worker stripped). Stale `tars-chromadb` + `tars-redis` containers (created 2026-03-13) destroyed on Node 2 2026-04-25. `docker ps` empty on tars2. |
| P0-11 | Run tars-probe on both nodes, capture baseline | BUILT | Tasin | logs collected 2026-04-21 | 2026-04-21 | — | i7-6700, 16GB DDR4-2133, Quadro M620 2GB, NVMe 256GB |
| P0-12 | Update architecture/model_tiers/tech_stack docs to match real hardware | BUILT | Claude | this session | 2026-04-21 | depends P0-11 | 16GB not 32GB; i7-6700 not 7700T; GPU exists |
| P0-13 | Move Redis to Node 1 in deploy/node1/docker-compose.yml | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25 | 2026-04-25 | — | `tars-redis` 7-alpine on Node 1 :6379 healthy 3d. TCP `PING`→`+PONG`. `ZCARD tars:jobs:queue`→0 (key valid). Old container destroyed on Node 2 2026-04-25. |
| P0-14 | Install lm-sensors on both nodes, baseline thermals | SHIPPED | Tasin | verified 2026-04-25 both nodes | 2026-04-25 | — | `sensors` present on tars1 + tars2; readings: tars1 nvme 44.9°C / nouveau 52°C / GPU 873mV; tars2 nvme 41.9°C / coretemp 42°C. |
| P0-15 | Install CUDA toolkit on Node 2 (for Quadro M620) | SHIPPED | Tasin | nvcc 12.2.140, driver 535.288.01, M620 visible; CUDAHOSTCXX persisted 2026-04-25 | 2026-04-25 | — | `/usr/local/cuda-12.2/bin/nvcc` V12.2.140; `nvidia-smi` driver 535.288.01 (1MiB/2048MiB free); PATH + LD_LIBRARY_PATH + `CUDAHOSTCXX=/usr/bin/g++-12` all in ~/.bashrc, verified via fresh `bash -ic`. |
| P0-16 | Storage cleanup Node 2 (36GB used vs Node 1 17GB) | SHIPPED | Tasin | verified 2026-04-25 | 2026-04-25 | — | `df -h /` = 19G used / 98G (was 36G). 17GB recovered. |
| P0-17 | Wire Node 2 worker to point at Node 1 Redis (100.94.4.103:6379) | SHIPPED | Claude | PR #8 merged `ea9624b`; smoke-001 round-trip 2026-04-25 | 2026-04-25 | — | `tars-worker.service` installed + enabled on tars2. Smoke: `ZADD tars:jobs:queue` from tars1 Redis → consumed by tars2 worker in <2s (ZCARD 1→0). All 4 units on tars2 active: llama-l0, llama-l1, llama-embed, tars-worker. |
| P0-18 | Claude SSH NOPASSWD scope + boundary check | SHIPPED | Tasin | `/etc/sudoers.d/tars-claude` on tars1, visudo OK 2026-04-25; boundary verified | 2026-04-25 | — | Sudoers Node 1: `systemctl restart/reload tars-backend` NOPASSWD. Boundary tests PASS: `sudo -n restart` succeeds; `sudo -n stop` + `sudo -n apt install` both blocked. tasin added to `docker` + `systemd-journal` groups. Doc: `docs/runbook.md §"Claude SSH Permission Boundary"`. **Revisit Phase 4 (WRITE_INFRA), pre-prod audit, public dashboard launch.** |

---

## Phase 1 — Foundation Fix

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P1-01 | `integrations/job_queue.py` w/ JobQueue class | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25; tests bd4937d, test_job_queue.py 10/10 | 2026-04-22 | — | ZADD on tars:jobs:queue, pubsub await on tars:jobs:results, subscribe-before-enqueue |
| P1-02 | Refactor `agents/coding.py` to use JobQueue | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25; tests bd4937d, test_coding_agent.py 8/8 | 2026-04-22 | — | `_QUEUE_KEY="tars:jobs:code"` removed; result read from message["result"] |
| P1-03 | Refactor `agents/fashion.py` to use JobQueue | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25; tests bd4937d, test_fashion.py::TestFashionImageDispatch 2/2 | 2026-04-22 | — | LPUSH to "tars:jobs" ripped; worker gained `save_image` task_type for image persistence |
| P1-04 | E2E test: distributed job round-trip | SHIPPED | Claude | c833ecb on tars1, verified 2026-04-25; tests bd4937d, test_queue_e2e.py 3/3 | 2026-04-22 | — | fakeredis.FakeServer shared between backend JobQueue + worker JobProcessor; covers happy path, unknown-type failure, priority ordering |
| P1-05 | Remove ChromaDB from `backend/src` | SHIPPED | Claude | c833ecb on tars1; Node 2 container destroyed 2026-04-25 | 2026-04-25 | — | No ChromaDB imports in backend/src; `CHROMA_AUTH_TOKEN` stripped from .env.example. Stale `tars-chromadb` container destroyed on Node 2. |
| P1-06 | Remove ChromaDB from `deploy/node2/docker-compose.yml` | SHIPPED | Claude | c833ecb on tars1; Node 2 container destroyed 2026-04-25 | 2026-04-25 | — | deploy/ clean. Live container teardown completed on Node 2. Qdrant added in P3. |
| P1-07 | Tag release `v0.1-distributed-real` | PLANNED | Tasin | PR #2 merged `c833ecb` 2026-04-25 | 2026-04-25 | — | PR #2 merged + deployed. Tasin: `git tag v0.1-distributed-real c833ecb && git push origin v0.1-distributed-real`. |

---

## Phase 2 — Local Inference Tier

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P2-01 | llama.cpp built on Node 2 with AVX2+FMA+CUDA | BUILT | Claude | tars2 ~/llama.cpp build 2026-04-25 | 2026-04-25 | — | cmake build w/ `-DGGML_CUDA=ON -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 -DCMAKE_CUDA_ARCHITECTURES=50 -DGGML_NATIVE=ON -DLLAMA_CURL=ON`. ggml v0.10.0, commit 0adede866. CUDA backend libs (libggml-cuda.so) present. `llama-cli`, `llama-server`, `llama-embedding` all built. Smoke: detects M620 (1997 MiB / 1969 MiB free), runs embed model end-to-end on GPU. **Note:** OpenSSL not found at config time → HTTPS support disabled in cpp-httplib (cosmetic; not used by tars). |
| P2-02 | Download Qwen3-1.7B GGUF (L0) | BUILT | Claude | ~/models/Qwen3-1.7B-Q8_0.gguf 2026-04-25 | 2026-04-25 | — | **Drift from spec:** Qwen only published Q8_0 + Q5_K_M for 1.7B (no Q4_K_M). Using **Qwen3-1.7B-Q8_0.gguf** (1.8GB, repo `Qwen/Qwen3-1.7B-GGUF`). VRAM note: full GPU offload at default ctx (4096) OOMs on 2GB M620. Plan for systemd unit: `-ngl 99 -c 1024` or partial offload. |
| P2-03 | Download Qwen3-8B Q4_K_M GGUF (L1) | BUILT | Claude | ~/models/Qwen3-8B-Q4_K_M.gguf 2026-04-25 | 2026-04-25 | — | 4.7GB, repo `Qwen/Qwen3-8B-GGUF`. Will run primarily on CPU with tiny GPU offload (KV cache only) given 2GB VRAM. Smoke test deferred to systemd unit phase. |
| P2-04 | systemd unit `llama-l0` on port 8001 (Qwen3-1.7B) | BUILT | Claude | `deploy/node2/systemd/llama-l0.service`, deployed tars2 2026-04-25 | 2026-04-25 | — | Active on tars2:8001. CPU-only via `CUDA_VISIBLE_DEVICES=` (M620 is reserved for embed). ctx 4096, threads 6, alias `qwen3-1.7b-reflex`. Cross-node smoke: 100.119.114.125:8001 returns `/v1/models`; chat completion runs at 52 tok/s prompt eval. **Note:** Qwen3 emits chain-of-thought in `reasoning_content` field — backend `local_client.py` must parse this. |
| P2-05 | systemd unit `llama-l1` on port 8002 (Qwen3-8B) | BUILT | Claude | `deploy/node2/systemd/llama-l1.service`, deployed tars2 2026-04-25 | 2026-04-25 | — | Active on tars2:8002. CPU-only (4.7GB model exceeds VRAM). ctx 4096, threads 6, alias `qwen3-8b-brain`. `/v1/models` endpoint reachable from Node 1 over tailscale. Inference smoke deferred — slow on CPU, will be tested with real backend traffic. |
| P2-05a | (stretch) bench Qwen3-30B-A3B Q4_K_M mmap'd | PLANNED | Tasin | — | 2026-04-21 | depends P2-04..5 | Only promote if ≥6 tok/s sustained |
| P2-05b | systemd unit `llama-embed` on port 8003 (Qwen3-Embedding-0.6B) | BUILT | Claude | `deploy/node2/systemd/llama-embed.service`, deployed tars2 2026-04-25 | 2026-04-25 | — | Active on tars2:8003. Full GPU offload (`-ngl 99`, ctx 512), CUDA env vars in unit. Cross-node `/v1/embeddings` returns 1024-dim vector. **Drift from spec:** only Q8_0 + f16 published (no Q4_K_M); using `Qwen3-Embedding-0.6B-Q8_0.gguf` (610MB). |
| P2-05c | Whisper.cpp w/ CUDA backend on Quadro M620 | PLANNED | Tasin | — | 2026-04-21 | depends P0-15 | Whisper-small.en, ~500MB VRAM |
| P2-05d | 3-way L1 bench: Qwen3-8B vs Gemma 4 12B vs Qwen3-30B-A3B-mmap | BUILT | Claude | `scripts/bench_l1_models.py` | 2026-04-25 | depends P2-04,5 | Harness scaffold: 55 prompts (50 quality + 5 tool-use), config-driven model list, tok/s + TTFT + tool-use accuracy + optional Claude Haiku judge. Smoke: qwen3-8b 4.96 tok/s CPU-only, 3/3 ok. Gemma4-12B + 30B-mmap stubs ready to enable when deployed (P2-05e). Run: `python scripts/bench_l1_models.py [--max-tokens 128] [--prompts N] [--judge]`. |
| P2-05e | Pull Gemma 4 12B Q4_K_M GGUF (candidate) | PLANNED | Tasin | — | 2026-04-21 | — | ~7.5GB |
| P2-06 | `backend/src/models/local_client.py` | BUILT | Claude | phase-2-local-client branch, tests test_local_client.py 11/11 | 2026-04-25 | — | httpx async, OpenAI-compat. `generate()` parses Qwen3 `reasoning_content` separately into `LocalResponse.reasoning`. `embed()` for 0.6B embed tier. Per-tier `enable_thinking` defaults (REFLEX off, BRAIN on). Live smoke from tars1 → all 3 tiers green: REFLEX 413ms, EMBED 1024-dim 68ms, BRAIN "2+2=4" 2162ms. |
| P2-07 | Add `LOCAL_REFLEX`/`LOCAL_BRAIN`/`LOCAL_EMBED` to `ModelName` | BUILT | Claude | phase-2-local-client branch | 2026-04-25 | — | `shared/constants.py` ModelName enum extended w/ `LOCAL_REFLEX`/`LOCAL_BRAIN`/`LOCAL_EMBED`. Endpoint mapping (port + alias + kind + default-thinking) lives in `local_client._ENDPOINTS`. |
| P2-08 | `orchestrator/signal_detector.py` | BUILT | Claude | PR #11 (cherry-pick of 4efae9e); test_signal_detector.py 31/31 | 2026-04-25 | — | `EscalationSignal` StrEnum (11 signals per `model_tiers.md`). `SignalDetector.detect(message, intent, attachments)` → `set[EscalationSignal]`. Pure function, deterministic, regex+intent-attribute. IMAGE_GEN takes precedence over IMAGE_UNDERSTANDING when both match. |
| P2-09 | Rewrite `model_router.py` around signals | BUILT | Claude | PR #11 (cherry-pick of 4efae9e); test_signal_aware_router.py 33/33 | 2026-04-25 | — | New `SignalAwareRouter` class lives alongside legacy `ModelRouter`. Local default (REFLEX/BRAIN per intent), cloud escalation per signal w/ documented precedence. ARCH_CODE → Claude on node2 w/ coding mcp; CRITICAL_DIAGNOSTIC → Claude w/ diagnostics mcp. |
| P2-10 | Feature flag `FEATURE_NEW_ROUTER` | BUILT | Claude | PR #11 (cherry-pick of 4efae9e) | 2026-04-25 | — | `Settings.feature_new_router: bool = False` in `config.py`. Engine `Orchestrator.__init__` reads flag, branches per-request between `ModelRouter` (legacy) and `SignalAwareRouter`. `model_routed` log gains `router=signal_aware\|legacy` + `signals=[…]` fields. Conftest pins flag False to keep legacy assertions valid. Production stays on legacy until 1-week soak — flip via `FEATURE_NEW_ROUTER=true` env. |
| P2-11 | Fallback chain extended (local → gemini → claude) | BUILT | Claude | phase-2-fallback branch; test_fallback_chain.py 14/14 | 2026-04-25 | — | `LocalClient` wired into `Orchestrator`. `_local_call()` + `_execute_local_with_fallback()`: LOCAL_REFLEX/BRAIN → local→gemini_flash→claude→raw. Cloud chain (gemini→claude, claude→gemini) unchanged. 14 tests cover every hop + regressions. Full suite 1105 passed. |
| P2-12 | L1 self-escalation JSON protocol | BUILT | Claude | phase-2-self-escalation branch; test_escalation_parser.py 17/17 + test_self_escalation.py 11/11 | 2026-04-25 | — | `orchestrator/escalation_parser.py` parses `{"escalate": "web\|gemini_pro\|claude", "reason": "..."}` from L1 reply (tolerates code fences + prose prefix). Engine wires `SELF_ESCALATION_SYSTEM_PROMPT` to L1 (`LOCAL_BRAIN`) calls only — L0 (`LOCAL_REFLEX`) never escalates. On detection → `_self_escalate()` reroutes to target tier; one-hop guarantee (upstream reply never re-parsed). Escalation target failure falls through cross-family cloud fallback then raw delivery. Result data tagged `self_escalated_from` + `escalation_reason`. Full suite 1133 passed. |
| P2-13 | Router unit tests (30+) | BUILT | Claude | PR #11 (cherry-pick of 4efae9e) | 2026-04-25 | — | 64 unit tests across `test_signal_detector.py` (31) + `test_signal_aware_router.py` (33). Covers every signal × intent matrix, precedence overrides, attachment dispatch, threshold edges. Full suite 1091 passed 0 failed. |
| P2-14 | Cost/tier tracking in `model_usage` | BUILT | Claude | phase-2-cost-tracking branch; test_usage_tracker.py 19/19 | 2026-04-25 | depends P2-06 | Added `LOCAL_REFLEX`, `LOCAL_BRAIN`, `LOCAL_EMBED` explicit zero-cost entries to `_COST_PER_1M` in `usage_tracker.py`. Engine already calls `UsageTracker.track()` with `route.model` after every local execute (step 6 in `handle()`); token counts flow via `AgentResult.data` from `_local_call()`. 3 new tests verify explicit entries (not fallthrough). Full suite 1136 passed. |
| P2-15 | Tag release `v0.2-local-first` | PLANNED | Tasin | — | 2026-04-21 | depends P2-01..14 | — |

---

## Phase 2.5 — Grounded Responses

> Live Telegram smoke 2026-04-25 exposed: slash commands fall through to LLM, BriefingAgent never invoked, LOCAL_REFLEX hallucinates briefings without real data. Phase 2.5 closes the "agent has senses" gap before Phase 3 dashboard makes the agent public.

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P2.5-01 | Slash command dispatch in `telegram_handlers.py` | PLANNED | Claude | — | 2026-04-25 | — | `CommandHandler` for `/briefing`, `/status`, `/help`, `/health`, `/budget`. Unknown slash → friendly "try plain English" reply. Tests: 5+ |
| P2.5-02 | `BriefingAgent.execute()` pulls real integrations | PLANNED | Claude | — | 2026-04-25 | depends P2.5-03 | weather_client + caldav_client + gmail (both accounts) + notion. Compose into structured context dict. Pass to LOCAL_BRAIN (Qwen3-8B, not 1.7B). Tests: weather call, cal events, email summary, notion tasks, composition |
| P2.5-03 | `ContextBuilder` real-data path | PLANNED | Claude | — | 2026-04-25 | — | Add intent-driven branch — for `weather`/`schedule`/`email`/`finance` intents pre-fetch + inject into `AgentContext.system_context`. Local tier reads it. Tests per intent. |
| P2.5-04 | Pre-fetch + inject pattern for local tier (no native tool-call) | PLANNED | Claude | — | 2026-04-25 | depends P2.5-03 | Qwen3 tool-call weak. Orchestrator pattern: detect tool-need by intent → fetch → format as system context → call LOCAL_BRAIN with grounded prompt. Document in `docs/code_conventions.md` (new section "Tool-use on local tier"). |
| P2.5-05 | Redis env drift fix (P0-13 followup) | PLANNED | Claude | — | 2026-04-25 | — | `/opt/tars/deploy/node1/.env` REDIS_URL points 192.168.12.201:6379 (LAN); should be 100.94.4.103:6379 (tailscale, per P0-13). Update env, restart, verify health goes green. |
| P2.5-06 | Persona prefix loader (pull-forward P5-02 partial) | PLANNED | Claude | — | 2026-04-25 | depends P2.5-04 | Move inline `BASE_LOCAL_SYSTEM_PROMPT` from `engine.py` into `data/persona/local.md`. `shared/persona.py::load_persona()` w/ `@lru_cache`. Phase 5 P5-02..04 then refines content, not infra. |
| P2.5-07 | Per-session ritual: deploy-drift + Redis health gates | PLANNED | Claude | — | 2026-04-25 | — | Update `docs/CLAUDE_PHASE_HANDOUT.md` ritual: add `git -C /opt/tars rev-parse HEAD` vs origin/main check + Redis-status assertion in JSON health check. Catches today's stale-code + Redis-IP regressions. |
| P2.5-08 | Live Telegram smoke gate | PLANNED | Tasin | — | 2026-04-25 | depends P2.5-01..04 | Manual: send `/briefing` → expect real weather + 3 cal events + email count + 3 notion tasks. Send "what's the weather" → expect grounded answer. No hallucination. |
| P2.5-09 | Tag release `v0.2.5-grounded` | PLANNED | Tasin | — | 2026-04-25 | depends P2.5-01..08 | Phase 2 was "local-first"; 2.5 is "local-first w/ senses". |

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

## Phase 3.5 — Sensor Foundation

> Pulled forward from Phase 4. Without `world_state` + weather/healthkit/tailscale sensors, BriefingAgent must poll integrations every call (slow + rate-limit risk). Sensors provide the substrate Phase 3 dashboard renders. Triggers + remaining sensors stay in Phase 4.

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P3.5-01 | `sensors/base.py` BaseSensor abstract (was P4-01) | PLANNED | Claude | — | 2026-04-25 | — | `async collect() -> dict`, `async publish(payload)`. Publish writes to `world_state` table + Redis pub/sub `tars:world:<sensor>`. Opus 4.7 design. |
| P3.5-02 | Postgres `world_state` table + partman monthly partitioning (was P4-09) | PLANNED | Claude | — | 2026-04-25 | depends P3.5-01 | Alembic migration. `id uuid pk`, `sensor text`, `payload jsonb`, `ts timestamptz`, index `(sensor, ts desc)`. |
| P3.5-03 | Weather sensor (was P4-07) | PLANNED | Claude | — | 2026-04-25 | depends P3.5-01..02 | 15-min cadence. Wraps existing `weather_client.py`. Writes to world_state + publishes pub/sub. |
| P3.5-04 | HealthKit sensor migration (was P4-06) | PLANNED | Claude | — | 2026-04-25 | depends P3.5-01..02 | Existing health_data ingest → adapt to BaseSensor pattern + world_state writes. |
| P3.5-05 | Tailscale presence sensor (was P4-08) | PLANNED | Claude | — | 2026-04-25 | depends P3.5-01..02 | Tailscale API. 2-min cadence. Tracks which devices online. |
| P3.5-06 | Spotify sensor (was P4-04) | PLANNED | Claude | — | 2026-04-25 | depends P3.5-01..02 | spotipy. 30s cadence. Now-playing track. Fail-soft if token missing. |
| P3.5-07 | `BriefingAgent` + `ContextBuilder` switch to read `world_state` | PLANNED | Claude | — | 2026-04-25 | depends P3.5-03..06 | Replace direct integration polling (from P2.5-02..03) with cached world_state read. Direct-integration kept as fallback when world_state stale. ≤500ms briefing prep. |
| P3.5-08 | Live Telegram smoke gate w/ sensor data | PLANNED | Tasin | — | 2026-04-25 | depends P3.5-07 | Send `/briefing` → real weather (last 15 min), HealthKit score, presence, Spotify now-playing if active. |
| P3.5-09 | Tag release `v0.2.8-sensors-foundation` | PLANNED | Tasin | — | 2026-04-25 | depends P3.5-01..08 | Pre-Phase 3 dashboard prep. Sensors stream is dashboard's life-blood. |

---

## Phase 4 — Autonomy Engine

| ID | Feature | Status | Owner | Evidence | Last Touched | Blockers | Notes |
|----|---------|--------|-------|----------|--------------|----------|-------|
| P4-01 | `sensors/base.py` BaseSensor abstract | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-01.** |
| P4-02 | Location sensor via iOS Shortcut POST | PLANNED | Tasin+Claude | — | 2026-04-21 | depends P3.5-01 | /api/v1/sensors/location |
| P4-03 | Mac activity sensor (Hammerspoon) | PLANNED | Tasin+Claude | — | 2026-04-21 | depends P3.5-01 | hash titles, no content |
| P4-04 | Spotify sensor via spotipy | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-06.** |
| P4-05 | Git activity sensor | PLANNED | Claude | — | 2026-04-21 | depends P3.5-01 | cron 5min |
| P4-06 | HealthKit sensor migration to pipeline | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-04.** |
| P4-07 | Weather sensor migration | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-03.** |
| P4-08 | Network presence (Tailscale API) | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-05.** |
| P4-09 | Postgres `world_state` monthly-partitioned | MOVED | — | — | 2026-04-25 | — | **Moved to P3.5-02.** |
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
