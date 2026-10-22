"""Document storage backend and ingestion durability columns

WHAT THIS MIGRATION ADDS
------------------------
Four columns and one index on `documents`, for the upload-hardening and
ingestion-durability work (services/storage.py, tasks/ingestion.py):

    storage_backend        "local" or "s3": which store holds this document's
                           file. Recorded per row so changing STORAGE_BACKEND
                           never orphans files uploaded before the change.
    attempts               how many times processing has been started, so a
                           document that keeps crashing its worker is given
                           up on instead of re-queued forever.
    processing_started_at  when the current attempt claimed the document; a
                           stale claim means the worker died and the recovery
                           routine re-queues it.
    error_message          why it failed, as a first-class column instead of
                           only meta_data["error"] (which is still written,
                           for frontends that read it).
    ix_documents_status    the recovery routine searches by status on a timer.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
Every existing document was written to local disk, so `storage_backend`
defaults to 'local' for them -- which is true. `attempts` starts at 0 and the
other two are NULL, meaning "no attempt recorded", which is also true. The
`file_path` column keeps holding absolute paths for old rows; LocalStorage
reads those as-is, and only new uploads store relative storage keys.

Revision ID: d4e6f8a0b2c5
Revises: c3d5e7f9a2b4
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c5"
down_revision: Union[str, None] = "c3d5e7f9a2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `server_default` fills every existing row as the column is added, which
    # is what lets these two be NOT NULL from the start.
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
