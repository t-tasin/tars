"""FastAPI dependency injection for T.A.R.S."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import verify_api_key
from src.config import Settings, get_settings
from src.db.session import async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session that auto-commits/rollbacks."""
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Re-export so routers can do: Depends(get_settings), Depends(verify_auth)
verify_auth = verify_api_key
