from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b9c1d3e5f8"
down_revision: Union[str, None] = "f6a8b0c2d4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
