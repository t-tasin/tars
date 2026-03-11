# Finance System: Plaid → Teller.io Migration + Gap Fill

## Task Breakdown (7 agents)

| # | Agent | Skill | Ultrathink? | Depends On |
|---|-------|-------|-------------|------------|
| 1 | Teller Client + Config Migration | `feature-dev` | Yes | — |
| 2 | DB Schema Migration (Alembic) | `feature-dev` | No | — |
| 3 | Finance Agent Rewrite + Trend Engine | `feature-dev` | Yes | 1, 2 |
| 4 | Subscription Tracker + Anomaly Detection | `feature-dev` | Yes | 2 |
| 5 | Budget System | `feature-dev` | No | 2 |
| 6 | Morning Briefing + Scheduler Wiring | `feature-dev` | No | 1, 3 |
| 7 | Tests (all finance) | `feature-dev` | No | 1–6 |

**Execution order**: Agents 1 & 2 in parallel → Agents 3, 4, 5 in parallel → Agent 6 → Agent 7

---

## Agent 1: Teller Client + Config Migration

> **Skill**: `/feature-dev`
> **Ultrathinking**: YES — mTLS auth pattern is non-trivial, needs careful httpx SSL config
> **Estimated files**: 3–4 modified, 1 new

### Context

T.A.R.S. is migrating from Plaid to Teller.io for bank transaction data. Teller is free for personal use, uses mTLS certificate authentication (not API keys), and has a simpler REST API. The user already has their Teller certificate and private key.

### Task

Replace `backend/src/integrations/plaid_client.py` with a new `backend/src/integrations/teller_client.py` and update config.

### Teller.io API Reference (use this, do NOT guess)

**Base URL**: `https://api.teller.io`

**Authentication**: mTLS (mutual TLS). Every request must include a client certificate + private key. In Python httpx, this is done via `httpx.AsyncClient(cert=("/path/to/cert.pem", "/path/to/key.pem"))`. Access tokens are passed as HTTP Basic Auth username (password is empty). Sandbox environment does NOT require mTLS but still needs the access token.

**Endpoints used (READ-ONLY, HC-04)**:

```
GET /accounts
  → Returns list of all linked accounts
  → Response: [{ "id": "acc_xxx", "name": "Checking", "type": "depository", "subtype": "checking", "institution": { "name": "Chase", "id": "chase" }, "currency": "USD", "enrollment_id": "enr_xxx", "links": { "self": "...", "balances": "...", "details": "...", "transactions": "..." }, "status": "open" }]

GET /accounts/:account_id/balances
  → Response: { "account_id": "acc_xxx", "ledger": "5000.00", "available": "4850.00", "links": {...} }

GET /accounts/:account_id/transactions
  → Pagination: ?count=250&from_id=txn_xxx
  → Filter: ?from_date=2026-01-01 (ISO 8601, inclusive, on or after)
  → Response: [{
      "id": "txn_xxx",
      "account_id": "acc_xxx",
      "amount": "-12.50",  // negative = debit/spend, positive = credit/income (OPPOSITE of Plaid!)
      "date": "2026-03-10",
      "description": "STARBUCKS #1234",
      "status": "posted",  // or "pending"
      "type": "card_payment",
      "running_balance": "4837.50",
      "details": {
        "processing_status": "complete",
        "category": "dining",  // Teller categories: accommodation, advertising, bar, charity, clothing, dining, education, electronics, entertainment, fuel, general, groceries, health, home, income, insurance, investment, loan, office, phone, service, shopping, software, sport, tax, transport, transportation, utilities
        "counterparty": { "name": "Starbucks", "type": "organization" }
      },
      "links": { "self": "...", "account": "..." }
    }]
```

**CRITICAL — Amount sign convention is OPPOSITE of Plaid**:
- Teller: negative = money spent (debit), positive = money received (credit/income)
- Plaid: positive = money spent, negative = money received
- The new client must normalize this so downstream code always sees: positive = spending, negative = income (matching the existing convention in the codebase).

### Requirements

1. **Create `backend/src/integrations/teller_client.py`**:
   - Class `TellerClient` — async, uses `httpx.AsyncClient` with mTLS cert
   - Constructor takes: `access_token: str`, `cert_path: str`, `key_path: str`, `env: str = "sandbox"`
   - For sandbox env, skip mTLS cert (Teller sandbox doesn't require it) but still use access token as Basic Auth
   - For development/production, configure `httpx.AsyncClient(cert=(cert_path, key_path))`
   - Access token passed as HTTP Basic Auth: `auth=httpx.BasicAuth(access_token, "")`
   - Use circuit breaker from `utils.resilience.get_service_health_registry()` (same pattern as existing PlaidClient)
   - Methods:
     - `async def get_transactions(self, start_date: date, end_date: date) -> list[dict[str, Any]]` — paginate through all accounts, filter by date, **normalize amounts** (negate Teller amounts so positive = spending)
     - `async def get_accounts(self) -> list[dict[str, Any]]` — list linked accounts with balances
     - `async def get_balances(self, account_id: str) -> dict[str, Any]` — single account balance
     - `async def sync_daily(self) -> int` — fetch yesterday's transactions across all accounts, upsert into `transactions` table (dedup by `teller_transaction_id`), return count of new rows
   - Structured logging with structlog (match existing patterns: `teller_transactions_fetched`, `teller_sync_completed`, etc.)
   - Error handling: raise `IntegrationError` on HTTP errors. Log and re-raise.
   - HC-04 docstrings and comments everywhere

2. **Update `backend/src/config.py`**:
   - Replace Plaid config fields with Teller fields:
     ```python
     # --- Teller ---
     teller_access_token: str
     teller_cert_path: str = "/etc/tars/teller/cert.pem"
     teller_key_path: str = "/etc/tars/teller/key.pem"
     teller_env: str = "sandbox"  # sandbox | development | production
     ```
   - Remove all `plaid_*` fields

3. **Delete `backend/src/integrations/plaid_client.py`** (fully replaced)

4. **Update `backend/tests/test_plaid_client.py`** → rename to `test_teller_client.py` and rewrite for new client. Mock httpx responses matching Teller's JSON format. Test:
   - mTLS cert is passed in non-sandbox envs
   - Amount sign normalization (Teller negative → positive spending)
   - Pagination (mock multi-page responses)
   - `sync_daily` dedup logic
   - Sandbox mode skips cert

### Files to modify
- `backend/src/integrations/teller_client.py` (NEW)
- `backend/src/config.py` (MODIFY — replace plaid fields)
- `backend/src/integrations/plaid_client.py` (DELETE)
- `backend/tests/test_plaid_client.py` → `backend/tests/test_teller_client.py` (REWRITE)

### Do NOT touch
- `agents/finance.py` (Agent 3 handles this)
- `db/models.py` (Agent 2 handles this)
- `api/finance.py` (Agent 3 handles this)
- `scheduler/jobs.py` (Agent 6 handles this)

---

## Agent 2: DB Schema Migration (Alembic)

> **Skill**: `/feature-dev`
> **Ultrathinking**: NO — straightforward column renames and additions
> **Estimated files**: 2 modified, 1 new

### Context

Migrating from Plaid to Teller.io. The `transactions` table has Plaid-specific columns that need renaming. We also need new tables/columns for subscription tracking, budget system, and anomaly detection.

### Task

Create an Alembic migration and update the SQLAlchemy model to reflect Teller fields + new finance features.

### Requirements

1. **Create `backend/alembic/versions/005_migrate_plaid_to_teller.py`**:

   Follow the existing migration pattern (see `001_initial_schema.py` for style). This migration must:

   **a) Rename Plaid columns in `transactions` table**:
   - `plaid_transaction_id` → `teller_transaction_id`
   - `plaid_account_id` → `teller_account_id`
   - Keep all other columns as-is

   **b) Add new columns to `transactions` table**:
   - `counterparty_name VARCHAR(255)` — extracted from Teller's `details.counterparty.name`
   - `counterparty_type VARCHAR(50)` — "organization" or "person"
   - `transaction_type VARCHAR(50)` — Teller's type field (card_payment, transfer, etc.)
   - `description TEXT` — raw bank description string from Teller

   **c) Create `budgets` table** (new):
   ```sql
   CREATE TABLE budgets (
       id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       category VARCHAR(100) NOT NULL,
       monthly_limit NUMERIC(12, 2) NOT NULL,
       active BOOLEAN NOT NULL DEFAULT true,
       created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
   );
   CREATE UNIQUE INDEX idx_budgets_category ON budgets(category) WHERE active = true;
   ```

   **d) Add columns to `finance_summaries` table**:
   - `top_merchants JSONB DEFAULT '[]'::jsonb` — top 5 merchants for the period
   - `vs_previous_period JSONB DEFAULT '{}'::jsonb` — delta vs previous period (for trend detection)
   - `subscription_charges JSONB DEFAULT '[]'::jsonb` — recurring charges detected

   **e) Update indexes**:
   - Drop old unique index on `plaid_transaction_id`, create new one on `teller_transaction_id`
   - Add index: `idx_txns_counterparty ON transactions(counterparty_name)`
   - Add index: `idx_txns_type ON transactions(transaction_type)`
   - Add index: `idx_txns_recurring_date ON transactions(is_recurring, transaction_date DESC) WHERE is_recurring = true`

2. **Update `backend/src/db/models.py`** — the `Transaction` model:
   - Rename `plaid_transaction_id` → `teller_transaction_id`
   - Rename `plaid_account_id` → `teller_account_id`
   - Add `counterparty_name`, `counterparty_type`, `transaction_type`, `description` mapped columns
   - Update the `FinanceSummary` model to add `top_merchants`, `vs_previous_period`, `subscription_charges`
   - Add new `Budget` model class

3. **Update `backend/src/db/repositories/__init__.py`** — add `BudgetRepository` import if it uses a barrel pattern

### Migration must be reversible (include downgrade function).

### Files to modify
- `backend/alembic/versions/005_migrate_plaid_to_teller.py` (NEW)
- `backend/src/db/models.py` (MODIFY)
- `backend/src/db/repositories/__init__.py` (MODIFY if barrel)

### Do NOT touch
- `plaid_client.py` or `teller_client.py` (other agents)
- `agents/finance.py` (Agent 3)
- `api/finance.py` (Agent 3)

---

## Agent 3: Finance Agent Rewrite + Trend Engine

> **Skill**: `/feature-dev`
> **Ultrathinking**: YES — trend comparison logic and Claude escalation routing need careful design
> **Estimated files**: 3–4 modified

### Context

The `FinanceAgent` in `backend/src/agents/finance.py` currently only does basic period summaries with Gemini insights. It's missing: historical trend comparison, period-over-period deltas, `finance_summaries` table persistence, Claude escalation for complex analysis, and per-transaction list in response data. The DB now has `teller_transaction_id` / `teller_account_id` columns (Agent 2), and `TellerClient` exists (Agent 1).

### Existing Code to Understand

Read these files before making changes:
- `backend/src/agents/base.py` — `BaseAgent`, `AgentResult`, `AgentContext` dataclasses
- `backend/src/agents/finance.py` — current implementation
- `backend/src/models/gemini_client.py` — `GeminiClient` interface (used for `.generate()`)
- `backend/src/models/claude_spawner.py` — `ClaudeSpawner` interface (for escalation)
- `backend/src/db/models.py` — `Transaction`, `FinanceSummary` models (post-Agent-2 state)
- `backend/src/db/repositories/finance_summaries.py` — `FinanceSummaryRepository`

### Requirements

1. **Rewrite `backend/src/agents/finance.py`** with these capabilities:

   **a) Period summary (existing, keep but improve)**:
   - Query transactions by period (day/week/month) — same as now
   - But also include individual transaction list (up to 20) in `AgentResult.data["transactions"]`
   - Use `teller_transaction_id` / `teller_account_id` column names (post-migration)

   **b) Historical trend comparison (NEW)**:
   - After computing current period summary, query the `finance_summaries` table for the **previous equivalent period** (last week, last month)
   - Compute deltas: total_spent change (%), per-category changes
   - Include in insights prompt and in `AgentResult.data["trends"]`
   - Example: `{"total_change_pct": 15.2, "top_increase": {"category": "dining", "change_pct": 30.0}, "top_decrease": {"category": "transport", "change_pct": -12.0}}`

   **c) Persist summaries to `finance_summaries` table (NEW)**:
   - After computing a summary, upsert it into `finance_summaries` (using `FinanceSummaryRepository`)
   - Include: `period_type`, `period_start`, `period_end`, `total_spent`, `total_income`, `by_category`, `top_merchants`, `vs_previous_period`, `alerts`
   - This enables future trend lookups without recomputing from raw transactions

   **d) Claude escalation for complex analysis (NEW)**:
   - If the user's message contains keywords like "analyze", "deep dive", "compare", "why", "explain spending", or if `context.config.get("force_claude")` is True → route to Claude Code via `ClaudeSpawner`
   - Build a prompt that includes the current summary data + trends and asks Claude for deeper analysis
   - Otherwise keep using Gemini Flash (default path)
   - Handle Claude unavailability gracefully (HC-09): fall back to Gemini, then to local fallback

   **e) Enhanced Gemini prompt**:
   - Include trend data in the Gemini prompt (not just raw summary)
   - Ask Gemini to call out: "X category is up Y% vs last period", anomalies, etc.

2. **Update `backend/src/api/finance.py`**:
   - Add `transactions` list to the response (currently only in agent, not API)
   - Add `trends` dict to the response (period-over-period comparison)
   - Update `FinanceSummaryResponse` in `api/schemas.py` to include new fields:
     ```python
     class FinanceSummaryResponse(BaseModel):
         period: str
         date: str
         total_spent: float
         transactions: list[dict[str, Any]]
         month_to_date: dict[str, Any]
         alerts: list[str]
         trends: dict[str, Any] | None = None  # NEW
     ```

3. **Keep HC-04 compliance**: All operations remain read-only. Agent never writes to external services. Only reads transactions + writes summaries to local DB.

### Files to modify
- `backend/src/agents/finance.py` (REWRITE)
- `backend/src/api/finance.py` (MODIFY)
- `backend/src/api/schemas.py` (MODIFY — FinanceSummaryResponse)
- `backend/src/db/repositories/finance_summaries.py` (MODIFY — add upsert method)

### Do NOT touch
- `teller_client.py` (Agent 1)
- `db/models.py` (Agent 2)
- `scheduler/jobs.py` (Agent 6)
- `briefing.py` (Agent 6)

---

## Agent 4: Subscription Tracker + Anomaly Detection

> **Skill**: `/feature-dev`
> **Ultrathinking**: YES — recurring charge detection algorithm and anomaly thresholds need careful reasoning
> **Estimated files**: 2 new, 1 modified

### Context

The requirements doc (section 7.2.12) specifies two missing capabilities:
1. **Subscription tracking**: Identify recurring charges, track when they hit, alert on price increases
2. **Unusual charge alerts**: "New charge of $299 at Best Buy — expected?"

Neither exists in the codebase. The `transactions` table has an `is_recurring` boolean field and a `counterparty_name` field (post-Agent-2 migration).

### Existing Code to Understand

- `backend/src/db/models.py` — `Transaction` model (has `is_recurring`, `counterparty_name`, `merchant_name`, `amount`, `category`)
- `backend/src/agents/base.py` — `BaseAgent`, `AgentResult`
- `backend/src/utils/resilience.py` — patterns for health tracking
- `backend/src/integrations/notification_service.py` — how alerts are sent

### Requirements

1. **Create `backend/src/agents/subscription_tracker.py`** (new module, not a full agent — a utility used by FinanceAgent and scheduler):

   This is a **utility class** `SubscriptionTracker`, not a standalone BaseAgent. It's called by the FinanceAgent and by a scheduled job.

   **Detection algorithm**:
   - Query the last 90 days of transactions
   - Group by `merchant_name` (or `counterparty_name` as fallback)
   - A merchant is "recurring" if it appears in **3+ distinct months** OR **has charges with consistent intervals** (within ±3 days of expected cycle)
   - Supported cycles: weekly (~7 days), biweekly (~14 days), monthly (~30 days), annual (~365 days)
   - When a recurring merchant is detected, update `is_recurring = True` on matching transactions

   **Price increase detection**:
   - For each recurring merchant, compare the most recent charge amount to the average of previous charges
   - If the latest charge is >10% higher than the rolling average → flag as price increase
   - Return: `{"merchant": "Netflix", "previous_avg": 15.49, "current": 22.99, "increase_pct": 48.4}`

   **Methods**:
   ```python
   class SubscriptionTracker:
       async def detect_recurring(self, session: AsyncSession) -> list[RecurringCharge]
       async def check_price_changes(self, session: AsyncSession) -> list[PriceChange]
       async def get_subscription_summary(self, session: AsyncSession) -> dict[str, Any]
   ```

   **Data classes** (in the same file):
   ```python
   @dataclass
   class RecurringCharge:
       merchant: str
       amount: float
       cycle: str  # "weekly", "biweekly", "monthly", "annual"
       last_charged: date
       next_expected: date

   @dataclass
   class PriceChange:
       merchant: str
       previous_avg: float
       current_amount: float
       change_pct: float
   ```

2. **Create `backend/src/agents/anomaly_detector.py`** (utility class, same pattern):

   **`AnomalyDetector` class** — detects unusual individual transactions.

   **Detection rules**:
   - **Large transaction**: Amount > 2× the user's average transaction size for that category (based on last 90 days)
   - **New merchant**: First time seeing this merchant (no prior transactions in DB)
   - **Category spike**: Single transaction > 50% of the entire month's spending in that category
   - **Off-pattern**: Transaction at a merchant the user normally visits (recurring) but with an amount 3× the usual

   **Methods**:
   ```python
   class AnomalyDetector:
       async def scan_recent(self, session: AsyncSession, lookback_days: int = 1) -> list[Anomaly]
       async def check_transaction(self, session: AsyncSession, txn: Transaction) -> Anomaly | None
   ```

   **Data class**:
   ```python
   @dataclass
   class Anomaly:
       transaction_id: str
       merchant: str
       amount: float
       date: date
       reason: str  # "large_transaction", "new_merchant", "category_spike", "off_pattern"
       details: str  # Human-readable: "New charge of $299 at Best Buy — first time seeing this merchant"
       severity: str  # "info", "warning"
   ```

3. **Update `backend/src/agents/finance.py`** (only the parts that integrate these):
   - Import `SubscriptionTracker` and `AnomalyDetector`
   - In `execute()`, after building the summary, also run:
     - `subscription_tracker.get_subscription_summary(session)` → include in `AgentResult.data["subscriptions"]`
     - `anomaly_detector.scan_recent(session, lookback_days=1)` → include in `AgentResult.data["anomalies"]`
   - If anomalies have severity "warning", include them in the text response prominently

### Files to modify
- `backend/src/agents/subscription_tracker.py` (NEW)
- `backend/src/agents/anomaly_detector.py` (NEW)
- `backend/src/agents/finance.py` (MODIFY — add integration calls to execute())

### Do NOT touch
- `teller_client.py` (Agent 1)
- `db/models.py` (Agent 2)
- `scheduler/jobs.py` (Agent 6)

---

## Agent 5: Budget System

> **Skill**: `/feature-dev`
> **Ultrathinking**: NO — straightforward CRUD + comparison logic
> **Estimated files**: 3 new, 2 modified

### Context

The requirements doc specifies: "Budget vs. actual (if user sets budgets): 'You've used 78% of your dining budget with 10 days left'". This doesn't exist yet. Agent 2 created the `budgets` table and `Budget` model.

### Existing Code to Understand

- `backend/src/db/models.py` — `Budget` model (post-Agent-2: id, category, monthly_limit, active, created_at, updated_at)
- `backend/src/db/models.py` — `Transaction` model
- `backend/src/api/router.py` — how routes are aggregated
- `backend/src/api/schemas.py` — existing Pydantic response models
- `backend/src/dependencies.py` — FastAPI dependency injection pattern

### Requirements

1. **Create `backend/src/db/repositories/budgets.py`**:
   ```python
   class BudgetRepository:
       async def create(self, category: str, monthly_limit: Decimal) -> Budget
       async def get_active(self) -> list[Budget]
       async def get_by_category(self, category: str) -> Budget | None
       async def update_limit(self, category: str, monthly_limit: Decimal) -> Budget | None
       async def deactivate(self, category: str) -> bool
   ```

2. **Create `backend/src/api/budgets.py`** — REST endpoints:
   ```
   GET  /api/v1/finance/budgets          → list all active budgets with current month spending vs limit
   POST /api/v1/finance/budgets          → create/update a budget { "category": "dining", "monthly_limit": 200.00 }
   DELETE /api/v1/finance/budgets/:category → deactivate a budget
   GET  /api/v1/finance/budgets/status   → budget status summary (all categories, % used, days remaining)
   ```

   The `GET /budgets` and `GET /budgets/status` endpoints must:
   - Query the `budgets` table for active budgets
   - For each budget, query `transactions` for month-to-date spending in that category
   - Calculate: `spent`, `remaining`, `percent_used`, `days_remaining_in_month`, `on_track` (boolean: will they exceed if spending continues at current pace?)
   - `on_track` = True if `(spent / days_elapsed) * total_days_in_month <= monthly_limit`

3. **Add Pydantic schemas to `backend/src/api/schemas.py`**:
   ```python
   class BudgetCreateRequest(BaseModel):
       category: str
       monthly_limit: float

   class BudgetStatus(BaseModel):
       category: str
       monthly_limit: float
       spent: float
       remaining: float
       percent_used: float
       days_remaining: int
       on_track: bool

   class BudgetListResponse(BaseModel):
       budgets: list[BudgetStatus]
   ```

4. **Wire into `backend/src/api/router.py`** — import and include the budgets router.

5. **Integrate with FinanceAgent** (`backend/src/agents/finance.py`):
   - At the end of `execute()`, query active budgets and compute status
   - Include in `AgentResult.data["budget_status"]` — list of `{"category": "dining", "percent_used": 78, "remaining": 44.00, "on_track": false}`
   - If any budget is >80% used, add to the text response: "Budget alert: Dining at 78% ($156/$200) with 10 days left"

### Files to modify
- `backend/src/db/repositories/budgets.py` (NEW)
- `backend/src/api/budgets.py` (NEW)
- `backend/src/api/schemas.py` (MODIFY — add budget schemas)
- `backend/src/api/router.py` (MODIFY — include budget router)
- `backend/src/agents/finance.py` (MODIFY — add budget status to execute)

### Do NOT touch
- `teller_client.py`, `db/models.py`, `scheduler/jobs.py`, `briefing.py`

---

## Agent 6: Morning Briefing + Scheduler Wiring

> **Skill**: `/feature-dev`
> **Ultrathinking**: NO — plumbing/wiring work, not algorithmic
> **Estimated files**: 2–3 modified

### Context

The briefing agent (`backend/src/agents/briefing.py`) already fetches finance data in `_fetch_finance_data()` and builds a finance section. However:
1. It references `Transaction` model with Plaid column names — needs Teller column update
2. There's no scheduled job for daily Teller transaction sync
3. There's no scheduled job for subscription/anomaly scanning
4. The finance section in the briefing doesn't include budget status, subscription alerts, or anomalies

The scheduler (`backend/src/scheduler/jobs.py`) has no finance-related jobs at all.

### Existing Code to Understand

- `backend/src/agents/briefing.py` — read the `_fetch_finance_data()` method and `_build_finance_section()` function (around lines 533-590 and 859-872)
- `backend/src/scheduler/jobs.py` — full file, understand the `_run_orchestrated_job` pattern and direct-job pattern
- `backend/src/integrations/teller_client.py` (Agent 1's output) — `sync_daily()` method

### Requirements

1. **Update `backend/src/agents/briefing.py`**:

   **a) `_fetch_finance_data()` method** — this method queries the Transaction table directly. Update column references:
   - No column name changes needed in the SELECT (it uses `merchant_name`, `amount`, `category` which are unchanged)
   - BUT add to the query: fetch budget status and include it
   - Add: query anomalies from yesterday (use `AnomalyDetector.scan_recent(session, lookback_days=1)`)
   - Add: query subscription charges due soon (next 7 days) from `SubscriptionTracker`

   **b) `_build_finance_section()` function** — enhance the briefing section:
   - Current output: `yesterday_spending`, `month_to_date`, `note`
   - Add: `budget_alerts` — list of budget warnings for any category >80% used
   - Add: `subscription_alerts` — price increases detected
   - Add: `anomalies` — unusual charges from yesterday
   - Example output:
     ```python
     {
         "yesterday_spending": "$47.23 (2 transactions)",
         "month_to_date": "$1,247.00",
         "budget_alerts": ["Dining at 78% ($156/$200) — 10 days left"],
         "subscription_alerts": ["Netflix increased from $15.49 to $22.99 (+48%)"],
         "anomalies": [],
         "note": "",
     }
     ```

2. **Update `backend/src/scheduler/jobs.py`** — add two new scheduled jobs:

   **a) Teller daily sync** — 5:00 AM daily (before briefing at 5:50 AM):
   ```python
   async def teller_sync_job(orchestrator: Orchestrator) -> None:
       """Sync yesterday's bank transactions from Teller.io."""
   ```
   - This is a **direct job** (not orchestrated), similar to `expire_approvals_job`
   - Instantiate `TellerClient` from config, call `sync_daily()`
   - After sync, run `SubscriptionTracker.detect_recurring()` to update `is_recurring` flags
   - Run `AnomalyDetector.scan_recent()` and send alerts via `notification_service` for any severity="warning" anomalies
   - Log: `teller_sync_job_completed`, count of new transactions, anomalies found

   **b) Register in `create_scheduler()`**:
   ```python
   scheduler.add_job(
       teller_sync_job,
       CronTrigger(hour=5, minute=0),
       id="teller_sync",
       name="Daily Teller Transaction Sync",
       kwargs={"orchestrator": orchestrator},
   )
   ```
   - Add to the logged jobs list

### Files to modify
- `backend/src/agents/briefing.py` (MODIFY — `_fetch_finance_data`, `_build_finance_section`)
- `backend/src/scheduler/jobs.py` (MODIFY — add `teller_sync_job`, register in scheduler)

### Do NOT touch
- `teller_client.py`, `db/models.py`, `finance.py` agent, `api/finance.py`

---

## Agent 7: Tests (All Finance)

> **Skill**: `/feature-dev`
> **Ultrathinking**: NO — test writing is systematic, not design-heavy
> **Estimated files**: 5–6 modified/new

### Context

After Agents 1–6, the finance system has been significantly expanded. All tests need updating/creating. The existing `backend/tests/test_finance_agent.py` tests the old FinanceAgent and will need updates. The existing `backend/tests/test_plaid_client.py` was replaced by Agent 1.

### Testing Conventions (from CLAUDE.md)

- `pytest` + `pytest-asyncio` for async tests
- Mock ALL external API calls — never hit real Teller, Gemini, Claude
- `conftest.py` provides test database, mock Redis, mock Gemini/Claude clients
- Test file per module: `test_<module>.py`

### Requirements

1. **`backend/tests/test_teller_client.py`** (Agent 1 should have created a basic version — verify and expand):
   - Test mTLS cert configuration (non-sandbox)
   - Test sandbox mode (no cert)
   - Test `get_transactions()` with mocked httpx responses matching Teller JSON format
   - Test amount normalization (Teller negative → positive spending)
   - Test pagination (multiple pages via `from_id`)
   - Test `get_accounts()` and `get_balances()`
   - Test `sync_daily()` — upserts, dedup by `teller_transaction_id`
   - Test error handling (HTTP 401, 500, timeouts)

2. **`backend/tests/test_finance_agent.py`** (update existing):
   - Update `_mock_summary()` to match new data shape (includes `transactions` list, `trends`, `subscriptions`, `anomalies`, `budget_status`)
   - Test trend comparison: mock `FinanceSummaryRepository.get_by_period()` to return a previous period, verify trends are computed
   - Test summary persistence: verify `FinanceSummaryRepository.create()` is called after execute
   - Test Claude escalation: when message contains "analyze", verify `ClaudeSpawner` is called
   - Test Claude fallback: when Claude fails, verify Gemini is used (HC-09)
   - Test budget status integration: mock budget data, verify it appears in result
   - Keep existing HC-04 compliance test

3. **`backend/tests/test_subscription_tracker.py`** (NEW):
   - Test recurring detection: create 90 days of mock transactions with a merchant appearing monthly → detected as recurring
   - Test non-recurring: merchant appearing only once → not flagged
   - Test price increase detection: recurring merchant with higher recent charge → flagged
   - Test no price change: consistent amounts → no flag
   - Test edge cases: merchant with only 2 months of data, variable amounts

4. **`backend/tests/test_anomaly_detector.py`** (NEW):
   - Test large transaction: amount > 2× category average → flagged
   - Test new merchant: first-time merchant → flagged
   - Test category spike: single txn > 50% of monthly category spend → flagged
   - Test normal transaction: within expected ranges → no flag
   - Test empty history: new user with no prior data → handle gracefully

5. **`backend/tests/test_budget_api.py`** (NEW):
   - Test `POST /finance/budgets` — create a budget
   - Test `GET /finance/budgets` — list with spending status
   - Test `GET /finance/budgets/status` — full status with on_track calculation
   - Test `DELETE /finance/budgets/:category` — deactivate
   - Test budget at 0% (no spending), 50%, 80% (warning), 100%+ (exceeded)
   - Test `on_track` calculation: verify pace math

6. **`backend/tests/test_finance_api.py`** (update existing if it exists, or create):
   - Test `GET /finance/summary` response includes `trends` field
   - Test period parameter (day, week, month)
   - Test with no transactions → empty response, no crash

### Files to modify/create
- `backend/tests/test_teller_client.py` (VERIFY/EXPAND)
- `backend/tests/test_finance_agent.py` (UPDATE)
- `backend/tests/test_subscription_tracker.py` (NEW)
- `backend/tests/test_anomaly_detector.py` (NEW)
- `backend/tests/test_budget_api.py` (NEW)
- `backend/tests/test_finance_api.py` (UPDATE or NEW)

### Do NOT touch
- Any source code files — this agent only writes tests
