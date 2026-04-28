"""008_wiki_tables

Phase 3 Sovereign Data Layer (P3-10). Creates ``wiki_proposals`` and
``wiki_index`` to back CuratorAgent (HC-14): every wiki write is a
proposal awaiting Tasin approval; only approved chunks are embedded
into Qdrant ``tasin_wiki`` and recorded in ``wiki_index`` for
deterministic re-embed / rebuild.

Revision ID: 008_wiki_tables
Revises: 007_add_world_state_partitioned
Create Date: 2026-04-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

from alembic import op

revision: str = "008_wiki_tables"
down_revision: str = "007_add_world_state_partitioned"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. wiki_proposals — CuratorAgent edit proposals awaiting review.
    # ---------------------------------------------------------------
    op.create_table(
        "wiki_proposals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("source_agent", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("reviewed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_wiki_proposals_status",
        ),
        sa.CheckConstraint(
            "operation IN ('create','update','delete')",
            name="ck_wiki_proposals_operation",
        ),
    )
    op.create_index(
        "idx_wiki_proposals_status_created",
        "wiki_proposals",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_wiki_proposals_target_path",
        "wiki_proposals",
        ["target_path"],
    )

    # ---------------------------------------------------------------
    # 2. wiki_index — live index of every approved chunk in Qdrant.
    # ---------------------------------------------------------------
    op.create_table(
        "wiki_index",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("qdrant_point_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "embedded_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.UniqueConstraint("qdrant_point_id", name="uq_wiki_index_qdrant_point_id"),
        sa.UniqueConstraint("path", "chunk_index", name="uq_wiki_index_path_chunk"),
    )
    op.create_index(
        "idx_wiki_index_content_hash",
        "wiki_index",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_wiki_index_content_hash", table_name="wiki_index")
    op.drop_table("wiki_index")

    op.drop_index("idx_wiki_proposals_target_path", table_name="wiki_proposals")
    op.drop_index("idx_wiki_proposals_status_created", table_name="wiki_proposals")
    op.drop_table("wiki_proposals")
