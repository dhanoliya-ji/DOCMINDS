"""Defines the PROJECT table -- a folder of related documents."""
import datetime
import uuid
from typing import List, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.document import Document
    from app.models.organization import Organization


class Project(Base):
    """A document collection and search boundary. Table name: "projects"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="projects"
    )

    documents: Mapped[List["Document"]] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )

    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        "ChatSession", back_populates="project", cascade="all, delete-orphan"
    )
