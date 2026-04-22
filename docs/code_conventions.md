# Code Conventions

## Async
- All I/O is async. `async def`, `await`
- `asyncio.gather()` for parallel fetches
- Claude via `asyncio.create_subprocess_exec` w/ timeout
- Gemini via `google-genai` async SDK
- Local via httpx to llama.cpp endpoints
- CPU-heavy work → `asyncio.to_thread()` or dispatch to Node 2 worker

## Error Handling
- Try/except around every agent `execute()`. Failed agent logs + returns gracefully. Never crashes orchestrator.
- Specific exceptions: `ClaudeSpawnError`, `ApprovalExpiredError`, `ApprovalAlreadyDecidedError`, `IntegrationError`, `LocalInferenceError`, `CircuitBreakerOpenError`, `AutonomyClassMissingError`
- Retry with exponential backoff on transient failures (API timeouts, rate limits) — via `utils/resilience.py::retry_with_backoff`
- Circuit breaker integration — 3 failures in 5min → open 60s → half-open probe
- Graceful degradation per HC-09

## Logging
- structlog JSON everywhere
- Structured kwargs, not f-strings:
  ```python
  log.info("agent_completed", agent="briefing", model="gemini_pro",
           duration_ms=3200, tokens=1450)
  ```
- Levels: DEBUG (dev), INFO (agent lifecycle, API calls), WARNING (degraded, budget), ERROR (failures), CRITICAL (service down)

## Typing
- Full hints. `from __future__ import annotations`
- Pydantic for API + config
- SQLAlchemy 2.0 mapped ORM
- `TypeAlias`, `Literal`, `TypedDict` where useful

## Organization
- Every integration extends `BaseIntegration` (abstract: `health_check()`, `_refresh_token()`)
- Every agent extends `BaseAgent`
- Every sensor extends `BaseSensor` (abstract: `collect()`, publishes to Redis channel)
- Every executor extends `BaseExecutor` (Node 2 worker)
- Every job board extends `JobBoardAdapter`
- Repository pattern for DB access — one class per entity in `db/repositories/`

## Top Pitfalls (full list)

1. Don't call L4/L5 Claude for L0/L1 work (HC-12)
2. Never bypass `ApprovalManager.create()` for WRITE_WORLD+ (HC-01)
3. No creds in code/config. Env vars via pydantic-settings (HC-05)
4. Don't block event loop
5. Cache API responses w/ TTL. Re-fetch stale
6. Don't send raw model output. Use `ResponseFormatter`
7. Every model call → `UsageTracker.track()` (HC-12)
8. Don't hardcode location (use config)
9. Never dev on servers. Mac → GitHub → GHCR → servers
10. Audit log every action (HC-08)
11. Gmail OAuth refresh must persist back to encrypted storage
12. Redis is on Node 2 (10.0.1.2:6379)
13. MCP servers need Node.js 22 LTS on Node 1
14. Scope MCP per agent via `MCP_PROFILES`
15. Apprise broadcast, APNs interactive, Telegram interactive — don't mix
16. Queue key: `tars:jobs:queue` (ZADD/ZPOPMIN sorted set). NEVER list-based keys
17. No ChromaDB. Qdrant only
18. Local first. Justify escalation in `SignalDetector`, not inline
19. Every new agent declares `AutonomyClass`. Missing = test fails
20. Wiki writes only via CuratorAgent + approval (HC-14)
21. Public dashboard: sanitize first, emit second. Canary tests every deploy (HC-13)
22. Voice eval gate (HC-15) blocks model swaps

## Lint
- `ruff check .`
- `ruff format .`
- Config in `pyproject.toml`
