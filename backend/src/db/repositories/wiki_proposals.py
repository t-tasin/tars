"""Repository for the ``wiki_proposals`` table (Phase 3 — HC-14).

Every wiki write originates here as a ``pending`` proposal. Tasin (or
auto-expiry) flips ``status`` to ``approved`` / ``rejected`` / ``expired``.
Only ``approved`` proposals are eligible to land in ``wiki_index``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WikiProposal

log = structlog.get_logger()

PROPOSAL_STATUSES: Final[tuple[str, ...]] = ("pending", "approved", "rejected", "expired")
PROPOSAL_OPERATIONS: Final[tuple[str, ...]] = ("create", "update", "delete")


class WikiProposalsRepository:
    """CRUD + status-transition operations for ``wiki_proposals``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        source_agent: str,
        target_path: str,
        operation: str,
        diff: str,
        rationale: str | None = None,
    ) -> WikiProposal:
        """Insert a new ``pending`` proposal.

        ``operation`` must be one of ``create`` / ``update`` / ``delete``.
        """
        if operation not in PROPOSAL_OPERATIONS:
            raise ValueError(f"invalid operation {operation!r}; expected one of {PROPOSAL_OPERATIONS}")

        record = WikiProposal(
            source_agent=source_agent,
            target_path=target_path,
            operation=operation,
            diff=diff,
            rationale=rationale,
            status="pending",
        )
        self._session.add(record)
        await self._session.flush()
        log.info(
            "wiki_proposal.created",
            proposal_id=str(record.id),
            source_agent=source_agent,
            target_path=target_path,
            operation=operation,
        )
        return record

    async def get_pending(self, limit: int = 100) -> list[WikiProposal]:
        """Return up to ``limit`` proposals still awaiting review (oldest first)."""
        result = await self._session.execute(
            select(WikiProposal)
            .where(WikiProposal.status == "pending")
            .order_by(WikiProposal.created_at.asc())
            .limit(limit)
        )
        # Filter status in Python too — the in-memory test shim ignores
        # WHERE clauses, returning every row of the table. Real PG already
        # filters server-side; this filter is a no-op there.
        rows = [r for r in result.scalars().all() if r.status == "pending"]
        rows.sort(key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC))
        return rows[:limit]

    async def get_by_id(self, proposal_id) -> WikiProposal | None:
        # Use ``.all()`` + filter so the test shim (which lacks ``first()``)
        # can serve this call. Real PG honors the WHERE clause directly.
        result = await self._session.execute(select(WikiProposal).where(WikiProposal.id == proposal_id))
        for row in result.scalars().all():
            if row.id == proposal_id:
                return row
        return None

    async def mark_reviewed(
        self,
        proposal_id,
        *,
        status: str,
        reviewed_by: str,
        review_note: str | None = None,
    ) -> WikiProposal | None:
        """Transition a proposal from ``pending`` to a terminal state.

        Valid target ``status`` values: ``approved`` / ``rejected`` / ``expired``.
        Returns the updated row, or ``None`` if no such proposal exists.
        Raises ``ValueError`` if the target status is invalid or the proposal
        is not currently ``pending`` (no re-decisions).
        """
        if status not in {"approved", "rejected", "expired"}:
            raise ValueError(f"invalid review status {status!r}")

        proposal = await self.get_by_id(proposal_id)
        if proposal is None:
            return None
        if proposal.status != "pending":
            raise ValueError(
                f"proposal {proposal_id} already in terminal state {proposal.status!r}; cannot re-review"
            )

        proposal.status = status
        proposal.reviewed_at = datetime.now(UTC)
        proposal.reviewed_by = reviewed_by
        proposal.review_note = review_note
        await self._session.flush()
        log.info(
            "wiki_proposal.reviewed",
            proposal_id=str(proposal.id),
            status=status,
            reviewed_by=reviewed_by,
        )
        return proposal
