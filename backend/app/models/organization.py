"""Defines the ORGANIZATION table -- the root of the whole multi-tenant design."""
import datetime
import uuid
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.api_key import APIKey
    from app.models.audit import AuditLog
    from app.models.project import Project
    from app.models.user import User


class Organization(Base):
    """A tenant. Table name is generated automatically as "organizations"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    monthly_token_quota: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_documents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free", server_default="free")
    billing_provider: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    plan_renews_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    users: Mapped[List["User"]] = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="organization", cascade="all, delete-orphan"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="organization", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="organization", cascade="all, delete-orphan"
    )
