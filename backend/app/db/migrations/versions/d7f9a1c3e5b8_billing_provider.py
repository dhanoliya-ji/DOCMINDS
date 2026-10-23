"""Billing provider per organization (Stripe or Razorpay)

WHAT THIS MIGRATION ADDS
------------------------
    organizations.billing_provider   "stripe", "razorpay" or NULL: which
                                     provider holds the organization's
                                     subscription (services/billing.py).

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
The Stripe columns keep their names and their data. stripe_subscription_id
now holds whichever provider's subscription id billing_provider names.
Organizations that already have a Stripe customer or subscription are
backfilled to "stripe", so they keep being managed through Stripe; every
other row stays NULL until it subscribes.

Revision ID: d7f9a1c3e5b8
Revises: b3d5f7a9c1e2
Create Date: 2026-10-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f9a1c3e5b8"
down_revision: Union[str, None] = "b3d5f7a9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("billing_provider", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE organizations SET billing_provider = 'stripe' "
        "WHERE stripe_customer_id IS NOT NULL OR stripe_subscription_id IS NOT NULL"
    )


def downgrade() -> None:
    """The exact mirror of `upgrade`."""
    op.drop_column("organizations", "billing_provider")
