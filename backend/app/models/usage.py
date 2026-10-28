"""Defines the USAGE_EVENTS table: one row per metered action."""
import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class UsageEvent(Base):
    """One metered action. Table name: "usage_events"."""

    __table_args__ = (
        Index("ix_usage_events_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(14, 8, asdecimal=False), default=0, server_default="0", nullable=False
    )

    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
