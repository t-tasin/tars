# CLAUDE.md — T.A.R.S.

> **Read first every session.** Thin index. Detail lives in linked files.

---

## What T.A.R.S. Is

Personal sovereign AI on 2× HP Z2 Mini G3. Local-first (Qwen3 via llama.cpp on Node 2). Claude/Gemini escalation on explicit signal only. JARVIS-inspired. 90% autonomous. Approval-gated for anything leaving the homelab.

**Product spec as narrative:** `docs/vision/day_in_life.md`
**Wiki auto-growth:** `docs/vision/wiki_growth.md`

---

## Documentation Index

| File | What's in it |
|---|---|
| `docs/FEATURES.md` | Living feature list + state machine (PLANNED → IN_PROGRESS → BUILT → TESTED → SHIPPED). **Update on every PR.** |
| `docs/architecture.md` | Nodes, services, data flow, queue keys |
| `docs/tech_stack.md` | Libraries, versions, deps per component |
| `docs/model_tiers.md` | Tier L0-L5, escalation signals, routing rules |
| `docs/tone_persona.md` | Persona file reference + Tone state machine |
| `docs/autonomy_budget.md` | READ / WRITE_LOCAL / WRITE_SELF / WRITE_WORLD / WRITE_INFRA |
| `docs/db_conventions.md` | Postgres schema rules, key tables, migrations |
| `docs/code_conventions.md` | Async, typing, errors, logging, structure |
| `docs/agent_pattern.md` | How to add an agent; BaseAgent interface |
| `docs/testing.md` | Test categories, pytest config, eval harness |
| `docs/observability.md` | structlog, Prometheus, Grafana, audit log |
| `docs/deployment.md` | CI/CD, GHCR, Docker Compose, resource limits |
| `docs/runbook.md` | Ops: restart, restore, rollback, incidents |
| `docs/vision/day_in_life.md` | Product vision |
| `docs/vision/wiki_growth.md` | CuratorAgent design |
| `docs/journal/YYYY-MM-DD.md` | Daily dev log — write one per active day |
| `tasks/lessons.md` | Self-improvement log — add entry after every correction |
| `.mcp.json` | MCP server config for Claude subprocess |
| `.env.example` | All env vars template |

**If any doc contradicts CLAUDE.md → CLAUDE.md wins. Fix the doc.**

---

## Hard Constraints (NEVER VIOLATE)

| ID | Constraint |
|----|-----------|
| HC-01 | No external communication sent without explicit user approval. Zero exceptions. |
| HC-02 | No code pushed to production or main branches without explicit approval. |
| HC-03 | No data deleted without explicit confirmation. |
| HC-04 | T.A.R.S. may NEVER initiate financial transactions or modify any financial account. Read-only only. |
| HC-05 | No credentials in plain text. Environment variables or encrypted secrets only. |
| HC-06 | No direct modification of AtlasDesk production databases. |
| HC-07 | T.A.R.S. never impersonates Tasin. All automated messages user-approved. |
| HC-08 | All actions logged to `audit_log`. |
| HC-09 | Graceful degradation if any AI model unavailable. Local always continues. |
| HC-10 | User can disable any agent or integration via config. |
| HC-11 | Wardrobe images stored locally only (Node 2). |
| HC-12 | AI usage stays within budget. Alert before limits. Track in `model_usage`. |
| HC-13 | Public dashboard stream contains ZERO PII. Sanitizer fuzz-tested before deploy. |
| HC-14 | CuratorAgent proposals require Tasin approval before writing to `wiki/`. |
| HC-15 | Persona voice consistency eval must pass before any model swap, LoRA rev, or system-prompt change. |

If unsure whether an action violates an HC → require approval.

---

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from Tasin: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start (SessionStart hook surfaces them automatically)

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between `main` and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding.
- Point at logs, errors, failing tests — then resolve them.
- Zero context switching required from Tasin.
- Fix failing CI without being told how.

---

## Feature State Protocol

Every PR must:
1. Reference a feature ID (`P2-03`) from `docs/FEATURES.md`
2. Update the feature row's Status, Evidence, Last Touched
3. Pass CI (`pytest tests/ -q`, `ruff check .`)

States: `PLANNED` → `IN_PROGRESS` → `BUILT` → `TESTED` → `SHIPPED`
Details: `docs/FEATURES.md` appendix.

New feature not in FEATURES.md? → PR that adds the row first (as `PLANNED`). Then the implementation PR.

---

## Commit Format

Conventional Commits. Subject ≤50 chars. Body only when "why" non-obvious.

```
fix(queue): match worker ZPOPMIN on tars:jobs:queue

Backend was LPUSHing tars:jobs:code (list) while worker read
tars:jobs:queue (sorted set). Jobs never dispatched.
```

Co-author footer required:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Git Safety

- Never force-push to main
- Never skip hooks (`--no-verify`)
- Never amend unless Tasin explicitly asks
- Never run `git reset --hard` / `git clean -f` / `branch -D` without confirm
- Pre-commit hook fail → fix root cause → NEW commit (never --amend)
- Commit messages via HEREDOC (preserves formatting)

---

## Core Pitfalls (top 10, see `docs/code_conventions.md` for full list)

1. Don't call L4/L5 (Claude) for anything L0/L1 can handle. Cost + rate limit.
2. Every `WRITE_WORLD` / `WRITE_INFRA` action goes through `ApprovalManager.create()`. HC-01.
3. Creds via env vars only. HC-05.
4. Async everywhere. CPU-heavy → `asyncio.to_thread()` or Node 2 worker.
5. Every model call → `UsageTracker.track()`. HC-12.
6. Queue key is `tars:jobs:queue` (ZADD/ZPOPMIN sorted set). No exceptions.
7. No ChromaDB. Qdrant for vectors.
8. Every new agent declares `AutonomyClass`. No default.
9. Wiki writes only via CuratorAgent proposals + Tasin approval. HC-14.
10. Public dashboard: sanitize first, emit second. HC-13.

---

## When In Doubt

- Might violate HC → require approval
- Escalation signal ambiguous → prefer local
- Tone ambiguous → prefer more serious
- User asks clarifying question → drop humor, answer
- Tests failing → stop, investigate, no band-aids
- Architecture unclear → propose 2-3 options w/ tradeoffs, let Tasin pick
- User intent unclear → **ASK**. Do not guess.
