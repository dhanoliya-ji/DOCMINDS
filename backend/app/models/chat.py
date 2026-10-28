"""Defines the three tables that store conversations."""
import datetime
import uuid
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.user import User


class ChatSession(Base):
    """One conversation. Table name: "chat_sessions"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(
        String(255), default="New Chat", nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")
    project: Mapped["Project"] = relationship(
        "Project", back_populates="chat_sessions"
    )
    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """One message in a conversation. Table name: "chat_messages"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[str] = mapped_column(String(50), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)


    tokens_used: Mapped[int] = mapped_column(Integer, nullable=True)

    latency: Mapped[float] = mapped_column(Float, nullable=True)

    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    steps: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )

    confidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="messages"
    )
    feedbacks: Mapped[List["Feedback"]] = relationship(
        "Feedback", back_populates="message", cascade="all, delete-orphan"
    )


class Feedback(Base):
    """A rating on one assistant answer. Table name: "feedbacks"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    comment: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    message: Mapped["ChatMessage"] = relationship(
        "ChatMessage", back_populates="feedbacks"
    )
