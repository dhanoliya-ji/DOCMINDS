from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d0f2a4c6e8"
down_revision: Union[str, None] = "a7b9c1d3e5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("resume_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """The exact mirror of `upgrade`."""
    op.drop_column("documents", "resume_after")
