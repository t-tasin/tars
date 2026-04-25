# Claude Phase Handout — T.A.R.S.

> Single source for **how to run Claude across remaining phases.** Each Claude session loads `CLAUDE.md` + `docs/FEATURES.md` + project memory at session start, so this doc is short on background and long on **strategy**: which model, which work in parallel, which sequential, where to spend Opus tokens vs Sonnet.

---

## Models — when to use which

| Model | Use for | Don't use for |
|-------|---------|---------------|
| **Sonnet 4.6** (default) | TDD loops, integration, deployment, doc edits, journaling, ~80% of TARS work | Novel architecture decisions where wrong call costs days |
| **Opus 4.7 (1M ctx)** | Architecture design, security-critical code, multi-file refactors ≥5 files, persona/LoRA tuning, debug spirals where Sonnet looped >3× | Anything Sonnet handles in one pass — wastes budget |
| **Opus 4.6 fast mode** (`/fast`) | Quick status sweeps, "diff branch vs main", small TDD cycles <100 LOC | Long deliberation tasks |
| **Haiku 4.5** | Single-file lint/format, FEATURES.md row updates, journal append, status checks, single-shell-command tasks | Anything requiring multi-file context or design judgment |

**Rule of thumb:** start every session in **Sonnet 4.6**. Upgrade to Opus 4.7 only when you hit a task explicitly tagged "Opus" below or after Sonnet loops.

---

## Token-efficiency rules (every session)

1. **Caveman mode** on (`/caveman` or auto via SessionStart hook) — saves ~75% output tokens
2. **`Explore` subagent** for any codebase question >2 files — keeps main context clean
3. **`claude-code-guide` agent** for SDK/API questions
4. Run `pytest` on **touched test files**, not full suite, until pre-merge
5. **One PR per logical chunk**, ≤500 LOC diff
6. Never re-read files already in context this session
7. Parallel streams → use **`isolation: "worktree"`** so contexts don't fragment
8. **Strict TDD** (superpowers skill) — 64 router tests proved the discipline pays

---

## Phase-by-phase plan

### Phase 2 wrap-up (3 PRs left)

| ID | Work | Model | Strategy |
|----|------|-------|----------|
| P2-11 | Fallback chain `local → gemini → claude` in engine | Sonnet | Sequential (touches engine.py) |
| **P2-12** | **L2 self-escalation JSON protocol** — system prompt + parser + engine state machine | **Opus 4.7** | Sequential, after P2-11. Fiddly: prompt design + JSON parsing + reroute logic |
| P2-14 | Cost/tier tracking — add LOCAL_* zero-cost entries, verify `track()` on every LocalClient call | Sonnet | Sequential (touches usage_tracker + engine) |
| P2-05d | Bench harness scaffold (`scripts/bench_l1_models.py`) | Sonnet | **Parallel** — separate branch, no engine touch |
| P2-15 | Tag `v0.2-local-first` | Tasin | Manual |

**Strategy:** P2-11 → P2-12 → P2-14 sequential single agent. Spawn P2-05d as parallel worktree subagent during P2-12 (Opus is busy thinking, Sonnet bench harness compiles in background).

---

### Phase 3 — Sovereign data layer (BIG parallelization win)

3 independent streams. Spawn **3 parallel worktree agents** at session start:

| Stream | Work | Model | Notes |
|--------|------|-------|-------|
| **A — Vector + embed** | P3-01 Qdrant compose + P3-03 qdrant_client.py + P3-04 embedding_client.py + P3-08 wiki collection ingest | Sonnet | TDD loop, similar shape to LocalClient |
| **B — Wiki content** | P3-05 wiki/ scaffold + P3-09 retrieve_wiki context-builder hook | Sonnet | Tasin owns P3-06 persona file authoring |
| **C — Web dashboard** | P3-11 Next.js 15 scaffold + P3-13 SSE endpoint + P3-15 dashboard MVP | Sonnet (delegate to **`vercel:nextjs` + `vercel:ai-sdk` skills**) | P3-12 Vercel deploy is Tasin manual |
| **D — Sanitizer** | **P3-14 sanitizer fuzz tests (HC-13)** | **Opus 4.7** | Security-critical, security-review skill mandatory before merge |

**Sequential chokepoint:** P3-07 wiki_watcher depends on P3-03+P3-04 from stream A. Run after stream A merges.

**Migration order in repo:** A → wait for A merge → B + C in parallel → D last (security review).

---

### Phase 4 — Autonomy engine (foundation sequential, sensors parallel)

**Foundation must lock first** (Opus 4.7 — design choices ripple):

| ID | Work | Model |
|----|------|-------|
| P4-01 | `BaseSensor` abstract | Opus 4.7 |
| P4-09 | Postgres `world_state` partman migration | Sonnet |
| P4-10 | `trigger_engine.py` pub/sub subscriber + pattern matcher | **Opus 4.7** |
| P4-11 | `AutonomyClass` enum | Sonnet |
| P4-12 | `AgentResult.autonomy_class` required field + test gate | Sonnet |
| P4-13 | `autonomy_budget.py` daily cap tracker | **Opus 4.7** (security-adjacent: budget caps are HC) |
| P4-14 | `autonomy_budget` table | Sonnet |

**Then 7 sensors in parallel** (Sonnet, all independent):

P4-02 location · P4-03 mac activity · P4-04 spotify · P4-05 git · P4-06 healthkit · P4-07 weather · P4-08 tailscale presence

**Then 10 triggers in parallel** (Sonnet, all consume same world_state):

P4-15 evening_wind_down · P4-16 meeting_prep · P4-17 commit_streak · P4-18 sleep_recovery · P4-19 unused_clothes · P4-20 email_batch · P4-21 subscription_audit · P4-22 atlasdesk_watchdog · P4-23 focus_mode · P4-24 arrived_home

**Strategy:** sequential for P4-01/09/10/11/12/13/14, then dispatch 7 + 10 parallel worktree agents (Sonnet each, ~30 min per).

---

### Phase 5 — Persona + LoRA (sequential, Opus-heavy)

LoRA pipeline is a hard chain — each step depends on prior:

| ID | Work | Model |
|----|------|-------|
| P5-01 | persona file finalization | Tasin |
| P5-02/03/04 | Persona prefix injection in 3 clients | **Opus 4.7** (multi-file, voice-critical, HC-15) |
| P5-05 | `tone_state_machine.py` | Opus 4.7 |
| P5-06 | Tone postfix all 3 clients | Sonnet (P5-02..04 already designed pattern) |
| P5-07 | Gmail sent export | Tasin |
| P5-08 | Hand-label 500 dialog pairs | Tasin |
| P5-09 | **Unsloth LoRA training script** | **Opus 4.7** (novel ML code) |
| P5-10 | Train Qwen3-1.7B-tars-v1 LoRA | Tasin (Colab T4 / Mac MLX) |
| P5-11 | Export + deploy merged GGUF, swap into L0 | Sonnet (deploy ops) |
| P5-12 | Voice consistency eval suite | **Opus 4.7** |
| P5-13/14 | Claude Opus judge wiring + ≥4.2 gate | Sonnet |

**Strategy:** strictly sequential. No parallelism — each step's output is next step's input. Use Opus 4.7 for design steps, Sonnet for plumbing.

---

### Phase 6 — Public dashboard polish (max parallelization)

Each panel is its own component, no shared state. **Dispatch 8 parallel worktree subagents at session start:**

| Stream | Component | Model |
|--------|-----------|-------|
| 1 | P6-01 r3f 3D two-node scene + P6-02 Tailscale particles + P6-03 LLM orb glow | Sonnet (`frontend-design` skill + `vercel:react-best-practices`) |
| 2 | P6-04 live activity feed | Sonnet |
| 3 | P6-05 routing treemap | Sonnet |
| 4 | P6-06 agent ring 14 badges | Sonnet |
| 5 | P6-07 system pulse panel | Sonnet |
| 6 | P6-08 today's numbers banner | Sonnet |
| 7 | P6-09 "now working on" tile | Sonnet |
| 8 | P6-10 mobile responsive layout | Sonnet |

**Sequential after parallel finish:**

| ID | Work | Model |
|----|------|-------|
| P6-11 | Hero copy | Tasin + Sonnet |
| P6-12 | OG image + meta tags | Sonnet |
| **P6-13** | **Lighthouse perf >90** | **Opus 4.6 fast mode** + `vercel:performance-optimizer` agent |
| P6-14 | About drawer | Sonnet |
| P6-15 | Tag release | Tasin |

---

### Phase 7 — Eval harness (foundation sequential, suites parallel)

**Foundation:**

| ID | Work | Model |
|----|------|-------|
| P7-01 | Postgres `evals` table + alembic | Sonnet |
| **P7-08** | **Nightly runner `eval_nightly.py`** | **Opus 4.7** (orchestration design) |

**Then 6 eval suites in parallel** (each is its own dataset + scorer, no overlap):

P7-02 intent_classifier · P7-03 email_classifier · P7-04 briefing · P7-05 voice_consistency · P7-06 routing_precision · P7-07 wiki_retrieval

Use **Sonnet for each suite, Opus 4.7 for P7-04 briefing** (Claude judge wiring is fiddly).

**Sequential after suites:**

P7-09 Grafana dashboard (Sonnet) · P7-10 regression alert (Sonnet) · P7-11 7-day green streak (Tasin observation) · P7-12 tag (Tasin)

---

### Phase 8 — Demo polish (mostly Tasin, scattered Sonnet)

| ID | Work | Owner | Model |
|----|------|-------|-------|
| P8-01..03 | USB mic + wake-word train + E2E | Tasin | — |
| P8-04 | Capture demo video | Tasin | — |
| P8-05 | Mac menubar SwiftUI app | Tasin (or Sonnet w/ Swift help) | Sonnet |
| P8-06 | iOS Live Activity | Tasin | — |
| P8-07 | Excalidraw diagram | Tasin | — |
| P8-08 | 30-day metrics snapshot | Tasin | — |
| P8-09 | Public writeup | Tasin + Sonnet draft | Sonnet |
| P8-10 | Tag release | Tasin | — |
| P8-11 | iOS App Intent `AskTARSIntent` | Tasin | — |
| P8-12 | iPhone Action Button shortcut | Tasin | — |
| P8-13 | Apple Watch App Intent | Tasin | — |

**Strategy:** Phase 8 is Tasin-led hardware/content. Claude is on standby for code review of SwiftUI / App Intent code. **No agent dispatch needed.**

---

### Phase 9 — Ship

All Tasin. Claude on standby for last-minute README polish.

---

## Master ordering recommendation

1. **Phase 2 wrap-up** (3 sessions, Opus session in middle for P2-12)
2. **Phase 3 sovereign data** (1 long session w/ 4 parallel worktree agents at start; Opus only for D sanitizer)
3. **Phase 4 autonomy** (foundation Opus session + sensors session w/ 7 parallel + triggers session w/ 10 parallel)
4. **Phase 5 persona** (Tasin authors persona, then 1 long Opus chain session for inject/tune/eval)
5. **Phase 6 dashboard polish** (1 session w/ 8 parallel agents + 1 perf session)
6. **Phase 7 eval harness** (1 foundation session + 1 session w/ 6 parallel suites)
7. **Phase 8/9 ship** (Tasin-led, occasional Claude code review)

Estimated total: **~12-15 Claude sessions** to v1.0-ship-ready, mostly Sonnet, ~5 Opus 4.7 spikes for the architecture/security/persona/eval design.

---

## Per-session ritual (every session uses this)

1. **`SessionStart` loads CLAUDE.md + memory** automatically
2. **Sanity sweep** before any work:
   ```
   git checkout main && git pull
   gh pr list --state open
   ssh tars1 "systemctl is-active tars-backend"
   ssh tars2 "systemctl is-active llama-l0 llama-l1 llama-embed tars-worker"
   cd backend && .venv/bin/pytest tests/ -q | tail -3
   ```
3. **Drift caught → STOP, report, do not proceed**
4. Branch per phase, commit per logical chunk, **always** `ruff format` + `ruff check` before push (CI checks both — caught us once on PR #6/#7)
5. PR with test plan + FEATURES row update + journal entry **in the same PR**
6. **Strict TDD** for production code (RED → GREEN → REFACTOR)

---

## Skills always available (use proactively)

- `superpowers:test-driven-development` — every feature/bugfix
- `superpowers:brainstorming` — before any creative design (start of phases 4, 5, 7)
- `superpowers:writing-plans` — when scope is unclear
- `superpowers:dispatching-parallel-agents` — Phase 3, 4, 6, 7 parallel streams
- `superpowers:using-git-worktrees` — every parallel dispatch
- `superpowers:verification-before-completion` — before claiming any task done
- `feature-dev:feature-dev` — Phase 4/5 architecture design
- `frontend-design:frontend-design` + `ui-ux-pro-max:ui-ux-pro-max` — Phase 3 dashboard MVP, Phase 6 polish
- `vercel:nextjs` + `vercel:ai-sdk` + `vercel:react-best-practices` — Phase 3/6 web work
- `vercel:performance-optimizer` — P6-13 lighthouse
- `claude-mem:make-plan` + `claude-mem:do` — multi-step phases (Phase 4 foundation, Phase 5 LoRA pipeline)
- `security-review` — P3-14, P4-13, P9-01

---

## Hard rules (never violate)

- **CLAUDE.md HC-01..15** — read every session
- Caveman mode every session
- Branch protection (P0-07 once enabled by Tasin) — never force-push main
- TDD strict for production code — superpowers skill is rigid here
- FEATURES.md updated **every PR**
- Sudoers boundary (P0-18) — `sudo -n` only for whitelisted; everything else is Tasin manual
