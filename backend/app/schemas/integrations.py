"""No response ever carries an OAuth token, a Stripe or Razorpay secret or a raw provider object: connections..."""
import datetime
import uuid
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


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
