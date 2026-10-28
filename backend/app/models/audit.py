"""Defines the AUDIT_LOGS table -- a permanent record of who did what, and when."""
import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class AuditLog(Base):
    """One recorded action. Table name: "audit_logs"."""

    __table_args__ = (
        Index("ix_audit_logs_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    action: Mapped[str] = mapped_column(String(255), nullable=False)

    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="audit_logs"
    )
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")
