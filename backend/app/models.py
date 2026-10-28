"""Every database table (SQLAlchemy models)."""
import datetime
import uuid
from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from typing import List, Optional

from app.core.config import settings
from app.db.base_class import Base


# ---- organization ----
class Organization(Base):
    """A tenant. Table name is generated automatically as "organizations"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    monthly_token_quota: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_documents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_storage_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free", server_default="free")
    billing_provider: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    plan_renews_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    users: Mapped[List["User"]] = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="organization", cascade="all, delete-orphan"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="organization", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="organization", cascade="all, delete-orphan"
    )


# ---- user ----
class User(Base):
    """A person who can sign in. Table name: "users"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[str] = mapped_column(
        String(50), default="Employee", nullable=False
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    password_changed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    api_key_role = None

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="users"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="user", cascade="save-update, merge", passive_deletes=True
    )
    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        "ChatSession", back_populates="user", cascade="all, delete-orphan"
    )


# ---- project ----
class Project(Base):
    """A document collection and search boundary. Table name: "projects"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="projects"
    )

    documents: Mapped[List["Document"]] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )

    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        "ChatSession", back_populates="project", cascade="all, delete-orphan"
    )


# ---- document ----
class Document(Base):
    """One uploaded file. Table name: "documents"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    file_type: Mapped[str] = mapped_column(String(50), nullable=False)

    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    storage_backend: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local", nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, index=True
    )

    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    processing_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    resume_after: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    duplicate_hash: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # JSONB is Postgres's binary JSON type: queryable and indexable, unlike a
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

    project: Mapped["Project"] = relationship("Project", back_populates="documents")

    chunks: Mapped[List["Chunk"]] = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )


# ---- chunk ----
class Chunk(Base):
    """One piece of a document, stored as readable text."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    page_number: Mapped[int] = mapped_column(Integer, nullable=True)

    meta_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    embeddings: Mapped[List["ChunkEmbedding"]] = relationship(
        "ChunkEmbedding", back_populates="chunk", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_chunks_document_order", "document_id", "chunk_index"),
    )


class ChunkEmbedding(Base):
    """The vector representation of one chunk -- its meaning, expressed as numbers."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )

    embedding: Mapped[List[float]] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION), nullable=False
    )

    model_name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunk: Mapped["Chunk"] = relationship("Chunk", back_populates="embeddings")

    __table_args__ = (
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ---- chat ----
class ChatSession(Base):
    """One conversation. Table name: "chat_sessions"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(
        String(255), default="New Chat", nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")
    project: Mapped["Project"] = relationship(
        "Project", back_populates="chat_sessions"
    )
    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """One message in a conversation. Table name: "chat_messages"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[str] = mapped_column(String(50), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)


    tokens_used: Mapped[int] = mapped_column(Integer, nullable=True)

    latency: Mapped[float] = mapped_column(Float, nullable=True)

    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    steps: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )

    confidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="messages"
    )
    feedbacks: Mapped[List["Feedback"]] = relationship(
        "Feedback", back_populates="message", cascade="all, delete-orphan"
    )


class Feedback(Base):
    """A rating on one assistant answer. Table name: "feedbacks"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    comment: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    message: Mapped["ChatMessage"] = relationship(
        "ChatMessage", back_populates="feedbacks"
    )


# ---- share ----
SHARE_KINDS = ("message", "session", "project")


class ShareLink(Base):
    """A read-only public link. Table name: "share_links"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    view_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_viewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def target_id(self) -> Optional[uuid.UUID]:
        """Whichever of the three foreign keys `kind` says is set."""
        return {
            "message": self.message_id,
            "session": self.session_id,
            "project": self.project_id,
        }.get(self.kind)


# ---- prompt ----
PROMPT_SCOPES = ("personal", "org")


class PromptTemplate(Base):
    """A saved prompt. Table name: "prompt_templates"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")

    title: Mapped[str] = mapped_column(String(120), nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---- api_key ----
class APIKey(Base):
    """A programmatic credential."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    hashed_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    role: Mapped[str] = mapped_column(
        String(50), default="Employee", server_default="Employee", nullable=False
    )

    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="api_keys")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="api_keys"
    )


# ---- audit ----
class AuditLog(Base):
    """One recorded action. Table name: "audit_logs"."""

    __table_args__ = (
        Index("ix_audit_logs_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    action: Mapped[str] = mapped_column(String(255), nullable=False)

    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="audit_logs"
    )
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")


# ---- invite ----
class Invite(Base):
    """An invitation to join an organization. Table name: "invites"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(50), nullable=False)

    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship("Organization")

    @property
    def status(self) -> str:
        """"accepted", "revoked", "expired" or "pending" -- in that order of"""
        if self.accepted_at is not None:
            return "accepted"
        if self.revoked_at is not None:
            return "revoked"
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=datetime.timezone.utc)
        if expires <= datetime.datetime.now(datetime.timezone.utc):
            return "expired"
        return "pending"


# ---- refresh_token ----
class RefreshToken(Base):
    """One issued refresh token. Table name: "refresh_tokens"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---- revoked_token ----
class RevokedToken(Base):
    """A signed-out access token. Table name: "revoked_tokens"."""

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---- user_token ----
PASSWORD_RESET = "password_reset"


EMAIL_VERIFY = "email_verify"


class UserToken(Base):
    """A single-use emailed token. Table name: "user_tokens"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    purpose: Mapped[str] = mapped_column(String(32), nullable=False)

    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---- usage ----
class UsageEvent(Base):
    """One metered action. Table name: "usage_events"."""

    __table_args__ = (
        Index("ix_usage_events_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(14, 8, asdecimal=False), default=0, server_default="0", nullable=False
    )

    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---- webhook ----
PENDING = "pending"        # queued, or waiting between retries


SUCCEEDED = "succeeded"    # the receiver answered 2xx


FAILED = "failed"          # out of attempts, or refused permanently


class WebhookEndpoint(Base):
    """A URL that receives an organization's events."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    event_types: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)

    secret: Mapped[str] = mapped_column(String(128), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WebhookDelivery(Base):
    """One event sent (or being sent) to one endpoint, with its outcome."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),
        Index("ix_webhook_deliveries_status_next", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    next_attempt_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---- connector ----
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


# ---- billing ----
class BillingEvent(Base):
    """One processed Stripe event. Table name: "billing_events"."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
