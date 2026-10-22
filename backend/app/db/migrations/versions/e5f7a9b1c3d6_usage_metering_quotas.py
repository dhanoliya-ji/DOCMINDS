"""Usage metering and per-organization quotas

WHAT THIS MIGRATION ADDS
------------------------
One table and three columns, for metering and quotas (services/usage.py):

    usage_events   one row per chat answer: organization, user, mode, the
                   model that answered, tokens in and out, estimated cost,
                   and the id of the request that caused it.
                   ix_usage_events_org_created serves "this organization,
                   this month" -- run by the quota check on every chat
                   request and by GET /org/usage.
                   ix_usage_events_user_id lets deleting a user (which sets
                   user_id to NULL here) find its rows without a full scan.

    organizations.monthly_token_quota   per-organization overrides of the
    organizations.max_documents         QUOTA_* settings. NULL = use the
    organizations.max_storage_bytes     setting; 0 = unlimited.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
The three columns are nullable and start NULL, so every existing
organization simply follows the QUOTA_* settings -- no row's meaning changes.
`usage_events` starts empty: usage is counted from the deploy onwards, so the
first month's report covers only the part of the month after it.

Revision ID: e5f7a9b1c3d6
Revises: d4e6f8a0b2c5
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f7a9b1c3d6"
down_revision: Union[str, None] = "d4e6f8a0b2c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Quota overrides on organizations
    # ------------------------------------------------------------------
    op.add_column(
        "organizations", sa.Column("monthly_token_quota", sa.BigInteger(), nullable=True)
    )
    op.add_column("organizations", sa.Column("max_documents", sa.Integer(), nullable=True))
    op.add_column(
        "organizations", sa.Column("max_storage_bytes", sa.BigInteger(), nullable=True)
    )

    # ------------------------------------------------------------------
    # 2. usage_events
    # ------------------------------------------------------------------
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(precision=14, scale=8),
            server_default="0",
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_events_org_created", "usage_events", ["org_id", "created_at"], unique=False
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"], unique=False)


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_index("ix_usage_events_org_created", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_column("organizations", "max_storage_bytes")
    op.drop_column("organizations", "max_documents")
    op.drop_column("organizations", "monthly_token_quota")
