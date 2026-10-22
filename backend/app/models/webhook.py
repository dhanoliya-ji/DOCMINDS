"""
webhook.py
==========
WHAT THIS FILE DOES
-------------------
Defines the two WEBHOOK tables:

    webhook_endpoints   "send these events to this URL" -- registered by an
                        organization's Admin (api/v1/webhooks.py).
    webhook_deliveries  one row per (event, endpoint): the payload we sent,
                        how many attempts it took, what the receiver answered.
                        This is the DELIVERY LOG admins read when a receiver
                        says "we never got it".

WHAT A WEBHOOK IS
-----------------
Instead of a customer's system asking us "is the document done yet?" every
few seconds (polling), WE call THEM the moment it is: an HTTP POST to a URL
they gave us, carrying a small JSON description of what happened.

THE SECRET
----------
Every endpoint gets a random secret, shown to the Admin ONCE at creation.
Each delivery is signed with it (HMAC-SHA256, services/webhooks.py), so the
receiver can check that a POST really came from DocMinds and was not altered.

Unlike API keys and invite tokens, this secret cannot be stored as a hash:
to SIGN with it we need the secret itself, not a fingerprint of it. It is
never returned by the API after creation, and it is only as sensitive as the
webhook (it lets someone forge events to that ONE receiver -- nothing more).

DELETE RULES
------------
    organization deleted -> its endpoints and deliveries go too (CASCADE).
    endpoint deleted     -> its delivery log goes with it (CASCADE).
"""

import datetime
import uuid
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

# The lifecycle of one delivery row.
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

    # 2048: the practical maximum URL length browsers and servers agree on.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Which events this endpoint wants, e.g. ["document.processed"]. The
    # wildcard "*" means every event, including ones added in the future.
    event_types: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)

    # The HMAC signing key (see the module docstring for why it is not hashed).
    secret: Mapped[str] = mapped_column(String(128), nullable=False)

    # A paused endpoint receives nothing, but keeps its log and its secret.
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
        # "The latest deliveries of this endpoint" -- the delivery log page.
        Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),
        # The retry sweep: "pending rows whose next attempt is due".
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
    # The exact JSON body POSTed. Kept so a retry sends the same bytes'
    # meaning, and so an admin can see what was sent.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default=PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The receiver's HTTP status on the last attempt (None: no answer at all).
    last_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # A short reason for the last failure: "timeout", "HTTP 500", ...
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
