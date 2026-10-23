"""Resumable ingestion: documents.resume_after

WHAT THIS MIGRATION ADDS
------------------------
One nullable column on `documents`, for large-document ingestion
(tasks/ingestion.py):

    resume_after   set while a document is PAUSED -- its embedding provider's
                   rate limit outlasted the retry budget. The document stays
                   "pending", keeps every chunk embedded so far, and the
                   recovery sweep resumes it once this time has passed.
                   NULL for every other document.

A real column rather than a key inside meta_data because the sweep has to ask
the database "which paused documents are due?" on a timer, the same way on
Postgres and on SQLite, and a JSON lookup is spelled differently on each.

WHY NOTHING EXISTING IS REWRITTEN
---------------------------------
No document was paused before this column existed, so NULL is true for all of
them.

Revision ID: b8d0f2a4c6e8
Revises: a7b9c1d3e5f8
Create Date: 2026-10-23
"""

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
