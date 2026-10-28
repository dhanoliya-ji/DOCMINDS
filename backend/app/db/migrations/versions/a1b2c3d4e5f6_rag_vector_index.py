from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "57f671738b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    """Apply the changes. Runs on `alembic upgrade head`."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("DELETE FROM chunk_embeddings")

    op.execute(
        f"ALTER TABLE chunk_embeddings "
        f"ALTER COLUMN embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING embedding::vector({EMBEDDING_DIM})"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw "
        "ON chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.create_index(
        "ix_chunks_document_order",
        "chunks",
        ["document_id", "chunk_index"],
        unique=False,
    )


    op.drop_index("ix_documents_duplicate_hash", table_name="documents")

    op.create_index(
        "ix_documents_duplicate_hash", "documents", ["duplicate_hash"], unique=False
    )

    op.create_index(
        "uq_documents_project_hash",
        "documents",
        ["project_id", "duplicate_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Undo the changes. Runs on `alembic downgrade -1`."""
    op.drop_index("uq_documents_project_hash", table_name="documents")
    op.drop_index("ix_documents_duplicate_hash", table_name="documents")
    op.create_index(
        "ix_documents_duplicate_hash", "documents", ["duplicate_hash"], unique=True
    )

    op.drop_index("ix_chunks_document_order", table_name="chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_hnsw")

    op.execute(
        "ALTER TABLE chunk_embeddings "
        "ALTER COLUMN embedding TYPE vector "
        "USING embedding::vector"
    )
