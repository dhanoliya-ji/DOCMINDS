"""Share links, the prompt library, and answer confidence

WHAT THIS MIGRATION ADDS
------------------------
    share_links        read-only public links to an answer, a conversation
                       or a project (models/share.py). Only an HMAC of each
                       token is stored; one nullable foreign key per kind, so
                       deleting the target deletes its links (CASCADE).
    prompt_templates   saved prompts, personal or shared with the whole
                       organization (models/prompt.py). The built-in
                       templates live in code, not here.
    chat_messages.confidence
                       JSONB, the high / medium / low verdict on an answer
                       and the reasons for it (services/confidence.py).

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
Both tables are new and start empty, and the new column is nullable: answers
saved before this migration simply show no confidence badge. No backfill --
recomputing a verdict for an old answer would need the retrieval scores of a
search that is long gone, and the citations alone would be a different
formula wearing the same badge.

Revision ID: c9e1a3b5d7f0
Revises: b8d0f2a4c6e8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e1a3b5d7f0"
down_revision: Union[str, None] = "b8d0f2a4c6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. share_links
    # ------------------------------------------------------------------
    op.create_table(
        "share_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_share_links_org_id", "share_links", ["org_id"], unique=False)

    # ------------------------------------------------------------------
    # 2. prompt_templates
    # ------------------------------------------------------------------
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_templates_org_id", "prompt_templates", ["org_id"], unique=False)
    op.create_index("ix_prompt_templates_user_id", "prompt_templates", ["user_id"], unique=False)

    # ------------------------------------------------------------------
    # 3. chat_messages.confidence
    # ------------------------------------------------------------------
    op.add_column(
        "chat_messages",
        sa.Column("confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.drop_column("chat_messages", "confidence")
    op.drop_index("ix_prompt_templates_user_id", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_org_id", table_name="prompt_templates")
    op.drop_table("prompt_templates")
    op.drop_index("ix_share_links_org_id", table_name="share_links")
    op.drop_table("share_links")
