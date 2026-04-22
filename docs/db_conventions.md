# Database Conventions

## Basics
- PostgreSQL 16, db/user: `tars`
- Connection: `postgresql+asyncpg://tars:{password}@tars-db:5432/tars`

## Primary keys
`id UUID PRIMARY KEY DEFAULT gen_random_uuid()`

## Timestamps
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` on every table
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` on mutables, auto-update trigger

## Enums
`CREATE TYPE ... AS ENUM` — e.g. `task_status`, `task_priority`, `approval_status`, `risk_tier`, `email_tier`, `health_status`, `job_status`, `autonomy_class`, `tone_kind`.

## JSONB
Flexible/nested: metadata, previews, payloads, evidence. Default `'{}'::jsonb` or `'[]'::jsonb`.

## Naming
- snake_case tables/columns
- Plural table names
- FK: `{referenced_table_singular}_id`

## Indexes
- Every WHERE/ORDER BY/JOIN col
- Partial indexes: `WHERE status='pending'`, `WHERE active=true`
- GIN for JSONB + trigram text search

## Migrations
Alembic. Numbered Python files in `alembic/versions/`. Run `alembic upgrade head`. Never `ALTER TABLE` manually.

## Partitioning
- `world_state` — monthly RANGE partitions, auto-drop >1yr
- `audit_log` — quarterly, retained 3yr

## Key Tables

### Core
`conversations`, `messages`, `agent_tasks`, `agent_outputs`, `audit_log`

### AI
`model_usage`, `approvals`, `feedback_log`, `evals`

### Agents' data
`email_classifications`, `briefings`, `contacts`, `system_health_log`, `job_listings`, `job_applications`, `wardrobe_items`, `wardrobe_outfits`, `transactions`, `finance_summaries`, `health_data`

### Infra
`config`, `world_state` (partitioned), `autonomy_budget`, `autonomy_limits`, `wiki_proposals`, `wiki_index`

### Sensors
Readings land in `world_state` (jsonb payload, indexed by source + recorded_at).

## Encryption

- Credentials (OAuth tokens, API keys): `pgcrypto` symmetric encrypt, key in KMS or encrypted env file
- `health_data.private_notes`: pgcrypto
- `world_state.payload` for location/presence: pgcrypto
- `wiki_proposals.evidence` containing PII: pgcrypto

## Backups

- `pg_dump` nightly 03:00, encrypted w/ age, stored off-Node 2 (external drive or S3-compatible)
- Restore test weekly (automated, reads dump, verifies row count tolerance)
