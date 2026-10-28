"""The request and response shapes for the collaboration features."""
import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ShareKind = Literal["message", "session", "project"]


class ShareLinkCreate(BaseModel):
    """Make a public read-only link to an answer, a conversation or a project."""

    kind: ShareKind = Field(..., description="What to share: an answer, a conversation or a project.")
    target_id: uuid.UUID = Field(
        ..., description="The assistant message, chat session or project id."
    )
    expires_in_days: Optional[int] = Field(
        None, ge=1, le=365, description="Days until the link stops working; omit for never."
    )


class ShareLinkOut(BaseModel):
    """A link in the management list. The token itself is never shown again."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    target_id: Optional[uuid.UUID] = None
    token_prefix: str
    created_by: Optional[uuid.UUID] = None
    created_at: datetime.datetime
    expires_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    view_count: int = 0
    last_viewed_at: Optional[datetime.datetime] = None
    status: str = "active"


class ShareLinkCreated(ShareLinkOut):
    """The create response: the only time the token is ever returned."""

    token: str
    path: str


class PublicCitation(BaseModel):
    number: Optional[int] = None
    filename: Optional[str] = None
    page_number: Optional[int] = None
    score: Optional[float] = None
    snippet: Optional[str] = None


class PublicMessage(BaseModel):
    role: str
    content: str
    citations: List[PublicCitation] = []
    mode: Optional[str] = None
    confidence: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime.datetime] = None


class PublicDocument(BaseModel):
    filename: str
    file_type: str
    page_count: Optional[int] = None
    word_count: Optional[int] = None
    created_at: Optional[datetime.datetime] = None


class PublicShare(BaseModel):
    """What GET /public/shares/{token} returns -- an allow-list of fields, built"""

    kind: str
    title: str
    created_at: Optional[datetime.datetime] = None
    expires_at: Optional[datetime.datetime] = None
    messages: List[PublicMessage] = []
    documents: List[PublicDocument] = []


PromptScope = Literal["personal", "org"]


def _tidy_title(value: str) -> str:
    title = " ".join((value or "").split())
    if not title:
        raise ValueError("The title cannot be blank.")
    return title


def _tidy_body(value: str) -> str:
    body = (value or "").strip()
    if not body:
        raise ValueError("The prompt cannot be blank.")
    return body


class PromptTemplateCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=4000)
    scope: PromptScope = Field(
        "personal", description='"personal" (only you) or "org" (everyone; editor role).'
    )

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _tidy_title(value)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return _tidy_body(value)


class PromptTemplateUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    body: Optional[str] = Field(None, min_length=1, max_length=4000)
    scope: Optional[PromptScope] = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _tidy_title(value)

    @field_validator("body")
    @classmethod
    def _body(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _tidy_body(value)


class PromptTemplateOut(BaseModel):
    id: str
    title: str
    body: str
    scope: str
    key: Optional[str] = None
    editable: bool = False
    owner_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None


class ComparisonRequest(BaseModel):
    document_a: uuid.UUID = Field(..., description="The first (usually older) document.")
    document_b: uuid.UUID = Field(..., description="The second (usually newer) document.")
    focus: Optional[str] = Field(
        None, max_length=300,
        description='Optional: compare only what concerns this, e.g. "termination".',
    )


class ComparedDocument(BaseModel):
    id: uuid.UUID
    filename: str


class ComparisonCitation(BaseModel):
    side: Literal["a", "b"]
    number: int
    label: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: Optional[int] = None
    snippet: str


class Difference(BaseModel):
    kind: Literal["added", "removed", "changed"]
    topic: str
    detail: str
    a: List[int] = []
    b: List[int] = []


class ComparisonOut(BaseModel):
    document_a: ComparedDocument
    document_b: ComparedDocument
    summary: str
    differences: List[Difference]
    citations: List[ComparisonCitation]
    alignment: str
    pairs: int = 0
    identical: int = 0
    model: str
    tokens_used: int = 0
    latency: float = 0.0
