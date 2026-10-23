"""
document.py
===========
WHAT THIS FILE DOES
-------------------
Defines the DOCUMENT table -- the record of one uploaded file.

Note the distinction: this row is the RECORD, not the file. The bytes live on
disk at `file_path`; this table stores everything we know ABOUT them.

THE STATUS FIELD IS THE HEART OF THIS TABLE
-------------------------------------------
Because processing happens in a background worker, the browser has no way of
being told when it finishes. This column is how progress is communicated:

    pending     uploaded, sitting in the Celery queue (or waiting to retry)
    processing  a worker has claimed it -- `processing_started_at` says when
    completed   chunked, embedded and SEARCHABLE
    failed      something went wrong; the reason is in `error_message` (and,
                for older frontends, meta_data["error"]). A file rejected by
                the malware scan is failed with meta_data["quarantined"].

The frontend polls until it reads "completed", and services/retrieval.py
searches ONLY completed documents -- a half-processed file would otherwise
return partial answers that look complete.

TWO FEATURES WORTH UNDERSTANDING
--------------------------------
DEDUPLICATION. Every upload is SHA-256 hashed. Uploading the same file twice
into one project is detected and skipped, saving the expensive OCR and
embedding work. A SHA-256 of the CONTENT means a renamed file is still caught.

VERSIONING. Uploading a file with a name already present in the project stores
it as version 2, 3 and so on, rather than overwriting. Document history is
preserved.
"""

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

    # The name the user uploaded, shown in the UI and in citations.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # The extension without the dot: "pdf", "docx", "xlsx". This is what
    # extractor.py dispatches on to choose a parser.
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Size in bytes. Displayed as KB/MB by the frontend.
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # Where the bytes actually live: a STORAGE KEY such as
    # "<project_id>/<uuid>.pdf", read through services/storage.py. The
    # client's filename is never part of it. (Rows from before storage keys
    # existed hold an absolute disk path here; LocalStorage still reads them.)
    # The column keeps its old name so no data has to be rewritten.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    # WHICH storage backend holds that key: "local" or "s3". Recorded per
    # document so switching STORAGE_BACKEND does not orphan older uploads --
    # they are still read from wherever they were written.
    storage_backend: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local", nullable=False
    )

    # pending | processing | completed | failed  -- see the docstring above.
    # Indexed because the recovery routine searches by it on a timer.
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, index=True
    )

    # ---- Ingestion bookkeeping (tasks/ingestion.py) ---------------------
    # How many times processing has been STARTED. Retries and recoveries
    # both count, so a document that keeps killing its worker is eventually
    # given up on instead of being re-queued forever.
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # When the current (or last) attempt claimed the document -- and, while
    # it runs, its HEARTBEAT: the pipeline moves it forward after every batch
    # of pages or chunks, so a large document that legitimately takes an hour
    # is never mistaken for a dead one. A "processing" row whose heartbeat is
    # older than INGESTION_STALE_AFTER_MINUTES is assumed to be orphaned by a
    # dead worker and is re-queued.
    processing_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set only while the document is PAUSED: the embedding provider's rate
    # limit outlasted the retry budget, so the document waits ("pending",
    # every chunk embedded so far kept) and the recovery sweep resumes it
    # once this time has passed. NULL otherwise. meta_data["progress"] says
    # the same thing in words for the UI.
    resume_after: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Why the document failed, in words a user can act on. NULL unless the
    # status is "failed" (or a retry is pending after a transient error).
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # SHA-256 of the file's CONTENTS, used for deduplication.
    #
    # `index=True` but NOT unique here. Uniqueness is enforced on the PAIR
    # (project_id, duplicate_hash) by a migration, because the same file may
    # legitimately exist in two different projects -- and a globally unique
    # constraint would let one tenant's upload block another's.
    duplicate_hash: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )

    # Revision number for repeated uploads of the same filename.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ---- The flexible metadata column ----
    # JSONB is Postgres's binary JSON type: queryable and indexable, unlike a
    # plain text column holding JSON.
    #
    # This is where the ingestion pipeline records everything it learned:
    #   language, word_count, char_count, page_count
    #   chunk_count, chunk_strategy, chunk_size, chunk_overlap
    #   embedding_model, embedding_provider, embedding_dimension
    #   needs_ocr, ocr_applied, processing_seconds
    #   error            (only when status == "failed")
    #
    # Using JSONB means adding a new metadata field later needs NO migration --
    # which matters because the pipeline gains new measurements over time.
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

    # ---- Relationships -------------------------------------------------
    project: Mapped["Project"] = relationship("Project", back_populates="documents")

    # Deleting a document deletes its chunks, and their cascade in turn removes
    # the embeddings. That chain is what keeps the vector index free of rows
    # pointing at documents that no longer exist.
    chunks: Mapped[List["Chunk"]] = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )
