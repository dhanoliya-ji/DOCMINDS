"""Defines the DOCUMENT table -- the record of one uploaded file."""
import datetime
import uuid
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.chunk import Chunk
    from app.models.project import Project


class Document(Base):
    """One uploaded file. Table name: "documents"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    file_type: Mapped[str] = mapped_column(String(50), nullable=False)

    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    storage_backend: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local", nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, index=True
    )

    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    processing_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    resume_after: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    duplicate_hash: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # JSONB is Postgres's binary JSON type: queryable and indexable, unlike a
    meta_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship("Project", back_populates="documents")

    chunks: Mapped[List["Chunk"]] = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )
