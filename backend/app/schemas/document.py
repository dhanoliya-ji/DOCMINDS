"""
document.py  (schemas)
======================
WHAT THIS FILE DOES
-------------------
Describes what the API returns about an uploaded document.

WHY THERE IS NO "DocumentCreate" SCHEMA
---------------------------------------
Every other resource has one, but documents are created by a FILE UPLOAD, and
a file upload is not JSON.

Browsers send files as `multipart/form-data`, so the upload endpoint declares
its inputs individually with `File(...)` and `Form(...)` instead:

    files: List[UploadFile] = File(...)
    project_id: uuid.UUID = Form(...)
    chunk_strategy: str = Form("fixed_size")

Everything else about the document -- its size, its type, its checksum -- is
derived by the server from the file itself, never accepted from the client.

WHY `status` AND `meta_data` ARE THE INTERESTING FIELDS
-------------------------------------------------------
Because processing happens in a background worker, these two fields are how the
browser learns what happened to a file after it was accepted. The frontend
polls this shape until `status` reads "completed".
"""

import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DocumentBase(BaseModel):
    """The descriptive fields shared by document responses."""

    filename: str

    # The extension without a dot: "pdf", "docx". This is what extractor.py
    # dispatched on, and what the UI shows as a type badge.
    file_type: str

    # Size in bytes. The frontend formats it as KB or MB for display.
    file_size: int

    # pending | processing | completed | failed
    # ("pending" also covers "waiting to retry after a transient error")
    #
    # The single most-read field in this schema: the document list polls until
    # it reads "completed", and only completed documents are searchable.
    status: str

    # Revision number, incremented when a file with the same name is uploaded
    # again into the same project.
    version: int

    # ---- Everything the ingestion pipeline learned ----
    # A free-form dictionary rather than fixed fields, mirroring the JSONB
    # column on the model. Typically holds:
    #
    #   language, word_count, char_count, page_count
    #   chunk_count, chunk_strategy, chunk_size, chunk_overlap
    #   embedding_model, embedding_provider, embedding_dimension
    #   needs_ocr, ocr_applied, processing_seconds
    #   error                     (only present when status == "failed")
    #
    # Keeping it open-ended means the pipeline can record a new measurement
    # without a matching change here and in the frontend types.
    meta_data: dict


class DocumentOut(DocumentBase):
    """
    A document as returned by the API.

    Note `file_path` is deliberately absent. It is stored on the model, but
    exposing a server filesystem path to a client tells an attacker about your
    directory layout for no benefit. Downloads go through
    `/documents/{id}/preview`, which checks ownership before serving the file.
    """

    # Lets Pydantic build this straight from the SQLAlchemy row object by
    # reading its attributes, rather than requiring a manual field-by-field copy.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID

    # Why processing failed, in words a user can act on ("The file does not
    # look like a PDF", "Rejected by the malware scan: Eicar-Signature").
    # None unless something went wrong. Also mirrored into meta_data["error"]
    # for frontends written before this field existed.
    error_message: Optional[str] = None

    # How many times processing has been started. Above 1 means it was retried
    # after a transient failure or recovered after a worker died.
    attempts: int = 0

    created_at: datetime.datetime
    updated_at: datetime.datetime
