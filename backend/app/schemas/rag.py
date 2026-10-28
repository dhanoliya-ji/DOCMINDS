"""WHAT A "SCHEMA" IS AND WHY THIS FILE EXISTS A schema is a description of the SHAPE of data going into or..."""
import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    """What the client sends to POST /api/v1/search."""

    query: str = Field(..., min_length=1, max_length=2000,
                       description="The question or phrase to search for.")

    project_id: Optional[uuid.UUID] = Field(
        None, description="Restrict the search to a single project."
    )
    document_id: Optional[uuid.UUID] = Field(
        None, description="Restrict the search to a single document."
    )

    document_ids: Optional[List[uuid.UUID]] = Field(
        None, max_length=50,
        description="Restrict the search to these documents.",
    )

    top_k: Optional[int] = Field(
        None, ge=1, le=50, description="How many results to return (1-50)."
    )
    min_score: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Discard results less similar than this (0-1)."
    )


class SearchResult(BaseModel):
    """One matching chunk, as returned to the client."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID

    filename: str
    page_number: Optional[int] = None
    chunk_index: int

    content: str

    score: float

    meta_data: Dict[str, Any] = {}

    match_type: Optional[str] = None

    rerank_score: Optional[float] = None


class SearchResponse(BaseModel):
    """The full reply from the search endpoint."""

    query: str
    results: List[SearchResult]

    count: int

    took_seconds: float


class ChatSessionCreate(BaseModel):
    """Body for POST /api/v1/chat/sessions -- start a new conversation."""

    project_id: uuid.UUID = Field(
        ..., description="Which project's documents this conversation covers."
    )
    title: Optional[str] = Field(
        None, max_length=255,
        description="Optional name. Defaults to the first question asked."
    )


class ChatSessionOut(BaseModel):
    """A conversation, as shown in the sidebar list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    project_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ChatSessionUpdate(BaseModel):
    """Body for PATCH /api/v1/chat/sessions/{id} -- rename a conversation."""

    title: str = Field(..., min_length=1, max_length=255,
                       description="The new name shown in the history sidebar.")


class Citation(BaseModel):
    """A pointer back to the exact passage that supports one part of the answer."""

    number: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: Optional[int] = None
    score: float

    snippet: str


class ChatRequest(BaseModel):
    """Body for POST /api/v1/chat/sessions/{id}/messages -- ask a question."""

    message: str = Field(..., min_length=1, max_length=4000,
                         description="The user's question.")
    top_k: Optional[int] = Field(
        None, ge=1, le=20,
        description="How many document excerpts to give the AI as context."
    )

    mode: Literal["rag", "agent"] = Field(
        "rag", description='"rag" (fixed pipeline) or "agent" (tool-using agent).'
    )

    document_ids: Optional[List[uuid.UUID]] = Field(
        None, max_length=50,
        description="Answer only from these documents.",
    )


class AgentStep(BaseModel):
    """One tool call the agent made while answering, shown in the UI as a trace."""

    tool: str
    args: Dict[str, Any] = {}
    summary: str


class ChatMessageOut(BaseModel):
    """One message in a conversation, from either the user or the assistant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID

    role: str
    content: str

    tokens_used: Optional[int] = None
    latency: Optional[float] = None
    citations: List[Dict[str, Any]] = []

    mode: Optional[str] = None
    model: Optional[str] = None
    steps: List[Dict[str, Any]] = []

    confidence: Optional[Dict[str, Any]] = None

    created_at: datetime.datetime


class ChatResponse(BaseModel):
    """The complete result of asking a question."""

    session_id: uuid.UUID
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    citations: List[Citation]

    model: str

    mode: str = "rag"

    steps: List[AgentStep] = []

    search_query: Optional[str] = None

    confidence: Optional[Dict[str, Any]] = None


class FeedbackCreate(BaseModel):
    """Body for the thumbs up / thumbs down button on an answer."""

    rating: int = Field(..., ge=-1, le=1,
                        description="1 for thumbs up, -1 for thumbs down.")
    comment: Optional[str] = Field(
        None, max_length=1000, description="Optional written feedback."
    )


class FeedbackBreakdown(BaseModel):
    """Up and down votes for one slice: a mode, or a model."""

    key: str
    up: int
    down: int


class FeedbackItem(BaseModel):
    """One rated answer, with the question that led to it."""

    message_id: uuid.UUID
    session_id: uuid.UUID
    rating: int
    comment: Optional[str] = None
    question: Optional[str] = None
    answer: str
    mode: Optional[str] = None
    model: Optional[str] = None
    created_at: datetime.datetime


class FeedbackSummary(BaseModel):
    """GET /api/v1/org/feedback -- how the organization rates its answers."""

    total: int
    up: int
    down: int
    satisfaction: Optional[float] = None
    by_mode: List[FeedbackBreakdown] = []
    by_model: List[FeedbackBreakdown] = []
    recent_negative: List[FeedbackItem] = []
