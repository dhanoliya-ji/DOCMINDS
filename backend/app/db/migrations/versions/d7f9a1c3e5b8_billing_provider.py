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
