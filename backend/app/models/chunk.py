"""Defines the two database tables at the heart of semantic search."""
import datetime
import uuid
from typing import List, TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.config import settings
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.document import Document


class Chunk(Base):
    """One piece of a document, stored as readable text."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    page_number: Mapped[int] = mapped_column(Integer, nullable=True)

    meta_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    embeddings: Mapped[List["ChunkEmbedding"]] = relationship(
        "ChunkEmbedding", back_populates="chunk", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_chunks_document_order", "document_id", "chunk_index"),
    )


class ChunkEmbedding(Base):
    """The vector representation of one chunk -- its meaning, expressed as numbers."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )

    embedding: Mapped[List[float]] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION), nullable=False
    )

    model_name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunk: Mapped["Chunk"] = relationship("Chunk", back_populates="embeddings")

    __table_args__ = (
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
