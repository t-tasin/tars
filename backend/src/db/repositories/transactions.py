"""Repository for the transactions table."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Transaction

log = structlog.get_logger()


class TransactionRepository:
    """CRUD operations for the ``transactions`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> Transaction:
        """Insert a new transaction record."""
        txn = Transaction(**kwargs)
        self._session.add(txn)
        await self._session.flush()
        return txn

    async def get_recent(self, days: int = 7, limit: int = 100) -> list[Transaction]:
        """Fetch transactions from the last N days."""
        cutoff = date.today() - timedelta(days=days)
        result = await self._session.execute(
            select(Transaction)
            .where(Transaction.transaction_date >= cutoff)
            .order_by(Transaction.transaction_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_date_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[Transaction]:
        """Fetch transactions within a date range (inclusive)."""
        result = await self._session.execute(
            select(Transaction)
            .where(
                Transaction.transaction_date >= start_date,
                Transaction.transaction_date <= end_date,
            )
            .order_by(Transaction.transaction_date.desc())
        )
        return list(result.scalars().all())

    async def get_by_category(
        self,
        category: str,
        limit: int = 50,
    ) -> list[Transaction]:
        """Fetch transactions for a specific category."""
        result = await self._session.execute(
            select(Transaction)
            .where(Transaction.category == category)
            .order_by(Transaction.transaction_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def bulk_create(self, transactions: list[dict[str, Any]]) -> int:
        """Insert multiple transactions. Returns count inserted."""
        count = 0
        for data in transactions:
            self._session.add(Transaction(**data))
            count += 1
        await self._session.flush()
        return count
