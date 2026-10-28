"""Request and response shapes for the API (Pydantic)."""
import datetime
import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from typing import Any, Dict, List, Literal, Optional


# ---- token ----
class Token(BaseModel):
    """What /auth/login and /auth/refresh return."""

    access_token: str
    refresh_token: str

    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """The claims we expect to find INSIDE a decoded token."""

    sub: Optional[str] = None

    role: Optional[str] = None
    org_id: Optional[str] = None

    type: Optional[str] = None

    jti: Optional[str] = None

    iat: Optional[float] = None
    exp: Optional[float] = None


class TokenRefreshRequest(BaseModel):
    """Body for POST /auth/refresh -- exchange a refresh token for a new pair."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Body for POST /auth/logout."""

    refresh_token: Optional[str] = None


# ---- user ----
class UserBase(BaseModel):
    """Fields shared by several user schemas, defined once."""

    email: EmailStr

    role: Literal["Admin", "Manager", "Employee"] = "Employee"


class UserCreate(UserBase):
    """Body for POST /auth/signup."""

    password: str

    org_name: Optional[str] = Field(default=None, max_length=255)

    invite_token: Optional[str] = Field(default=None, max_length=256)

    org_id: Optional[uuid.UUID] = None


class UserUpdate(BaseModel):
    """Body for editing a user. Every field is optional so a caller can send just"""

    email: Optional[EmailStr] = None
    role: Optional[Literal["Admin", "Manager", "Employee"]] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class UserOut(UserBase):
    """What the API returns about a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    is_active: bool
    is_verified: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---- account ----
RoleName = Literal["Admin", "Manager", "Employee", "admin", "editor", "viewer"]


class PasswordResetRequest(BaseModel):
    """Body for POST /auth/password-reset/request."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Body for POST /auth/password-reset/confirm."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str


class EmailVerifyRequest(BaseModel):
    """Body for POST /auth/verify-email."""

    token: str = Field(min_length=1, max_length=256)


class Message(BaseModel):
    """A plain acknowledgement, for endpoints whose result is just "done"."""

    detail: str


class InvitePreview(BaseModel):
    """What GET /auth/invites/{token} tells a not-yet-signed-up visitor, so the"""

    email: str
    role: str
    org_name: str
    expires_at: datetime.datetime


class MemberOut(BaseModel):
    """One member of the caller's organization."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime.datetime


class MemberUpdate(BaseModel):
    """Body for PATCH /org/members/{id}. Send only what should change."""

    role: Optional[RoleName] = None
    is_active: Optional[bool] = None


class InviteCreate(BaseModel):
    """Body for POST /org/invites."""

    email: EmailStr
    role: RoleName = "Employee"


class InviteOut(BaseModel):
    """An invite as the invites list shows it. No token -- see the module docstring."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime.datetime
    accepted_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    invited_by: Optional[uuid.UUID] = None
    created_at: datetime.datetime
    status: str = "pending"


class InviteCreated(InviteOut):
    """The response to creating an invite. The ONLY time the token is returned."""

    token: str
    invite_url: str


class APIKeyCreate(BaseModel):
    """Body for POST /org/api-keys."""

    name: str = Field(min_length=1, max_length=255)
    role: RoleName = "Employee"
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class APIKeyOut(BaseModel):
    """An API key as the list shows it: the prefix only, never the key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    role: str
    user_id: uuid.UUID
    is_active: bool
    expires_at: Optional[datetime.datetime] = None
    last_used_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class APIKeyCreated(APIKeyOut):
    """The response to creating a key. The ONLY time the full key is shown."""

    key: str


class AuditLogOut(BaseModel):
    """One audit entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    details: Dict[str, Any]
    ip_address: Optional[str] = None
    user_id: Optional[uuid.UUID] = None
    created_at: datetime.datetime


class AuditLogPage(BaseModel):
    """One page of audit entries, newest first."""

    items: List[AuditLogOut]
    total: int
    limit: int
    offset: int


# ---- organization ----
class OrganizationBase(BaseModel):
    """The company name."""

    name: str


class OrganizationCreate(OrganizationBase):
    """Body for creating an organization directly."""

    pass


class OrganizationUpdate(OrganizationBase):
    """Body for renaming an organization."""

    pass


class OrganizationOut(OrganizationBase):
    """An organization as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime


# ---- project ----
class ProjectBase(BaseModel):
    """The one field a human actually chooses."""

    name: str = Field(..., min_length=1, max_length=255)


class ProjectCreate(ProjectBase):
    """Body for POST /projects/."""

    pass


class ProjectUpdate(ProjectBase):
    """Body for PATCH /projects/{id} -- renaming a project."""

    pass


class ProjectOut(ProjectBase):
    """A project as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID

    org_id: uuid.UUID

    created_at: datetime.datetime
    updated_at: datetime.datetime


class ProjectDetail(ProjectOut):
    """GET /projects/{id} -- one project, with a summary of what is in it."""

    document_count: int = 0
    ready_count: int = 0


class EmbeddingPoint(BaseModel):
    """One chunk in the 3D embedding map: where it sits and what it is."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    snippet: str
    x: float
    y: float
    z: float


class EmbeddingMap(BaseModel):
    """GET /projects/{id}/embedding-map -- a 3D PCA projection of the chunks."""

    points: list[EmbeddingPoint]
    total: int
    truncated: bool
    explained_variance: list[float]


# ---- document ----
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


# ---- rag ----
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


# ---- collab ----
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


# ---- usage ----
class QuotaUsage(BaseModel):
    used: int
    quota: int
    unlimited: bool
    remaining: Optional[int] = None


class ModelUsage(BaseModel):
    """One model's share of the month."""

    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class ModeUsage(BaseModel):
    """One chat mode's share of the month ("rag" or "agent")."""

    mode: str
    requests: int
    total_tokens: int


class UsageSummary(BaseModel):
    period_start: datetime.datetime
    period_end: datetime.datetime

    requests: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    tokens: QuotaUsage
    documents: QuotaUsage
    storage_bytes: QuotaUsage

    by_model: List[ModelUsage]
    by_mode: List[ModeUsage]


# ---- webhook ----
class WebhookCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    event_types: List[str] = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class WebhookUpdate(BaseModel):
    """Every field optional: send only what changes."""

    url: Optional[str] = Field(None, min_length=1, max_length=2048)
    event_types: Optional[List[str]] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    description: Optional[str] = None
    event_types: List[str]
    is_active: bool
    created_at: Optional[datetime.datetime] = None


class WebhookCreated(WebhookOut):
    secret: str


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    next_attempt_at: Optional[datetime.datetime] = None
    delivered_at: Optional[datetime.datetime] = None
    created_at: Optional[datetime.datetime] = None
    payload: Dict[str, Any]


class WebhookEventType(BaseModel):
    name: str
    description: str


# ---- integrations ----
class ProviderOut(BaseModel):
    id: str
    name: str
    configured: bool


class AuthorizeOut(BaseModel):
    authorization_url: str


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    account_label: str
    status: str
    last_error: Optional[str] = None
    created_at: Optional[datetime.datetime] = None


class RemoteItemOut(BaseModel):
    id: str
    name: str
    kind: str
    mime_type: Optional[str] = None
    modified_at: Optional[str] = None
    revision: Optional[str] = None
    size: Optional[int] = None
    web_url: Optional[str] = None
    importable: bool
    has_children: bool = False


class RemoteItemPage(BaseModel):
    items: List[RemoteItemOut]
    next_cursor: Optional[str] = None


class ImportRequest(BaseModel):
    project_id: uuid.UUID
    item_ids: List[str] = Field(..., min_length=1, max_length=50)
    auto_sync: bool = True


class ImportItemOut(BaseModel):
    """One picked item's progress in a background import."""

    item_id: str
    name: Optional[str] = None
    status: Literal["pending", "downloading", "imported", "duplicate", "already_imported", "skipped", "failed"]
    document_id: Optional[str] = None
    detail: Optional[str] = None


class ImportJobOut(BaseModel):
    """A background import: POST .../import answers with this (202), and"""

    id: uuid.UUID
    connection_id: Optional[uuid.UUID] = None
    project_id: uuid.UUID
    provider: str
    status: Literal["queued", "running", "completed"]
    auto_sync: bool
    items: List[ImportItemOut]
    counts: Dict[str, int] = {}
    created_at: Optional[datetime.datetime] = None
    finished_at: Optional[datetime.datetime] = None


class DisconnectOut(BaseModel):
    """The local disconnect always happens; this says whether the provider"""

    revoked: bool
    manage_url: Optional[str] = None
    detail: Optional[str] = None


class LinkedDocumentOut(BaseModel):
    document_id: uuid.UUID
    project_id: uuid.UUID
    connection_id: Optional[uuid.UUID] = None
    provider: str
    external_id: str
    external_name: str
    filename: str
    document_status: Optional[str] = None
    revision: Optional[str] = None
    web_url: Optional[str] = None
    auto_sync: bool
    sync_status: str
    last_error: Optional[str] = None
    last_synced_at: Optional[datetime.datetime] = None
    last_checked_at: Optional[datetime.datetime] = None


class LinkUpdate(BaseModel):
    auto_sync: bool


class SyncResult(BaseModel):
    document_id: uuid.UUID
    outcome: str
    sync_status: str
    last_error: Optional[str] = None


class PlanLimits(BaseModel):
    """0 = unlimited, as everywhere else."""

    monthly_tokens: int
    max_documents: int
    max_storage_bytes: int


class PlanOut(BaseModel):
    id: Literal["free", "pro", "team"]
    price_label: str
    limits: PlanLimits
    purchasable: bool


class BillingStatus(BaseModel):
    plan: str
    provider: Literal["stripe", "razorpay"] = "stripe"
    subscription_status: Optional[str] = None
    renews_at: Optional[datetime.datetime] = None
    has_customer: bool
    can_manage: bool = False
    can_cancel: bool = False
    limits: PlanLimits
    plans: List[PlanOut]


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "team"]


class RedirectOut(BaseModel):
    url: str


class DailyUsage(BaseModel):
    date: str
    requests: int
    tokens: int
    cost_usd: float


class DailyDocuments(BaseModel):
    date: str
    processed: int
    failed: int


class DailyUsers(BaseModel):
    date: str
    active_users: int


class DailyFeedback(BaseModel):
    date: str
    up: int
    down: int


class TopQuestion(BaseModel):
    question: str
    count: int


class AnalyticsOverview(BaseModel):
    days: int
    since: datetime.datetime
    totals: dict
    usage: List[DailyUsage]
    documents: List[DailyDocuments]
    active_users: List[DailyUsers]
    feedback: List[DailyFeedback]
    top_questions: List[TopQuestion]
