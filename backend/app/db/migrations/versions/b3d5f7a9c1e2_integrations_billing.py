"""Cloud imports, auto-sync and Stripe billing

WHAT THIS MIGRATION ADDS
------------------------
    connector_connections   an organization's OAuth connections to Google
                            Drive, OneDrive / SharePoint, Notion and
                            Confluence; tokens stored ENCRYPTED
                            (services/connectors/crypto.py).
    connector_items         each imported document's source id, revision
                            and sync status. uq_connector_items_source stops
                            one source file being imported twice into a
                            project; ix_connector_items_checked serves the
                            sync sweep.
    billing_events          the ids of Stripe webhook events already applied,
                            so a redelivered event changes nothing twice.
    organizations.plan ...  plan (free | pro | team, default free), the
                            Stripe customer and subscription ids, the
                            subscription status and the renewal date.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
The tables are new and start empty. Every organization gets plan "free",
which maps onto the QUOTA_* settings it already uses -- no limit changes.
Cloud imports and billing stay off until CONNECTORS_ENABLED and
BILLING_ENABLED are set, so this migration changes no behaviour by itself.

Revision ID: b3d5f7a9c1e2
Revises: c9e1a3b5d7f0
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d5f7a9c1e2"
down_revision: Union[str, None] = "c9e1a3b5d7f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. connector_connections
    # ------------------------------------------------------------------
    op.create_table(
        "connector_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("account_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_connections_org_id", "connector_connections", ["org_id"])

    # ------------------------------------------------------------------
    # 2. connector_items
    # ------------------------------------------------------------------
    op.create_table(
        "connector_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("external_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("revision", sa.String(length=255), nullable=True),
        sa.Column("web_url", sa.String(length=2048), nullable=True),
        sa.Column("auto_sync", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sync_status", sa.String(length=16), nullable=False, server_default="synced"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["connector_connections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
        sa.UniqueConstraint("project_id", "provider", "external_id", name="uq_connector_items_source"),
    )
    op.create_index("ix_connector_items_connection_id", "connector_items", ["connection_id"])
    op.create_index("ix_connector_items_org_id", "connector_items", ["org_id"])
    op.create_index("ix_connector_items_checked", "connector_items", ["last_checked_at"])

    # ------------------------------------------------------------------
    # 3. billing_events
    # ------------------------------------------------------------------
    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_events_org_id", "billing_events", ["org_id"])

    # ------------------------------------------------------------------
    # 4. organizations: the plan and its Stripe ids
    # ------------------------------------------------------------------
    op.add_column(
        "organizations",
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
    )
    op.add_column("organizations", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("subscription_status", sa.String(length=32), nullable=True))
    op.add_column("organizations", sa.Column("plan_renews_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_organizations_stripe_customer_id", "organizations", ["stripe_customer_id"])


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.drop_index("ix_organizations_stripe_customer_id", table_name="organizations")
    op.drop_column("organizations", "plan_renews_at")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")
    op.drop_column("organizations", "plan")
    op.drop_index("ix_billing_events_org_id", table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_index("ix_connector_items_checked", table_name="connector_items")
    op.drop_index("ix_connector_items_org_id", table_name="connector_items")
    op.drop_index("ix_connector_items_connection_id", table_name="connector_items")
    op.drop_table("connector_items")
    op.drop_index("ix_connector_connections_org_id", table_name="connector_connections")
    op.drop_table("connector_connections")
