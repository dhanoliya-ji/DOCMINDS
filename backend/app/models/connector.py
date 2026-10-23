"""
connector.py  (database models)
===============================
WHAT THIS FILE DOES
-------------------
Defines the three tables behind cloud imports (services/connectors/):

    connector_connections   one OAuth connection of an organization to a
                            provider -- Google Drive, OneDrive / SharePoint,
                            Notion or Confluence -- with its tokens,
                            ENCRYPTED.
    connector_items         one imported document's link back to its source:
                            the provider's id for the file, the revision we
                            imported, and how the last sync went.
    connector_imports       one "Import" press: the picked items and how far
                            the BACKGROUND import of each has got
                            (pending -> downloading -> imported | failed ...),
                            so the page can poll it.

WHY THE TOKENS ARE ENCRYPTED, NOT HASHED
----------------------------------------
Passwords and API keys are hashed because we only ever need to CHECK them.
An OAuth access token has to be SENT to the provider, so we need it back in
full -- hashing would lose it. Encryption (services/connectors/crypto.py) is
the next best thing: a stolen database backup alone reveals nothing, because
the key lives in the environment (CONNECTOR_ENCRYPTION_KEY, or one derived
from SECRET_KEY), never in the database.

THE DELETE RULES
----------------
    organization deleted -> its connections and links go too (CASCADE).
    document deleted     -> its link goes too (CASCADE): nothing to sync.
    connection deleted   -> the documents it imported STAY, searchable as
                            before; their link's connection_id becomes NULL
                            and its sync_status "disconnected", so the page
                            can say why they no longer update.

IMPORT JOBS
-----------
POST /connections/{id}/import only validates and records a job (status
"queued"), then returns 202; downloading, storing and queueing for ingestion
happen in the background (service.run_import). The job's `items` is a JSON
list, one entry per picked id, written by the ONE worker that holds the job:

    {"item_id", "name", "status", "document_id", "detail"}
    status: pending | downloading | imported | duplicate | already_imported
            | skipped | failed

`heartbeat_at` is renewed after every item; a job whose heartbeat is older
than CONNECTOR_IMPORT_STALE_MINUTES was orphaned (the process died) and is
resumed by the startup recovery, skipping the items already finished.

SYNC STATUS
-----------
    synced         the copy we hold is the source's current revision
    syncing        a newer revision was found and is being re-indexed
    error          the last check failed; `last_error` says why
    source_deleted the provider says the file no longer exists (or we lost
                   access to it); the document is kept as it was
    disconnected   the connection was removed; nothing can be checked
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

# Connection states.
ACTIVE = "active"
ERROR = "error"   # a refresh failed; reconnecting fixes it

# Import job states.
IMPORT_QUEUED = "queued"
IMPORT_RUNNING = "running"
IMPORT_COMPLETED = "completed"

# Import item states.
ITEM_PENDING = "pending"
ITEM_DOWNLOADING = "downloading"
ITEM_IMPORTED = "imported"
ITEM_FAILED = "failed"
# Finished without a new document, and not an error.
ITEM_DUPLICATE = "duplicate"
ITEM_ALREADY_IMPORTED = "already_imported"
ITEM_SKIPPED = "skipped"
ITEM_UNFINISHED = (ITEM_PENDING, ITEM_DOWNLOADING)

# Link (document) sync states.
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
    # google_drive | onedrive | notion | confluence
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Who or what is connected, for people to recognise: an email address,
    # a Notion workspace name, a Confluence site.
    account_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Fernet ciphertext of a JSON object: access_token, refresh_token,
    # expires_at and provider extras (Confluence's cloud id). Never returned
    # by any endpoint.
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
        # The same source file imported twice into one project would be two
        # documents that both sync -- refused instead.
        UniqueConstraint("project_id", "provider", "external_id", name="uq_connector_items_source"),
        # The sync sweep: "links not checked since <cutoff>", oldest first.
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
    # The provider's id for the file or page (opaque; never part of a host).
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # The provider's own change marker: Drive's version, Graph's eTag,
    # Notion's last_edited_time, Confluence's version number.
    revision: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # The web address people open the original at, when the provider gives one.
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
        # The recovery sweep: unfinished jobs with an old heartbeat.
        Index("ix_connector_imports_status_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL: disconnecting mid-import fails the remaining items, it does
    # not delete the record of what was imported.
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
