"""Defines the API_KEYS table -- long-lived credentials for PROGRAMS rather than people."""
import datetime
import uuid
from typing import TYPE_CHECKING, Optional


from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class APIKey(Base):
    """A programmatic credential."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    hashed_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    role: Mapped[str] = mapped_column(
        String(50), default="Employee", server_default="Employee", nullable=False
    )

    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="api_keys")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="api_keys"
    )
