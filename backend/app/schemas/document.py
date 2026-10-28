"""Describes what the API returns about an uploaded document."""
import datetime
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentBase(BaseModel):
    """The descriptive fields shared by document responses."""

    filename: str

    file_type: str

    file_size: int

    status: str

    version: int

    meta_data: dict


class DocumentOut(DocumentBase):
    """A document as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID

    error_message: Optional[str] = None

    attempts: int = 0

    created_at: datetime.datetime
    updated_at: datetime.datetime


class DocumentUpdate(BaseModel):
    """Body for PATCH /documents/{id} -- rename a document."""

    filename: str = Field(..., min_length=1, max_length=255)


class ChunkContextOut(BaseModel):
    """One chunk of a document's text, for the citation viewer."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    page_number: Optional[int] = None
    content: str


class DocumentReindex(BaseModel):
    """Optional body for POST /documents/{id}/reindex -- new chunking options."""

    chunk_strategy: Optional[Literal["fixed_size", "sentence", "markdown"]] = None
    chunk_size: Optional[int] = Field(None, ge=50, le=4000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=1000)
