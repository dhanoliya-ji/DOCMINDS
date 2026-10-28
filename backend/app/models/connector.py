"""Defines the three tables behind cloud imports (services/connectors/)."""
import datetime
import uuid
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

ACTIVE = "active"
ERROR = "error"   # a refresh failed; reconnecting fixes it

IMPORT_QUEUED = "queued"
IMPORT_RUNNING = "running"
IMPORT_COMPLETED = "completed"

ITEM_PENDING = "pending"
ITEM_DOWNLOADING = "downloading"
ITEM_IMPORTED = "imported"
ITEM_FAILED = "failed"
ITEM_DUPLICATE = "duplicate"
ITEM_ALREADY_IMPORTED = "already_imported"
ITEM_SKIPPED = "skipped"
ITEM_UNFINISHED = (ITEM_PENDING, ITEM_DOWNLOADING)

SYNCED = "synced"
SYNCING = "syncing"
SYNC_ERROR = "error"
SOURCE_DELETED = "source_deleted"
DISCONNECTED = "disconnected"


class ConnectorConnection(Base):
    """An organization's OAuth connection to one cloud provider account."""

    __tablename__ = "connector_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    account_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    encrypted_tokens: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ACTIVE, server_default=ACTIVE)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorItem(Base):
    """One imported document's source: where it came from, and which revision."""

    __tablename__ = "connector_items"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_connector_items_source"),
        Index("ix_connector_items_checked", "last_checked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    revision: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    web_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    auto_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    sync_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SYNCED, server_default=SYNCED
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectorImport(Base):
    """One background import: the items picked in one "Import" press."""

    __tablename__ = "connector_imports"
    __table_args__ = (
        Index("ix_connector_imports_status_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    auto_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IMPORT_QUEUED, server_default=IMPORT_QUEUED
    )
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
