"""Async database session management for T.A.R.S."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_settings

log = structlog.get_logger()

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=_settings.debug,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Yield an async database session with automatic commit/rollback.

    Usage::

        async with get_db_session() as session:
            result = await session.execute(select(SomeModel))
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Test database connectivity at startup."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("database_connected", url=engine.url.render_as_string(hide_password=True))


async def close_db() -> None:
    """Dispose the engine and release all pooled connections."""
    await engine.dispose()
    log.info("database_engine_disposed")
