"""Tests for ``db.repositories.wiki_proposals.WikiProposalsRepository``.

Covers create + retrieve + status transitions for the CuratorAgent
proposal lifecycle (HC-14). PG-only assertions are gated behind
``requires_pg`` because the in-memory shim doesn't model WHERE/ORDER BY.
"""

from __future__ import annotations

import os
import uuid

import pytest

from db.models import WikiProposal
from db.repositories.wiki_proposals import WikiProposalsRepository

_HAS_PG = bool(os.environ.get("TEST_DATABASE_URL", ""))
requires_pg = pytest.mark.skipif(not _HAS_PG, reason="Requires real PostgreSQL")


@pytest.mark.asyncio
async def test_create_inserts_pending_proposal(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="identity/preferences.md",
        operation="create",
        diff="+ new pref line",
        rationale="Captured during evening review.",
    )
    assert proposal.status == "pending"
    assert proposal.source_agent == "CuratorAgent"
    assert proposal.target_path == "identity/preferences.md"
    assert proposal.operation == "create"
    assert proposal.diff == "+ new pref line"
    assert proposal.rationale == "Captured during evening review."
    assert proposal.reviewed_at is None
    assert proposal.reviewed_by is None


@pytest.mark.asyncio
async def test_create_rejects_invalid_operation(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    with pytest.raises(ValueError, match="invalid operation"):
        await repo.create(
            source_agent="CuratorAgent",
            target_path="identity/preferences.md",
            operation="overwrite",  # not in {create,update,delete}
            diff="...",
        )


@pytest.mark.asyncio
async def test_get_pending_returns_only_pending(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    p1 = await repo.create(
        source_agent="CuratorAgent",
        target_path="a.md",
        operation="create",
        diff="x",
    )
    p2 = await repo.create(
        source_agent="CuratorAgent",
        target_path="b.md",
        operation="update",
        diff="y",
    )

    # Approve p2 — should disappear from pending list.
    await repo.mark_reviewed(p2.id, status="approved", reviewed_by="tasin")

    pending = await repo.get_pending()
    pending_ids = {p.id for p in pending}
    assert p1.id in pending_ids
    assert p2.id not in pending_ids


@pytest.mark.asyncio
async def test_mark_reviewed_approve(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="x.md",
        operation="create",
        diff="...",
    )
    updated = await repo.mark_reviewed(
        proposal.id,
        status="approved",
        reviewed_by="tasin",
        review_note="LGTM",
    )
    assert updated is not None
    assert updated.status == "approved"
    assert updated.reviewed_by == "tasin"
    assert updated.review_note == "LGTM"
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_mark_reviewed_reject(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="y.md",
        operation="update",
        diff="...",
    )
    updated = await repo.mark_reviewed(proposal.id, status="rejected", reviewed_by="tasin")
    assert updated is not None
    assert updated.status == "rejected"


@pytest.mark.asyncio
async def test_mark_reviewed_expired(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="z.md",
        operation="delete",
        diff="...",
    )
    updated = await repo.mark_reviewed(
        proposal.id,
        status="expired",
        reviewed_by="auto-expired",
    )
    assert updated is not None
    assert updated.status == "expired"
    assert updated.reviewed_by == "auto-expired"


@pytest.mark.asyncio
async def test_mark_reviewed_invalid_status(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="a.md",
        operation="create",
        diff="...",
    )
    with pytest.raises(ValueError, match="invalid review status"):
        await repo.mark_reviewed(proposal.id, status="archived", reviewed_by="tasin")


@pytest.mark.asyncio
async def test_mark_reviewed_no_double_decision(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="a.md",
        operation="create",
        diff="...",
    )
    await repo.mark_reviewed(proposal.id, status="approved", reviewed_by="tasin")
    with pytest.raises(ValueError, match="already in terminal state"):
        await repo.mark_reviewed(proposal.id, status="rejected", reviewed_by="tasin")


@pytest.mark.asyncio
async def test_mark_reviewed_missing_returns_none(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    result = await repo.mark_reviewed(uuid.uuid4(), status="approved", reviewed_by="tasin")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_round_trip(test_db) -> None:
    repo = WikiProposalsRepository(test_db)
    proposal = await repo.create(
        source_agent="CuratorAgent",
        target_path="round.md",
        operation="create",
        diff="...",
    )
    fetched = await repo.get_by_id(proposal.id)
    assert fetched is not None
    assert fetched.id == proposal.id


@requires_pg
@pytest.mark.asyncio
async def test_status_check_constraint_rejects_invalid(test_db) -> None:
    """Real PG must reject status values outside the allowed set."""
    from sqlalchemy.exc import IntegrityError

    bad = WikiProposal(
        source_agent="CuratorAgent",
        target_path="a.md",
        operation="create",
        diff="...",
        status="bogus",
    )
    test_db.add(bad)
    with pytest.raises(IntegrityError):
        await test_db.flush()
