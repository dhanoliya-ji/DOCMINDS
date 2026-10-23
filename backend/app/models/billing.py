"""
billing.py  (database model)
============================
WHAT THIS FILE DOES
-------------------
Defines BILLING_EVENTS: the id of every Stripe webhook event we have already
applied.

WHY
---
Stripe delivers each event AT LEAST once: a slow answer, a network blip or a
manual "resend" in the dashboard delivers the same event again. Applying
"subscription updated" twice is harmless, but applying an OLD "subscription
created" after a newer "subscription deleted" would put a cancelled customer
back on Pro. Recording each id, in the same transaction as the change it
made, makes every event take effect exactly once (services/billing.py).

The plan itself lives on the organization (models/organization.py).
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class BillingEvent(Base):
    """One processed Stripe event. Table name: "billing_events"."""

    __tablename__ = "billing_events"

    # Stripe's own event id ("evt_..."): the primary key IS the dedupe check.
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
