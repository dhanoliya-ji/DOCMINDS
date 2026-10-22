"""Chat history details and full-text search

WHAT THIS MIGRATION ADDS
------------------------
Three columns on chat_messages, and one index on chunks.

    chat_messages.mode    "rag" or "agent" -- which pipeline wrote this
                          answer. NULL on user messages and on answers saved
                          before this migration.
    chat_messages.model   the model that ACTUALLY answered (the fallback's
                          name if the main one failed, "mock" in demo mode).
    chat_messages.steps   the agent's tool trace, as JSON. Before this it was
                          returned once on the live response and then lost,
                          so reopening a conversation showed no research
                          steps. '[]' for everything else.

    ix_chunks_content_fts a GIN index on to_tsvector('english', content).
                          This is what makes the KEYWORD half of hybrid
                          search (services/retrieval.py) fast: without it,
                          every keyword query would re-parse the text of
                          every chunk in the organization.

WHY AN EXPRESSION INDEX AND NOT A tsvector COLUMN
-------------------------------------------------
A stored `tsvector` column would have to be filled in by the ingestion
pipeline (or a trigger) and back-filled for every existing chunk. An index
on the EXPRESSION gives the same speed with none of that: Postgres computes
the vector as it builds the index, keeps it up to date on every insert, and
uses it for any query whose WHERE clause repeats the exact same expression
-- which retrieval.py does, through one shared helper so the two can never
drift apart.

The expression index is NOT declared on the model (models/chunk.py), because
the test suite also builds the schema on SQLite, which has no to_tsvector.
It lives here only.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
mode and model are nullable and start NULL; steps defaults to an empty list.
Old messages keep exactly the meaning they had.

Revision ID: a7b9c1d3e5f8
Revises: a7b9c1d3e5f8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b9c1d3e5f8"
down_revision: Union[str, None] = "a7b9c1d3e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. What produced each answer, and how
    # ------------------------------------------------------------------
    op.add_column("chat_messages", sa.Column("mode", sa.String(length=16), nullable=True))
    op.add_column("chat_messages", sa.Column("model", sa.String(length=255), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # 2. Full-text index for hybrid search
    # ------------------------------------------------------------------
    # 'english' must match retrieval.FTS_CONFIG exactly, or the planner will
    # not use this index for the keyword query.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_content_fts "
        "ON chunks USING gin (to_tsvector('english', content))"
    )


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_fts")
    op.drop_column("chat_messages", "steps")
    op.drop_column("chat_messages", "model")
    op.drop_column("chat_messages", "mode")
