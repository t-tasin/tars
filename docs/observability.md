# Observability

## Logging
- `structlog` JSON everywhere
- Every agent lifecycle + routing + approval + budget event logged w/ structured kwargs
- Shipped to Loki via promtail sidecar (future) or raw JSON to stdout (current)

## Metrics
- `fastapi-instrumentator` → Prometheus `/metrics` endpoint on Node 1
- Per-route p50/p95/p99 latency
- Per-model call count, cost, tokens, duration
- Per-agent success rate
- Circuit breaker state (closed/open/half-open)
- Eval suite pass rate
- Power draw (from Node 2 external meter via script)

## Grafana
Dashboards on Node 1 port 3000:
- `tars-overview` — requests/sec, error rate, active agents, approval queue depth
- `tars-models` — tier distribution, cost/day, tokens/day
- `tars-health` — Postgres/Redis/Qdrant status, circuit breakers
- `tars-evals` — nightly suite health, regressions
- `tars-power` — Node 1/2 draw over time

## Audit
`audit_log` table row per:
- API request
- Agent spawn
- Approval decision
- Config change
- External side effect
- CuratorAgent proposal + decision
- Model swap / LoRA rev

HC-08 compliance.

## Public Dashboard
Sanitized events published to Redis channel `tars:public:events`. SSE endpoint `/api/v1/public/stream` fan-outs to `tars.<domain>` visitors. Fuzz-tested canary strings never appear in output (HC-13).
