"""
audit.py
========
WHAT THIS FILE DOES
-------------------
Defines the AUDIT_LOGS table -- a permanent record of who did what, and when.

WHY AN AUDIT LOG MATTERS HERE
-----------------------------
This application holds a company's internal documents. Sooner or later somebody
needs to answer questions like:

    "Who uploaded this contract, and when?"
    "Which documents did this person access before they left?"
    "Who deleted the Q3 policy?"

Application logs are not good enough for that: they rotate, they are unstructured
and they are usually thrown away after a week. An audit trail is structured,
queryable and kept.

THE ONE UNUSUAL DESIGN DECISION
-------------------------------
`user_id` uses `ON DELETE SET NULL`, whereas nearly every other foreign key in
this schema uses `ON DELETE CASCADE`.

That is deliberate, and it is the whole point of an audit log. If deleting a
user also deleted their audit entries, then removing an account would erase the
evidence of everything that account ever did -- which is precisely the situation
an audit trail exists to prevent.

So the record survives. `user_id` becomes NULL, but the action, the timestamp,
the IP address and the details remain.
"""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class AuditLog(Base):
    """One recorded action. Table name: "audit_logs"."""

    # The audit log screen always asks "this organization's events, newest
    # first" -- so one composite index serves every page of it, instead of
    # sorting the whole table on every request.
    __table_args__ = (
        Index("ix_audit_logs_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # What happened, as a dotted string: "user.login", "document.upload",
    # "project.delete", "chat.query".
    #
    # The "noun.verb" convention keeps related events sorting together, so
    # `WHERE action LIKE 'document.%'` returns every document-related event.
    action: Mapped[str] = mapped_column(String(255), nullable=False)

    # Free-form context for this specific action, as JSONB. A "document.upload"
    # entry might carry the filename and size; a "chat.query" entry might carry
    # the question and how many chunks were retrieved.
    #
    # JSONB because different actions need genuinely different fields, and a
    # fixed set of columns would be mostly NULL for most rows.
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Where the request came from. 45 characters because that is the maximum
    # length of an IPv6 address in its longest textual form -- an
    # IPv4-mapped IPv6 address such as
    # "0000:0000:0000:0000:0000:ffff:192.168.100.228".
    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)

    # Which tenant this event belongs to, so one organization's audit trail can
    # be shown without exposing anyone else's.
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # >>> SET NULL, NOT CASCADE -- see the module docstring <<<
    # Nullable also because some actions are performed by the system itself
    # (scheduled jobs, migrations) with no user behind them.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # No `updated_at` here, and that is intentional: an audit record is
    # append-only. A log that can be edited is not evidence of anything.
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="audit_logs"
    )
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")
