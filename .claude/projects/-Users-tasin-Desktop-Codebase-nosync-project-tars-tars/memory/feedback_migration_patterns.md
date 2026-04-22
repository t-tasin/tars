---
name: migration-enum-and-asyncpg-patterns
description: Alembic migration pitfalls with PostgreSQL enums, asyncpg driver, and trigger functions — lessons from 2026-03-13 deployment debugging
type: feedback
---

## Alembic + asyncpg + PostgreSQL Enum Migration Rules

### 1. SQLAlchemy ORM enum types auto-create PG types during migrations

**Problem:** When `env.py` imports `Base` (for `target_metadata`), ORM models register enum types with `create_type=True` (default). During `op.create_table()`, SQLAlchemy fires `before_create` events that emit `CREATE TYPE` — even if the migration's own `sa.Enum` objects have `create_type=False`. This causes "type already exists" errors.

**Why:** SQLAlchemy's `SchemaType._set_table` registers `_on_table_create` event listeners on table columns. The ORM metadata's enum types interfere with migration-level types through these events. Setting `create_type=False` on migration-level enums is NOT sufficient because the ORM metadata's enums (loaded via Base import) still fire.

**How to apply:**
- In `db/models.py`: All enum columns MUST use explicit `sa.Enum(PythonEnum, name="pg_type_name", create_type=False)` type objects — never bare `Mapped[SomeEnum]` without an explicit type.
- In `alembic/env.py`: Monkey-patch `Enum._on_table_create` and `_on_metadata_create` to no-ops. Functions MUST be properly named (not lambdas) because SQLAlchemy's `portable_instancemethod` looks up methods by `__name__` at call time. A lambda has `__name__='<lambda>'` which causes `AttributeError`.
- In migrations: Create enum types via raw SQL with `DO $$ BEGIN CREATE TYPE ... EXCEPTION WHEN duplicate_object THEN NULL; END $$` for idempotent handling.

### 2. asyncpg cannot execute multiple SQL statements in one call

**Problem:** `op.execute()` with multiple SQL statements (e.g., `CREATE FUNCTION ...; CREATE TRIGGER ...;`) fails with "cannot insert multiple commands into a prepared statement".

**Why:** asyncpg uses PostgreSQL's extended query protocol which prepares statements individually. Unlike psycopg2, it cannot batch multiple statements.

**How to apply:** Always use one `op.execute()` per SQL statement. Never combine CREATE FUNCTION + CREATE TRIGGER (or any other multi-statement SQL) in a single `op.execute()` call.

### 3. Reuse shared trigger functions, don't create per-table duplicates

**Problem:** Migration 006 created per-table trigger functions (`update_workout_splits_updated_at()`, etc.) instead of reusing the shared `trigger_set_updated_at()` from migration 001.

**Why:** Unnecessary duplication. Migration 001 already creates a generic `trigger_set_updated_at()` function that works for any table with an `updated_at` column.

**How to apply:** When adding `updated_at` triggers in new migrations, always reference `trigger_set_updated_at()` from migration 001. Use trigger name `set_updated_at` (consistent with 001's pattern). Never create per-table trigger functions.

### 4. Consistent trigger function naming

**Problem:** Migration 002 referenced `update_updated_at_column()` which doesn't exist — the function created in 001 is `trigger_set_updated_at()`.

**How to apply:** The canonical trigger function name is `trigger_set_updated_at()`. The canonical trigger name is `set_updated_at`. Always verify function names match what was actually created in earlier migrations.
