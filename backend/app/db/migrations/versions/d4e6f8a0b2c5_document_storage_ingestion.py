from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c5"
down_revision: Union[str, None] = "c3d5e7f9a2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "storage_backend", sa.String(length=16), server_default="local", nullable=False
        ),
    )
    op.add_column(
        "documents",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))

    op.create_index("ix_documents_status", "documents", ["status"], unique=False)


def downgrade() -> None:
    """The exact mirror of `upgrade`, in reverse order."""
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_column("documents", "error_message")
    op.drop_column("documents", "processing_started_at")
    op.drop_column("documents", "attempts")
    op.drop_column("documents", "storage_backend")
