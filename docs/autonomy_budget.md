# Autonomy Budget

## Classes

Every `AgentResult` must declare `autonomy_class`. Test fails if missing.

| Class | Behavior | Examples |
|---|---|---|
| `READ` | Always auto. Pure info retrieval, no writes anywhere. | Fetch weather, search inbox, read calendar |
| `WRITE_LOCAL` | Always auto. Writes to Postgres, local FS, Redis, Qdrant. No external side effects. | Log workout, classify email into tier, update wiki_index, cache response |
| `WRITE_SELF` | Auto until daily budget cap (default 30/day). Self-addressed nudges/notes. | TARS reminds Tasin, schedules Postgres reminder, pushes internal note |
| `WRITE_WORLD` | ALWAYS approval (HC-01). External side effect. | Send email, create calendar event, create PR, post Notion, apply to job, Telegram DM |
| `WRITE_INFRA` | ALWAYS Tier-3 escalation. | Deploy, delete data, modify infra, push to prod, `rm` |

## 90% Autonomy Math

Expected steady-state distribution:
- READ: 40%
- WRITE_LOCAL: 40%
- WRITE_SELF: 10%
- WRITE_WORLD: ~9%
- WRITE_INFRA: ~1%

→ 90% auto-executed. 10% approval-gated. HC-01 integrity preserved.

## Budget Tracking Schema

```sql
CREATE TABLE autonomy_budget (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  day DATE NOT NULL,
  class TEXT NOT NULL,
  agent TEXT NOT NULL,
  count INT NOT NULL DEFAULT 0,
  UNIQUE (day, class, agent)
);

CREATE TABLE autonomy_limits (
  class TEXT PRIMARY KEY,
  daily_cap INT NOT NULL,
  weekly_cap INT
);
```

Default:
```
WRITE_SELF: 30/day, 180/week
```

Other classes uncapped (but HC-01 still gates WRITE_WORLD+).

## Decision Flow

```
agent.execute(ctx) → AgentResult(autonomy_class=X)
  ↓
engine.enforce_autonomy(result):
    if X == READ:            execute
    if X == WRITE_LOCAL:     execute
    if X == WRITE_SELF:
        counter = autonomy_budget.today(X, agent)
        if counter >= daily_cap:
            downgrade to approval_required(reason="budget exhausted")
        else:
            increment counter, execute
    if X == WRITE_WORLD:     → ApprovalManager.create(tier=2)
    if X == WRITE_INFRA:     → ApprovalManager.create(tier=3)
```

## Config Override

Per `wiki/identity/autonomy_overrides.yml` — Tasin can temporarily or permanently override:

```yaml
# User override: allow workout_tracker to WRITE_WORLD (auto-log to Strava)
# — Tasin trusts it at this level
workout_tracker:
  write_world: auto
  cap: 50/day

# User override: force communication agent to Tier-3 for EVERYTHING
# — Tasin wants extra caution during interview season
communication:
  all: tier3_escalation
```

Loaded at startup + hot-reloaded on file change.

## HC Coverage

- HC-01: WRITE_WORLD/WRITE_INFRA always approval → emails/PRs/messages never auto-send
- HC-02: code push is WRITE_INFRA → Tier-3
- HC-03: delete operations are WRITE_INFRA
- HC-04: finance write ops FORBIDDEN (autonomy_class raises exception)
- HC-07: impersonation = WRITE_WORLD by definition → always approval
- HC-10: user can set any agent's class to `disabled` via config

## Tests

- Every agent has `test_<agent>_autonomy_class()` — asserts correct class
- Integration test: fake 40-msg day, verify 90% ratio in `autonomy_budget`
- Integration test: exceed WRITE_SELF cap → downgrade to approval
