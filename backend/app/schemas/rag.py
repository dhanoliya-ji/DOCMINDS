"""
rag.py  (schemas)
=================
WHAT A "SCHEMA" IS AND WHY THIS FILE EXISTS
-------------------------------------------
A schema is a description of the SHAPE of data going into or coming out of the
API. We write them with Pydantic, and FastAPI uses them to do four jobs for
free -- jobs you would otherwise have to hand-write in every endpoint:

  1. VALIDATE incoming JSON. If the browser sends `top_k: "banana"` where a
     number is required, FastAPI rejects it with a clear 422 error before a
     single line of our code runs.
  2. CONVERT types. A UUID arrives as a string in JSON; Pydantic turns it into
     a real `uuid.UUID` object automatically.
  3. FILTER outgoing data. Only fields declared here are sent to the client.
     This is a genuine security feature -- it is why a User response can never
     accidentally leak `hashed_password`, even if the object holds it.
  4. DOCUMENT the API. The interactive docs at /docs are generated from these
     classes, so the documentation can never drift out of date with the code.

NAMING CONVENTION USED THROUGHOUT THIS PROJECT
----------------------------------------------
    ...Request / ...Create  -> data coming IN from the client
    ...Out / ...Response    -> data going OUT to the client

Keeping those separate matters: the fields a client is allowed to SEND are
rarely the same as the fields we are willing to RETURN.
"""

import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ======================================================================
# SEARCH -- pure semantic search, no AI-written answer
# ======================================================================


class SearchRequest(BaseModel):
    """
    What the client sends to POST /api/v1/search.

    This endpoint performs retrieval ONLY. It returns the matching passages
    without asking an LLM to summarise them, which makes it useful both as a
    fast "find the passage" feature and as a debugging tool: if chat answers
    look wrong, call this first to see whether retrieval or generation is at
    fault.
    """

    # `Field(...)` with three dots means REQUIRED -- there is no default.
    # min_length=1 rejects an empty search box; max_length caps abuse.
    query: str = Field(..., min_length=1, max_length=2000,
                       description="The question or phrase to search for.")

    # `Optional[X] = None` means the field may be omitted entirely.
    # When omitted, the search covers every project in the organization.
    project_id: Optional[uuid.UUID] = Field(
        None, description="Restrict the search to a single project."
    )
    document_id: Optional[uuid.UUID] = Field(
        None, description="Restrict the search to a single document."
    )

    # The "only these files" filter. Up to 50 ids; omitted or empty means the
    # whole project (or organization). Ids from another organization simply
    # match nothing -- the tenant filter in retrieval.py still applies.
    document_ids: Optional[List[uuid.UUID]] = Field(
        None, max_length=50,
        description="Restrict the search to these documents.",
    )

    # ge/le = "greater than or equal" / "less than or equal". These bounds stop
    # a client requesting 10,000 results and exhausting the server's memory.
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

    # Included so the UI can show "handbook.pdf, page 12" without needing a
    # second API call to look the document up by id.
    filename: str
    page_number: Optional[int] = None
    chunk_index: int

    # The actual text. This is what the user reads, and what was fed to the LLM.
    content: str

    # Similarity, 0..1. Displayed as a confidence badge in the UI so the user
    # can judge for themselves how strong the match was.
    score: float

    # Extra facts recorded at chunk time: token count, markdown header, etc.
    meta_data: Dict[str, Any] = {}

    # Which half of hybrid search found it: "vector" (meaning), "keyword"
    # (exact words) or "both". None when hybrid search is switched off.
    match_type: Optional[str] = None

    # The cross-encoder's relevance score, when RERANKER is on.
    rerank_score: Optional[float] = None


class SearchResponse(BaseModel):
    """The full reply from the search endpoint."""

    query: str
    results: List[SearchResult]

    # `count` saves the client doing len(results), and makes the response
    # self-describing when read in logs or the docs page.
    count: int

    # Round-trip time in seconds, so the UI can display "found in 0.08s".
    took_seconds: float


# ======================================================================
# CHAT -- retrieval plus an AI-written answer, with citations
# ======================================================================


class ChatSessionCreate(BaseModel):
    """
    Body for POST /api/v1/chat/sessions -- start a new conversation.

    A session groups related messages so follow-up questions have context, in
    the same way a thread works in any messaging app.
    """

    project_id: uuid.UUID = Field(
        ..., description="Which project's documents this conversation covers."
    )
    title: Optional[str] = Field(
        None, max_length=255,
        description="Optional name. Defaults to the first question asked."
    )


class ChatSessionOut(BaseModel):
    """A conversation, as shown in the sidebar list."""

    # `from_attributes=True` lets Pydantic build this straight from a
    # SQLAlchemy row object by reading its attributes. Without it we would have
    # to copy every field across by hand in every endpoint.
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
    """
    A pointer back to the exact passage that supports one part of the answer.

    Citations are the reason this system can be trusted with real company
    documents. The AI's answer is only as good as its sources, so we return
    those sources and let the user verify every claim themselves.

    The `number` matches the bracketed marker the model wrote in its answer:
    text reading "...30 days of leave [2]" refers to the citation numbered 2.
    """

    number: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: Optional[int] = None
    score: float

    # A short excerpt for the collapsed citation card in the UI. The full text
    # is available through the search endpoint if the user wants more.
    snippet: str


class ChatRequest(BaseModel):
    """Body for POST /api/v1/chat/sessions/{id}/messages -- ask a question."""

    message: str = Field(..., min_length=1, max_length=4000,
                         description="The user's question.")
    top_k: Optional[int] = Field(
        None, ge=1, le=20,
        description="How many document excerpts to give the AI as context."
    )

    # Which of the two answering strategies to use:
    #
    #   "rag"   -- the fixed pipeline: embed the question, fetch the top_k
    #              closest chunks, make ONE model call. Fast, cheap, and the
    #              same steps every time. This is the default, so every client
    #              written before agent mode existed keeps working unchanged.
    #   "agent" -- the Pydantic AI agent in services/agent.py. The model is
    #              given tools and decides which to call, so it can search
    #              several times, pick a specific file, or read a whole section.
    #              Better for multi-part and comparison questions; slower and
    #              uses several model calls.
    #
    # `Literal[...]` means only those exact strings are accepted. Anything else
    # ("Agent", "deep") is rejected with a 422 before our code runs.
    mode: Literal["rag", "agent"] = Field(
        "rag", description='"rag" (fixed pipeline) or "agent" (tool-using agent).'
    )

    # The "only these files" filter. Up to 50 ids; omitted or empty means the
    # whole project (or organization). Ids from another organization simply
    # match nothing -- the tenant filter in retrieval.py still applies.
    document_ids: Optional[List[uuid.UUID]] = Field(
        None, max_length=50,
        description="Answer only from these documents.",
    )


class AgentStep(BaseModel):
    """
    One tool call the agent made while answering, shown in the UI as a trace.

    Agent mode is less predictable than the fixed pipeline -- the model decides
    what to do -- so we show the user exactly what it did: "searched for X,
    found 5 excerpts", "read handbook.pdf chunks 0-4". That trace is what makes
    an agent's answer reviewable instead of a black box.

    Steps are returned in the response AND saved on the assistant message
    (ChatMessage.steps), so reopening a conversation from the history sidebar
    shows the trace too.
    """

    # The tool's name, e.g. "search_documents".
    tool: str
    # The arguments the model chose, e.g. {"query": "annual leave"}.
    args: Dict[str, Any] = {}
    # A short human-readable result, e.g. "Found 5 excerpts from 2 documents".
    summary: str


class ChatMessageOut(BaseModel):
    """One message in a conversation, from either the user or the assistant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID

    # "user" or "assistant". The frontend uses this to decide which side of the
    # screen to render the bubble on.
    role: str
    content: str

    # These three are populated only on assistant messages, which is why they
    # are all optional.
    tokens_used: Optional[int] = None
    latency: Optional[float] = None
    citations: List[Dict[str, Any]] = []

    # How the answer was produced (assistant messages saved after migration
    # a7b9c1d3e5f8; None otherwise): "rag" or "agent", the model that
    # actually answered, and the agent's tool trace.
    mode: Optional[str] = None
    model: Optional[str] = None
    steps: List[Dict[str, Any]] = []

    # The high / medium / low verdict and its reasons (services/confidence.py).
    # None on user messages and on answers saved before migration c9e1a3b5d7f0.
    confidence: Optional[Dict[str, Any]] = None

    created_at: datetime.datetime


class ChatResponse(BaseModel):
    """
    The complete result of asking a question.

    Both messages are returned, not just the answer, so the frontend can render
    the exchange without a second request and without guessing at ids.
    """

    session_id: uuid.UUID
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    citations: List[Citation]

    # Which model produced the answer. Shown in the UI and invaluable when
    # debugging: it immediately reveals whether a reply came from Groq or from
    # the offline mock fallback.
    model: str

    # Which strategy produced this answer: "rag" or "agent". Echoed back so the
    # UI can label the bubble without remembering what it sent.
    mode: str = "rag"

    # The agent's tool calls, in the order they happened. Always empty in
    # "rag" mode, where the steps are fixed and not worth listing.
    steps: List[AgentStep] = []

    # The query retrieval ACTUALLY searched with. Differs from the question
    # when a follow-up was rewritten into a standalone query
    # (services/query_rewrite.py); None in agent mode, which searches with
    # queries of its own choosing.
    search_query: Optional[str] = None

    # The same verdict as assistant_message.confidence, at the top level for
    # API clients that read only the summary fields.
    confidence: Optional[Dict[str, Any]] = None


class FeedbackCreate(BaseModel):
    """
    Body for the thumbs up / thumbs down button on an answer.

    Collecting this is what would let a real deployment measure answer quality
    over time and identify which documents retrieve badly.
    """

    # Constrained to exactly -1 or 1 by the bounds below.
    rating: int = Field(..., ge=-1, le=1,
                        description="1 for thumbs up, -1 for thumbs down.")
    comment: Optional[str] = Field(
        None, max_length=1000, description="Optional written feedback."
    )


# ======================================================================
# FEEDBACK SUMMARY -- the admin view of thumbs up / down
# ======================================================================


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
    """
    GET /api/v1/org/feedback -- how the organization rates its answers.

    `satisfaction` is up / (up + down), or None before anyone has voted. The
    `recent_negative` list is the useful part: the actual questions people
    were unhappy with, which usually point straight at a badly chunked or
    badly OCR'd document.
    """

    total: int
    up: int
    down: int
    satisfaction: Optional[float] = None
    by_mode: List[FeedbackBreakdown] = []
    by_model: List[FeedbackBreakdown] = []
    recent_negative: List[FeedbackItem] = []
