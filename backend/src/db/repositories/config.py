"""Repository for the config table."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Config

log = structlog.get_logger()


class ConfigRepository:
    """CRUD operations for the ``config`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, namespace: str, key: str) -> Any:
        """Get a single config value, unwrapped from the ``{"v": ...}`` wrapper.

        Returns ``None`` if the key does not exist.
        """
        result = await self._session.execute(
            select(Config).where(
                Config.namespace == namespace,
                Config.key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return row.value.get("v", row.value)

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        updated_by: str = "system",
    ) -> Config:
        """Upsert a single config value."""
        result = await self._session.execute(
            select(Config).where(
                Config.namespace == namespace,
                Config.key == key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.value = {"v": value}
            existing.updated_by = updated_by
            await self._session.flush()
            return existing

        row = Config(
            namespace=namespace,
            key=key,
            value={"v": value},
            updated_by=updated_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_namespace(self, namespace: str) -> dict[str, Any]:
        """Get all config key-value pairs for a namespace.

        Returns a flat dict with unwrapped values::

            {"time": "05:50", "sections": [...]}
        """
        result = await self._session.execute(select(Config).where(Config.namespace == namespace))
        rows = result.scalars().all()
        return {row.key: row.value.get("v", row.value) for row in rows}

    async def set_namespace(
        self,
        namespace: str,
        values: dict[str, Any],
        updated_by: str = "system",
    ) -> int:
        """Set multiple keys within a namespace. Returns count upserted."""
        count = 0
        for key, value in values.items():
            await self.set(namespace, key, value, updated_by=updated_by)
            count += 1
        return count

    async def get_all(self) -> dict[str, dict[str, Any]]:
        """Get all config as a nested dict: ``{namespace: {key: value}}``."""
        result = await self._session.execute(select(Config))
        rows = result.scalars().all()
        config: dict[str, dict[str, Any]] = {}
        for row in rows:
            config.setdefault(row.namespace, {})[row.key] = row.value.get("v", row.value)
        return config

    async def delete(self, namespace: str, key: str) -> bool:
        """Delete a config entry. Returns True if a row was deleted."""
        result = await self._session.execute(
            delete(Config).where(
                Config.namespace == namespace,
                Config.key == key,
            )
        )
        await self._session.flush()
        return result.rowcount > 0  # type: ignore[operator]
