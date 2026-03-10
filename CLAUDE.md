# CLAUDE.md — T.A.R.S. Project Context

> **Read this file at the start of every session.** It contains everything you need to make correct architectural decisions for T.A.R.S.

## Project Overview

**T.A.R.S.** (Tasin's Autonomous Resource System) is a personal AI assistant platform for a single user (Tasin). It runs 24/7 on two HP Z2 Mini G3 workstations at home, providing intelligent life management: morning briefings, email triage, job search, outfit suggestions, finance tracking, health monitoring, system monitoring (AtlasDesk infrastructure), communication drafting, product research, and coding assistance.

The system uses a **multi-model AI architecture**: Claude Code handles complex reasoning (communication drafting, code generation, research synthesis, diagnostics) and is enhanced with **MCP (Model Context Protocol) servers** that give it direct access to GitHub, PostgreSQL, Brave Search, and the filesystem. Google Gemini handles high-volume routine tasks (email classification, job screening, briefing composition, outfit suggestions, finance categorization), and deterministic local workers handle all API calls, data fetching, and scheduling with zero AI token cost. The right model for the right job — Claude is expensive and reserved for ~30-40% of AI calls; Gemini absorbs 60-70%.

T.A.R.S. is accessed via a custom iOS app (primary), Telegram bot (fallback), Apple Watch (notifications + quick approvals), HomePod Mini (voice output via AirPlay), and a custom "Hey TARS" wake word via USB mic on Node 1. All clients connect to the same backend API. Development happens on Mac, code is pushed to GitHub, CI/CD builds Docker images to GHCR, and the servers pull and run containers — no dev tools are installed on the servers.

## Architecture Summary

```
CLIENT LAYER
  iOS App (SwiftUI) ─── REST/WS ──┐
  Apple Watch ─── WatchConnectivity ──┤
  Telegram Bot ─── HTTP Long Poll ────┤
  Siri Shortcuts ─── HTTP Callback ───┤
  HomePod Mini ─── AirPlay Audio ─────┤
                                      ▼
NODE 1 — "BRAIN" (10.0.1.1)
  FastAPI (REST + WebSocket + APNs push)
  Orchestrator (Python asyncio, always-on)
    ├── Intent Classifier (rule-based, zero AI tokens)
    ├── Model Router (Claude | Gemini Flash/Pro/Vision | Local)
    ├── Context Builder (per-agent scoping)
    ├── Approval Queue Manager (Tier 1/2/3 enforcement)
    └── Scheduler (APScheduler — cron for briefing, email, jobs, health)
  PostgreSQL 16 (state DB — all persistent data)
  Porcupine Wake Word Daemon (USB mic → STT → orchestrator)
  Telegram Bot Gateway (python-telegram-bot)
  Integration Layer (CalDAV, Gmail, GitHub, Notion, Weather, Plaid, Grafana/Loki)
  Cloudflare Tunnel (external API access)
  Tailscale (mesh VPN to Node 2, iPhone, Wooster server)
      │
      │ Redis Queue (jobs) / Redis Pub/Sub (results)
      ▼
NODE 2 — "MUSCLE" (10.0.1.2)
  Redis 7 (job queue + pub/sub + cache)
  ChromaDB (vector store / semantic memory)
  Job Worker Daemon (consumes Redis jobs, dispatches to Docker containers)
  Docker Engine (sandboxed agent execution: code, research, diagnostics, job scraping)
  Persistent Volumes (/data/wardrobe, /data/outputs, /data/repos, /data/logs)
```

**Inter-node communication**: Gigabit Ethernet, private subnet 10.0.1.0/24, SSH key auth, Redis queue/pub/sub for job dispatch.

**External access**: Cloudflare Tunnel (outbound-only, bypasses T-Mobile CGNAT), Tailscale mesh VPN for inter-device communication.

## Technology Stack

### Node 1 (Brain)
- **Python 3.12+**, asyncio + uvloop
- **Node.js 22 LTS** (required for `npx`-based MCP servers used by Claude Code agents)
- **FastAPI 0.115+** (REST + WebSocket), Uvicorn
- **PostgreSQL 16** via asyncpg + SQLAlchemy 2.0 + Alembic
- **APScheduler 4.0+** for cron jobs
- **python-telegram-bot 21.x**
- **httpx 0.27+** for async HTTP
- **google-generativeai 0.8+** (Gemini SDK)
- **Claude Code CLI** (headless subprocess spawning, MCP-enhanced)
- **MCP Servers** (`@modelcontextprotocol/*`): GitHub, PostgreSQL, Brave Search, Filesystem — give Claude Code direct tool access
- **apprise 1.9+** (unified notification fan-out: Telegram, email, future channels)
- **caldav** (iCloud CalDAV), **google-api-python-client** (Gmail)
- **plaid-python 26.x**, **notion-client 2.x**
- **pvporcupine 3.x** (wake word), **pyaudio** (USB mic), **openai-whisper** (STT)
- **pyatv 0.14+** (AirPlay to HomePod), **pyttsx3/gTTS** (TTS)
- **PyAPNs2** (Apple Push Notifications)
- **structlog** (structured JSON logging)
- **Docker + Docker Compose**, cloudflared, tailscale

### Node 2 (Muscle)
- **Python 3.12+**, Redis 7.4+, ChromaDB 0.5+
- **sentence-transformers** (all-MiniLM-L6-v2 embeddings)
- Docker Engine (sandboxed containers)

### iOS App
- **Swift 5.10+**, SwiftUI, iOS 17+, MVVM + Repository pattern
- EventKit, HealthKit, Contacts, Speech, AVSpeechSynthesizer
- SiriKit + App Intents, WidgetKit, WatchKit + WatchConnectivity
- URLSession async/await (REST + WebSocket), Keychain

## Repository Structure

```
tars/
├── .github/workflows/           # CI: build-and-push.yml, lint.yml, test.yml
├── CLAUDE.md                    # THIS FILE — Claude Code project context
├── .mcp.json                    # MCP server config for Claude Code agents
├── .env.example                 # Template for all env vars
│
├── backend/                     # Node 1 Python backend
│   ├── pyproject.toml
│   ├── alembic.ini + alembic/   # DB migrations
│   ├── src/
│   │   ├── main.py              # Entrypoint (FastAPI + scheduler + WS)
│   │   ├── config.py            # pydantic-settings env loading
│   │   ├── dependencies.py      # FastAPI DI
│   │   ├── api/                 # REST endpoints (messages, briefings, approvals, health, jobs, wardrobe, finance, config, deploy, websocket)
│   │   │   ├── router.py        # Aggregates all sub-routers
│   │   │   ├── auth.py          # API key + device token middleware
│   │   │   └── schemas.py       # Pydantic request/response models
│   │   ├── orchestrator/        # Core engine
│   │   │   ├── engine.py        # Main orchestrator loop
│   │   │   ├── intent_classifier.py
│   │   │   ├── model_router.py
│   │   │   ├── context_builder.py
│   │   │   ├── approval_manager.py
│   │   │   └── response_formatter.py
│   │   ├── agents/              # Agent implementations
│   │   │   ├── base.py          # BaseAgent abstract class (MUST implement)
│   │   │   ├── briefing.py, email_classifier.py, job_search.py
│   │   │   ├── fashion.py, daily_life.py, health_monitor.py
│   │   │   ├── communication.py, product_research.py, coding.py
│   │   │   ├── research.py, eod_summary.py, finance.py
│   │   │   └── health_fitness.py
│   │   ├── models/              # AI model clients
│   │   │   ├── claude_spawner.py    # MCP-enhanced headless subprocess wrapper
│   │   │   ├── gemini_client.py     # Flash/Pro/Vision REST client
│   │   │   └── usage_tracker.py     # Token/cost tracking
│   │   ├── integrations/        # External service adapters
│   │   │   ├── caldav_client.py, gmail_client.py, github_client.py
│   │   │   ├── notion_client.py, weather_client.py, plaid_client.py
│   │   │   ├── grafana_client.py, telegram_bot.py, apns_client.py
│   │   │   ├── airplay_client.py, notification_service.py
│   │   │   └── job_boards/      # Adapter pattern (base.py + linkedin, indeed, yc, handshake, custom)
│   │   ├── wake_word/           # listener.py, stt_processor.py, tts_output.py
│   │   ├── db/
│   │   │   ├── session.py       # async session factory
│   │   │   ├── models.py        # SQLAlchemy ORM models
│   │   │   └── repositories/    # Data access layer (one per entity)
│   │   ├── scheduler/jobs.py    # All cron job definitions
│   │   └── utils/               # logger.py, crypto.py, constants.py
│   ├── tests/
│   └── Dockerfile
│
├── worker/                      # Node 2 job worker
│   ├── src/
│   │   ├── main.py              # Worker entrypoint
│   │   ├── job_processor.py     # Redis job consumer
│   │   ├── executors/           # code, research, diagnostic, job_scraper, image
│   │   └── docker_manager.py    # Sandbox container management
│   ├── tests/
│   └── Dockerfile
│
├── ios/TARS/                    # iOS app (Xcode project)
│   ├── TARS/
│   │   ├── Models/, ViewModels/, Views/, Services/, SiriIntents/, Widgets/
│   ├── TARSWatch/               # Apple Watch companion
│   └── TARSWidgetExtension/
│
├── deploy/
│   ├── node1/docker-compose.yml
│   ├── node2/docker-compose.yml
│   └── scripts/                 # deploy.sh, backup.sh, restore.sh, setup-server.sh
│
├── shared/                      # constants.py, schemas.py (shared types)
└── docs/                        # DESIGN_DOCUMENT.md, REQUIREMENTS_v2_1.md, API_REFERENCE.md, RUNBOOK.md
```

## Database Conventions

**Database**: PostgreSQL 16 on Node 1, database name `tars`, user `tars`.

**Primary keys**: All tables use `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`.

**Timestamps**: All tables include `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Tables with mutable rows also include `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` with an auto-update trigger.

**Enums**: Use PostgreSQL `CREATE TYPE ... AS ENUM (...)` for fixed value sets: `task_status`, `task_priority`, `approval_status`, `risk_tier`, `email_tier`, `health_status`, `job_status`. Reference them by type name in column definitions.

**JSONB**: Used for flexible/nested data: metadata fields, email addresses arrays, wardrobe seasons, briefing payloads, category breakdowns. Always provide a default (`'{}'::jsonb` or `'[]'::jsonb`).

**Naming**: snake_case for tables and columns. Tables are plural (`conversations`, `approvals`, `job_listings`). Foreign keys named `{referenced_table_singular}_id` (e.g., `conversation_id`, `task_id`).

**Migrations**: Alembic with `alembic/versions/` directory. Each migration is a numbered Python file. Run via `alembic upgrade head`. Connection string: `postgresql+asyncpg://tars:{password}@tars-db:5432/tars`.

**Indexes**: Create indexes for every column used in WHERE, ORDER BY, or JOIN. Use partial indexes where applicable (`WHERE status = 'pending'`, `WHERE active = true`). Use GIN indexes for JSONB and trigram text search.

**Key tables**: `conversations`, `messages`, `agent_tasks`, `approvals`, `email_classifications`, `briefings`, `config`, `contacts`, `agent_outputs`, `system_health_log`, `feedback_log`, `job_listings`, `job_applications`, `wardrobe_items`, `wardrobe_outfits`, `model_usage`, `transactions`, `finance_summaries`, `health_data`, `audit_log`.

## Code Conventions

### Async Patterns
- All I/O operations must be async (`async def`, `await`). The orchestrator runs on `asyncio` + `uvloop`.
- Use `asyncio.gather()` for parallel fetches (e.g., morning briefing data collection).
- Claude Code spawned via `asyncio.create_subprocess_exec()` with timeout.
- Gemini calls use the async SDK (`generate_content_async()`).

### Error Handling
- All agent execution wrapped in try/except. Failed agents log the error and return gracefully — never crash the orchestrator.
- Use specific exception classes: `ClaudeSpawnError`, `ApprovalExpiredError`, `ApprovalAlreadyDecidedError`, `IntegrationError`.
- Retry with exponential backoff for transient failures (API timeouts, rate limits).
- Graceful degradation: if Claude is unavailable, Gemini handles what it can; if Gemini is unavailable, Claude takes critical tasks; if both are down, local workers continue (HC-09).

### Logging Format
- **structlog** with JSON output. Every log line is structured:
  ```python
  log.info("agent_completed", agent="briefing", model="gemini_pro", duration_ms=3200, tokens=1450)
  log.error("agent_failed", agent="email_classifier", error="timeout", retries=3)
  log.info("approval_decided", approval_id="uuid", decision="approved", source="ios", latency_ms=800)
  ```
- Log levels: `DEBUG` (dev only), `INFO` (agent lifecycle, API calls), `WARNING` (degraded state, budget alerts), `ERROR` (failures), `CRITICAL` (service down).

### Typing
- Full type hints on all function signatures. Use `from __future__ import annotations`.
- Pydantic models for all API request/response schemas and config.
- SQLAlchemy 2.0 mapped classes for ORM models.
- Use `TypeAlias`, `Literal`, and `TypedDict` where appropriate.

### Code Organization
- Every integration extends `BaseIntegration` (abstract class with `health_check()` and `_refresh_token()`).
- Every agent extends `BaseAgent` (abstract class — see "Agent Development Pattern" below).
- Every job board extends `JobBoardAdapter` (adapter pattern).
- Repository pattern for DB access — one repository class per entity in `db/repositories/`.

## Multi-Model AI Routing Rules

| Route to | When the task involves |
|----------|----------------------|
| **Claude Code** | Complex reasoning, code generation, architecture decisions, communication drafting (emails to professors, cover letters), system diagnostics (analyzing Loki logs), research synthesis, interview prep, negotiation strategy, decision advising. **Enhanced with MCP servers** — Claude pulls its own context via tools instead of relying solely on prompt injection. |
| **Gemini Flash** | Email classification, job listing initial screening, simple Q&A, basic scheduling logic, health/fitness pattern detection, finance transaction categorization, general conversation |
| **Gemini Pro** | Morning briefing composition, job listing detailed evaluation, product research synthesis, meeting prep summaries, shopping recommendations, end-of-day summary |
| **Gemini Vision** | Wardrobe cataloging (photo → metadata), outfit suggestion, receipt/document OCR, fridge inventory, visual context analysis |
| **Local (zero tokens)** | API calls, data fetching, cron jobs, health checks, calendar sync, weather fetch, config changes, all deterministic operations |

**Escalation rule**: If a Gemini-routed task has `complexity == "high"`, escalate to Claude. If a task `requires_vision`, force Gemini Vision. If a task `needs_docker_sandbox`, route to Node 2.

**Budget enforcement**: Track all AI calls in `model_usage` table. Alert when Claude daily calls approach 15 or weekly approach 70. Target: <40% of total AI calls use Claude.

## MCP (Model Context Protocol) Integration

Claude Code agents are enhanced with MCP servers, giving them direct tool access to external services. Instead of pre-building all context in Python and injecting it into prompts, Claude Code pulls its own context via MCP tools as needed. This is a key architectural decision.

### `.mcp.json` Configuration (repo root)

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

### MCP Profiles Per Agent Type

Each Claude agent type gets access only to the MCP servers it needs:

```python
MCP_PROFILES = {
    "coding":         ["github", "filesystem", "postgres"],
    "research":       ["brave-search", "postgres"],
    "diagnostics":    ["postgres", "brave-search"],
    "communication":  ["postgres"],
    "general":        ["brave-search"],
}
```

### How MCP Changes Each Claude Agent

| Agent | Before MCP | After MCP |
|-------|-----------|-----------|
| **Coding/DevOps** | Dispatch to Node 2 → clone repo → inject CLAUDE.md → spawn | Spawn with GitHub MCP + filesystem MCP — Claude clones, reads, writes directly |
| **System Diagnostics** | Query Loki in Python → inject logs into prompt | Claude queries PostgreSQL MCP for health logs, pulls what it needs |
| **Research** | Build search results in Python → inject | Claude uses Brave Search MCP to research directly |
| **Communication** | Query state DB for past drafts → inject context | Claude queries PostgreSQL MCP for recipient history |

### Claude Code Spawning (MCP-Enhanced)

The `ClaudeSpawner` now uses `--max-turns 5` (to allow multi-turn MCP tool use) and `--allowedTools` to scope which MCP servers each agent can access. Working directory is set to `/data/repos` for filesystem MCP access. The prompt template tells Claude to use MCP tools to pull additional data as needed, reducing pre-built context injection.

**MCP Requirements**: Node.js 22 LTS must be installed on Node 1 (for `npx`-based MCP servers). MCP servers registered in `.mcp.json`. Auth tokens stored in encrypted env vars. Claude Code auto-discovers configured MCP servers.

## Notification Service (Apprise)

T.A.R.S. uses **Apprise** as a unified notification fan-out layer for alerts and informational broadcasts. Apprise handles the "send same message to multiple channels" pattern (Telegram, email, future Discord/Slack), while custom APNs code handles rich interactive push notifications (approve/reject buttons, approval cards on Apple Watch) and custom Telegram bot code handles the full interactive interface (inline keyboards, commands, file sharing).

**Channel routing by severity:**
- **Critical** → Telegram (Apprise) + Email (Apprise) + APNs push (custom, with action buttons)
- **Warning** → Telegram (Apprise) + APNs push (custom, informational)
- **Info** → Telegram (Apprise) only

Implementation: `backend/src/integrations/notification_service.py`

## Agent Development Pattern

### Creating a New Agent

1. Create `backend/src/agents/your_agent.py`
2. Extend `BaseAgent`:

```python
from agents.base import BaseAgent, AgentResult

class YourAgent(BaseAgent):
    """Description of what this agent does."""

    AGENT_TYPE = "your_agent"          # must match intent classifier mapping
    DEFAULT_MODEL = "gemini_flash"      # or "gemini_pro", "claude", "gemini_vision", "local"
    REQUIRES_APPROVAL = False           # True if this agent produces side effects

    async def execute(self, context: AgentContext) -> AgentResult:
        """Main execution logic. Return AgentResult with content + metadata."""
        # 1. Access scoped context (context.calendar, context.emails, etc.)
        # 2. Call AI model if needed via self.gemini or self.claude
        # 3. Return structured result
        return AgentResult(
            content={"key": "value"},
            text="Human-readable response",
            model=self.DEFAULT_MODEL,
            has_side_effects=self.REQUIRES_APPROVAL,
            action_type="your_action_type",   # if side effects
            approval_title="What you're proposing",  # if side effects
            preview={"details": "..."},        # if side effects
        )
```

3. Register in the intent classifier (`orchestrator/intent_classifier.py`):
   ```python
   "your_keyword|another_keyword": Intent(agent="your_agent"),
   ```

4. Register in the model router (`orchestrator/model_router.py`):
   ```python
   "your_agent": ModelRoute(model="gemini_flash", node="node1"),
   ```

5. If the agent has scheduled triggers, add to `scheduler/jobs.py`.
6. Write tests in `tests/test_agents/test_your_agent.py`.

### Required BaseAgent Interface
- `AGENT_TYPE: str` — unique identifier
- `DEFAULT_MODEL: str` — default AI model
- `REQUIRES_APPROVAL: bool` — whether outputs need user approval
- `async execute(context: AgentContext) -> AgentResult` — main execution method

## Approval System Pattern

Every action with external side effects must go through the approval system.

### Risk Tiers
- **Tier 1 (Autonomous)**: Read-only, informational, internal storage. Execute immediately, no approval.
- **Tier 2 (Approval Required)**: External side effects (send email, create event, create PR, archive emails, create Notion pages, apply to jobs). Propose → Preview → User approves/rejects/edits.
- **Tier 3 (Escalation)**: High-risk or irreversible (emails to professors, push to production, delete data, modify infrastructure). Full review with additional context.

### Marking an Action as Requiring Approval

In your agent's `execute()` method, return an `AgentResult` with:
```python
AgentResult(
    has_side_effects=True,
    action_type="send_email",           # maps to TIER_MAP in ApprovalManager
    approval_title="Send email to Prof. Sadigh",
    preview={                           # full preview shown to user
        "to": "sadigh@cs.stanford.edu",
        "subject": "Following up...",
        "body": "Dear Professor..."
    },
)
```

The orchestrator's `ApprovalManager` will:
1. Insert into `approvals` table with `status='pending'` and `expires_at=now()+1h`
2. Push to all connected clients via WebSocket
3. Send APNs push notification with approve/reject actions
4. Wait for user decision (approve → execute, edit → execute with changes, reject → discard)
5. Log to `audit_log`

### TIER_MAP Reference
```python
"send_email":       "tier2_approval"
"create_event":     "tier2_approval"
"archive_emails":   "tier2_approval"
"create_notion":    "tier2_approval"
"create_pr":        "tier2_approval"
"apply_job":        "tier2_approval"
"email_professor":  "tier3_escalation"
"push_production":  "tier3_escalation"
"delete_data":      "tier3_escalation"
"modify_infra":     "tier3_escalation"
```

## Docker / Deployment Conventions

### Workflow
```
Mac (development) → GitHub push → GitHub Actions CI/CD → GHCR (ghcr.io/tasin/tars-backend, tars-worker)
                                                              ↓
                                    Servers: docker compose pull && docker compose up -d
```

### Images
- `ghcr.io/tasin/tars-backend:latest` — Node 1 backend (API + orchestrator + scheduler + telegram + wake word)
- `ghcr.io/tasin/tars-worker:latest` — Node 2 job worker

### Docker Compose
- `deploy/node1/docker-compose.yml` — tars-backend, tars-db (postgres:16-alpine), cloudflared
- `deploy/node2/docker-compose.yml` — redis (7-alpine), chromadb, tars-worker

### Deployment Commands
```bash
# On each node:
cd /opt/tars/deploy/node{1,2}
docker compose pull
docker compose up -d --remove-orphans

# Self-deploy via T.A.R.S.:
POST /api/v1/deploy  {"confirm": true}
# Or: /deploy command via Telegram/iOS app
```

### Resource Limits
- tars-backend: 4GB RAM, 4 CPUs
- tars-db: 2GB RAM
- redis: 2GB RAM (maxmemory 2gb, allkeys-lru)
- chromadb: 2GB RAM
- tars-worker: 4GB RAM, 4 CPUs

### Persistent Volumes
- Node 1: `pgdata` (PostgreSQL), `tars-data` (/data)
- Node 2: `redis-data`, `chroma-data`, `worker-data` (/data/wardrobe, /data/outputs, /data/repos, /data/logs)

### Servers have NO dev tools
Docker, Docker Compose, and persistent data volumes only. All building happens in CI/CD. Never SSH into a server to edit code.

## Environment Variables Reference

See `.env.example` for the complete list. Key groups: Database, Redis, ChromaDB, T.A.R.S. API, AI Models (Gemini), Telegram, Gmail (OAuth ×2), iCloud CalDAV, GitHub, Notion, Plaid, Weather, Picovoice, APNs, Cloudflare, Grafana/Loki, Brave Search (for MCP), SerpAPI (optional).

Claude Code uses system-level auth (Max 5x plan) — no API key needed. MCP servers configured in `.mcp.json` with env vars for auth tokens.

## Testing Conventions

```bash
cd backend && python -m pytest tests/ -v
```

- Tests live in `backend/tests/` and `worker/tests/`.
- Use `pytest` + `pytest-asyncio` for async tests.
- `conftest.py` provides test database, mock Redis, mock Gemini/Claude clients.
- Test categories:
  - **Unit tests**: Each agent, intent classifier, model router, approval manager
  - **Integration tests**: Each integration client (mock external APIs)
  - **API tests**: Each REST endpoint (TestClient)
- Mocking: Mock all external API calls. Never hit real Gmail, GitHub, Gemini, etc. in tests.
- CI runs tests on every PR via `.github/workflows/test.yml`.

## Common Pitfalls

1. **Don't call Claude for simple tasks.** Email classification, scheduling, basic Q&A — these are Gemini Flash territory. Claude is expensive and rate-limited.
2. **Never bypass the approval system.** Every external side effect must go through `ApprovalManager.create()`. Check HC-01.
3. **Don't store credentials in code or config files.** Environment variables only, loaded via pydantic-settings. Check HC-05.
4. **Don't block the asyncio event loop.** CPU-heavy work (Whisper STT, image processing) should run in `asyncio.to_thread()` or dispatch to Node 2.
5. **Don't assume API data is fresh.** Cache responses in state DB with TTL. Re-fetch if stale (>5 min for most sources).
6. **Don't send raw AI output to users.** Always use `ResponseFormatter` to structure responses into the correct message types (text, card, approval, image, briefing).
7. **Don't forget to track AI usage.** Every Claude and Gemini call must go through `UsageTracker.track()` for budget monitoring.
8. **Don't hardcode the Wooster, Ohio location.** Use config for location-dependent features (weather, commute).
9. **Don't run development on the servers.** Mac → GitHub → GHCR → servers. Always.
10. **Don't forget audit logging.** Every API call, agent spawn, approval decision, config change, and external action must be logged (HC-08).
11. **OAuth token refresh for Gmail.** The google-auth library handles automatic refresh, but the refreshed token must be persisted back to encrypted storage.
12. **Redis is on Node 2 (10.0.1.2:6379).** Don't look for it on Node 1.
13. **ChromaDB uses token auth.** Include `CHROMA_AUTH_TOKEN` in all requests.
14. **MCP servers require Node.js 22 LTS.** The `npx`-based MCP servers (GitHub, PostgreSQL, Brave Search, Filesystem) won't work without Node.js installed on Node 1.
15. **Scope MCP access per agent.** Don't give every Claude agent access to every MCP server. Use `MCP_PROFILES` to restrict — coding agents get GitHub + filesystem, research agents get Brave Search, etc.
16. **Use Apprise for broadcast alerts, custom code for interactive notifications.** Apprise handles "send to all channels." APNs custom code handles approve/reject buttons. Telegram custom code handles inline keyboards. Don't mix these.

## Hard Constraints (NEVER VIOLATE)

| ID | Constraint |
|----|-----------|
| **HC-01** | **No email, message, or external communication sent without explicit user approval. Zero exceptions.** |
| **HC-02** | No code pushed to production or main branches without explicit approval. |
| **HC-03** | No data deleted without explicit confirmation. |
| **HC-04** | T.A.R.S. may NEVER initiate financial transactions, make purchases, move money, or modify any financial account. Read-only Plaid access ONLY for expense tracking. All financial data stored locally. |
| **HC-05** | No credentials stored in plain text anywhere. Environment variables or encrypted secrets only. |
| **HC-06** | No direct modification of AtlasDesk production databases. |
| **HC-07** | T.A.R.S. never impersonates the user. All automated messages must be user-approved. |
| **HC-08** | All actions logged and auditable. Every API call, agent spawn, approval decision, config change, and external action logged to `audit_log`. |
| **HC-09** | Graceful degradation if any AI model unavailable. Local workers always continue. If Claude down → Gemini handles what it can. If Gemini down → Claude handles critical tasks. If both down → raw data delivery continues. |
| **HC-10** | User can disable any agent or integration at any time via simple command. |
| **HC-11** | Wardrobe images stored locally only (Node 2). Never persisted on third-party services beyond transient Gemini Vision API calls. |
| **HC-12** | T.A.R.S. AI usage stays within budget. Alert before hitting Claude weekly limits. Track all usage in `model_usage` table. |

**If you are ever unsure whether an action violates a hard constraint, err on the side of caution and require approval.**
