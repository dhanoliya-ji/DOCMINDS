"""Add RAG support: fixed vector dimension, HNSW index, per-project duplicates

WHAT IS A MIGRATION?
--------------------
The Python classes in app/models/ describe what the database SHOULD look like.
A migration is the set of instructions that CHANGES a real, existing database
from its current shape to that new shape -- without destroying the data already
inside it.

Alembic keeps a small table called `alembic_version` in your database recording
which migration was applied last. Running `alembic upgrade head` applies every
migration newer than that. This is how the same schema change gets applied
identically on your laptop, on a teammate's machine, and on the production
server, in the right order, exactly once each.

WHAT THIS PARTICULAR MIGRATION FIXES
------------------------------------
Three real problems that blocked semantic search from working:

  1. The `embedding` column was declared as `vector` with NO dimension.
     Postgres accepted vectors of any size, which meant a 384-number vector and
     a 1536-number vector could sit in the same column -- and comparing them is
     meaningless. Worse, pgvector REFUSES to build an index on a dimensionless
     column, so every search had to read every row.

  2. There was no vector index at all. We add an HNSW index, which turns search
     from "compare against every row" into "hop through a graph towards the
     nearest neighbours".

  3. `documents.duplicate_hash` was globally UNIQUE. That is a genuine
     multi-tenancy bug: if Company A uploaded a file, Company B uploading the
     very same file would hit a unique-constraint violation and be blocked --
     and could even infer that some other tenant had that exact file. We
     replace it with a UNIQUE constraint scoped to (project_id, duplicate_hash),
     which is what the upload code in api/v1/documents.py already assumes.

Revision ID: a1b2c3d4e5f6
Revises: 57f671738b02
Create Date: 2026-08-30
"""

from typing import Sequence, Union

from alembic import op

# ----------------------------------------------------------------------
# Alembic identifiers. `down_revision` points at the migration that must run
# BEFORE this one, which is how Alembic knows the correct order.
# ----------------------------------------------------------------------
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "57f671738b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The number of dimensions our default embedding model produces.
# all-MiniLM-L6-v2 -> 384. Hard-coded here rather than read from settings
# because a migration must describe a FIXED historical change: re-running it
# later with different settings has to produce the same schema it did the first
# time, otherwise databases drift apart.
EMBEDDING_DIM = 384


def upgrade() -> None:
    """Apply the changes. Runs on `alembic upgrade head`."""

    # ------------------------------------------------------------------
    # 1. Make sure the pgvector extension is installed.
    # ------------------------------------------------------------------
    # This teaches Postgres the `vector` data type and the distance operators.
    # `IF NOT EXISTS` makes the statement safe to run repeatedly.
    # The pgvector/pgvector Docker image ships the extension files, but each
    # individual database still has to enable it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # 2. Clear out any existing embeddings.
    # ------------------------------------------------------------------
    # We are about to fix the column at 384 dimensions. Any vector already
    # stored with a different size cannot be converted -- there is no
    # meaningful way to turn 1536 numbers into 384 -- so the rows must go.
    #
    # This is NOT data loss in any meaningful sense: embeddings are derived
    # data. The original files are still on disk and the chunk text is still in
    # the `chunks` table, so re-uploading or re-processing regenerates them.
    op.execute("DELETE FROM chunk_embeddings")

    # ------------------------------------------------------------------
    # 3. Pin the vector column to a fixed dimension.
    # ------------------------------------------------------------------
    # `USING embedding::vector(384)` tells Postgres how to convert existing
    # values. The table is empty after step 2, so nothing is actually
    # converted, but the clause is required for the ALTER to be accepted.
    op.execute(
        f"ALTER TABLE chunk_embeddings "
        f"ALTER COLUMN embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING embedding::vector({EMBEDDING_DIM})"
    )

    # ------------------------------------------------------------------
    # 4. Build the HNSW index -- this is what makes search fast.
    # ------------------------------------------------------------------
    # Without an index, finding the closest vectors means scoring every row in
    # the table on every single search. That is acceptable for a few hundred
    # chunks and completely unusable at scale.
    #
    # HNSW builds a navigable graph so a search can walk towards its nearest
    # neighbours in roughly logarithmic time.
    #
    #   vector_cosine_ops -- the index is built for COSINE distance, which must
    #                        match the `<=>` operator used in retrieval.py. An
    #                        index built for a different distance measure is
    #                        silently ignored by the query planner.
    #   m = 16             -- links kept per node. Higher is more accurate but
    #                        makes the index bigger.
    #   ef_construction=64 -- effort spent placing each node at build time.
    #                        Higher gives a better index but a slower build.
    #
    # These are pgvector's recommended defaults and are a good balance for
    # collections up to roughly a million vectors.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw "
        "ON chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # ------------------------------------------------------------------
    # 5. Index for fetching one document's chunks in order.
    # ------------------------------------------------------------------
    # Used whenever we display a document's chunks, or look up the neighbours
    # of a search hit to show surrounding context.
    op.create_index(
        "ix_chunks_document_order",
        "chunks",
        ["document_id", "chunk_index"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 6. Scope the duplicate check to a project instead of globally.
    # ------------------------------------------------------------------
    # The original schema made `duplicate_hash` unique across the whole table.
    # Two different tenants uploading an identical file -- a common PDF form,
    # say -- would collide. Deduplication should mean "this project already has
    # this exact file", so the constraint belongs on the pair of columns.

    # Drop the old single-column unique index. Postgres named it automatically
    # from the column, following its usual ix_<table>_<column> convention.
    op.drop_index("ix_documents_duplicate_hash", table_name="documents")

    # A plain (non-unique) index, so duplicate lookups by hash stay fast.
    op.create_index(
        "ix_documents_duplicate_hash", "documents", ["duplicate_hash"], unique=False
    )

    # The real rule: a given file may appear only once WITHIN a project.
    op.create_index(
        "uq_documents_project_hash",
        "documents",
        ["project_id", "duplicate_hash"],
        unique=True,
    )


def downgrade() -> None:
    """
    Undo the changes. Runs on `alembic downgrade -1`.

    Every migration should be reversible so a bad deployment can be rolled
    back. The steps are the mirror image of `upgrade`, in reverse order --
    because you cannot, for example, drop a column that another object still
    depends on.
    """
    op.drop_index("uq_documents_project_hash", table_name="documents")
    op.drop_index("ix_documents_duplicate_hash", table_name="documents")
    op.create_index(
        "ix_documents_duplicate_hash", "documents", ["duplicate_hash"], unique=True
    )

    op.drop_index("ix_chunks_document_order", table_name="chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_hnsw")

    # Return the column to a dimensionless vector.
    op.execute(
        "ALTER TABLE chunk_embeddings "
        "ALTER COLUMN embedding TYPE vector "
        "USING embedding::vector"
    )
