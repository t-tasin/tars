# Session Kickoff Prompts

Copy-paste at the start of a fresh Claude Code session per phase.

---

## Generic Template (fill in PHASE + ROWS)

```
Phase <N> of T.A.R.S.

Read first, in order:
  1. CLAUDE.md (full)
  2. docs/FEATURES.md (Phase <N> rows P<N>-01..XX)
  3. docs/journal/ (latest 1-2 entries)
  4. tasks/lessons.md (all corrections — must not repeat)

Prior-phase state:
  Tag: v0.<N-1>-<slug>  (check `git tag -l`)
  Exit metric: <paste or "see journal">

Hardware reminder:
  Node 1 (tars-brain, 100.94.4.103): i7-6700, 16GB DDR4, Quadro M620 (idle)
  Node 2 (tars-muscle, 100.119.114.125): same, CUDA 12.2 + driver 535 held
  Build flags for CUDA: -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 -DCMAKE_CUDA_ARCHITECTURES=50
  Env: CUDAHOSTCXX=/usr/bin/g++-12 set in ~/.bashrc

Phase <N> goal: <one sentence from FEATURES.md header>

Open tasks this session:
  P<N>-<XX>  <title>
  P<N>-<YY>  <title>
  ...

Work rules:
  1. Plan mode first. Show full diff plan BEFORE any edit. Wait for OK.
  2. Execute row-by-row. Commit after each feature.
  3. Every PR references a P<N>-XX ID and updates FEATURES.md Status + Evidence + Last Touched.
  4. TDD where feasible: failing test → impl → refactor.
  5. Demand elegance on non-trivial changes — pause + ask "more elegant way?".
  6. Never mark TESTED without proving the artifact works (run the test, see the output).
  7. If corrected: stop, add entry to tasks/lessons.md with rule-for-future.

End of session:
  - Journal entry at docs/journal/YYYY-MM-DD.md
  - FEATURES.md state sync
  - lessons.md updated if any corrections happened
  - Tag release if phase complete: git tag v0.<N>-<slug>

Go.
```

---

## Phase 0 — Baseline + Instrumentation

```
Phase 0 of T.A.R.S.

Read first, in order:
  1. CLAUDE.md
  2. docs/FEATURES.md (Phase 0 rows P0-01..17)
  3. docs/journal/2026-04-22.md
  4. tasks/lessons.md

Current state:
  - Hardware probed. i7-6700 + 16GB + Quadro M620 both nodes.
  - Node 1 free disk 81GB, Node 2 free disk 75GB (post-prune).
  - CUDA 12.2 TESTED on Node 2. Driver 535 held. gcc-12 host compiler pinned.
  - CLAUDE.md swapped to thin 174-line version.
  - 983/993 tests pass baseline.

Phase 0 goal: green tests + instrumented backend + purged ChromaDB
before Phase 1 touches the Redis queue bug.

Open tasks (incomplete rows only):
  P0-01  Fix 2 failing tests + test_telegram_handlers import error
  P0-02  Journal convention (already started, confirm + commit)
  P0-03  Baseline coverage report via pytest --cov
  P0-04  Add Prometheus + Grafana to deploy/node1/docker-compose.yml
  P0-05  Wire fastapi-instrumentator, expose /metrics
  P0-06  Power meter baseline (manual task — Kill-A-Watt reading)
  P0-07  Branch protection rules on main (manual GitHub task)
  P0-08  GitHub Projects board (manual)
  P0-09  Verify CLAUDE.md already swapped ✓
  P0-10  Delete ChromaDB from backend src (health_monitor, api/health, config.py)
  P0-13  Move Redis to Node 1 compose, stub Qdrant slot
  P0-14  Install lm-sensors both nodes (manual)
  P0-16  Storage cleanup Node 2 ✓ already done
  P0-17  Worker config point at Node 1 Redis (10.0.1.1:6379)

Done before session start: P0-11, P0-12, P0-15 (TESTED), P0-16 (done).

Work rules:
  1. Plan mode first. Show full diff plan BEFORE any edit.
  2. Execute row-by-row. Commit after each.
  3. Every PR references P0-XX and updates FEATURES.md.
  4. TDD where feasible.
  5. Demand elegance on non-trivial changes.
  6. Never mark TESTED without running the test.
  7. Corrected → update tasks/lessons.md.

End of session:
  - docs/journal/YYYY-MM-DD.md entry
  - FEATURES.md rows updated
  - Tag v0.0-preflight when all rows BUILT/TESTED/SHIPPED

Go.
```

---

## Phase 1 — Foundation Fix (Redis Queue + ChromaDB Removal)

```
Phase 1 of T.A.R.S.

Read first:
  1. CLAUDE.md
  2. docs/FEATURES.md (Phase 1 rows P1-01..07)
  3. docs/journal/ (last 2 entries)
  4. tasks/lessons.md

Prior tag: v0.0-preflight

Phase 1 goal: make the "distributed" architecture actually distributed.
Backend currently LPUSHes to `tars:jobs:code` (list). Worker reads
`tars:jobs:queue` (sorted set). Jobs never dispatch. Fix this.

Open tasks:
  P1-01  backend/src/integrations/job_queue.py  — JobQueue class (ZADD + pubsub)
  P1-02  Refactor agents/coding.py to use JobQueue
  P1-03  Refactor agents/fashion.py to use JobQueue
  P1-04  E2E test: distributed job round-trip (testcontainers redis)
  P1-05  Remove ChromaDB from backend/src (verify Phase 0 left clean)
  P1-06  Remove ChromaDB from deploy/node2/docker-compose.yml
  P1-07  Tag v0.1-distributed-real

Work rules: same as generic template.

End: journal + FEATURES.md + tag.

Go.
```

---

## Phase 2 — Local Inference Tier

```
Phase 2 of T.A.R.S.

Read first:
  1. CLAUDE.md
  2. docs/FEATURES.md (Phase 2 rows P2-01..15)
  3. docs/journal/ (last 2-3 entries)
  4. tasks/lessons.md
  5. docs/model_tiers.md (bench plan)

Prior tag: v0.1-distributed-real

Phase 2 goal: Node 2 runs Qwen3 + (bench winner) 24/7. Router
local-first w/ signal-based escalation.

Open tasks (summary — see FEATURES.md for full):
  P2-01..05  llama.cpp build + GGUFs + systemd units
  P2-05a  Stretch bench Qwen3-30B-A3B mmap'd
  P2-05d  3-way L1 bench: Qwen3-8B vs Gemma 4 12B vs Qwen3-30B-A3B
  P2-06..14  Backend integration + router + tests
  P2-15  Tag v0.2-local-first

CUDA reminder: llama.cpp cmake flags
  -DLLAMA_CUDA=ON -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 -DCMAKE_CUDA_ARCHITECTURES=50

Bench gate: pick L1 empirically. Do not promote a candidate that doesn't
beat Qwen3-8B on sustained tok/s × quality product.

Work rules: same as generic.

End: journal + FEATURES.md + tag.

Go.
```

---

## Phases 3-9

Same template pattern. Fill in:
  - Phase number + title
  - Prior tag
  - Goal sentence from FEATURES.md
  - Open row IDs
  - Any phase-specific reminders (Qdrant setup, Vercel domain, LoRA corpus path, eval judge model, etc.)

Template above covers the shape. Follow it.
