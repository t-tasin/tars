from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
import sqlalchemy.sql.sqltypes as _sqltypes

from src.db.models import Base

# Prevent SQLAlchemy from auto-creating PostgreSQL enum types during
# op.create_table() in migrations.  Migrations handle enum DDL explicitly;
# without this patch the ORM metadata's enum types (registered via
# Base.metadata import) fire before_create events that duplicate the
# CREATE TYPE statements and cause "type already exists" errors.
# NOTE: Functions must keep original names because SQLAlchemy's
# portable_instancemethod looks them up by __name__ at call time.
def _on_table_create(self, target, bind, **kw):  # noqa: ARG001
    pass

def _on_metadata_create(self, target, bind, **kw):  # noqa: ARG001
    pass

_sqltypes.Enum._on_table_create = _on_table_create
_sqltypes.Enum._on_metadata_create = _on_metadata_create

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment if available, so we never rely
# solely on the value baked into alembic.ini.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
