"""Defines `ShareLink` -- a read-only PUBLIC link to one piece of content."""
import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

SHARE_KINDS = ("message", "session", "project")


class ShareLink(Base):
    """A read-only public link. Table name: "share_links"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    view_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_viewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def target_id(self) -> Optional[uuid.UUID]:
        """Whichever of the three foreign keys `kind` says is set."""
        return {
            "message": self.message_id,
            "session": self.session_id,
            "project": self.project_id,
        }.get(self.kind)
