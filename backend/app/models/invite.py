"""
invite.py
=========
WHAT THIS FILE DOES
-------------------
Defines the INVITES table -- the ONLY way to join an organization that already
exists.

WHY INVITES EXIST AT ALL
------------------------
Signup used to accept an `org_id` and a `role` straight from the request body.
Organization ids are not secret (they sit in every JWT and API response), so
anybody who learned one could sign up INTO that company -- as an Admin, since
the role was theirs to choose. That is a tenant-isolation hole, not a missing
nicety.

An invite closes it. An Admin creates one for a specific email address and a
specific role; the system emails a link containing a random token; signing up
with that token puts the new user into the inviting organization, with the
role the ADMIN chose. Whatever role the client sends is ignored.

WHAT IS STORED, AND WHAT IS NOT
-------------------------------
The token itself is never stored -- only its SHA-256 hash, exactly as with API
keys. Someone who reads this table gets nothing they can paste into a signup
form.

THE LIFECYCLE
-------------
    created     accepted_at NULL, revoked_at NULL, expires_at in the future
    accepted    accepted_at set   -> cannot be used again
    revoked     revoked_at set    -> an Admin changed their mind
    expired     expires_at passed -> just ignored
"""

import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class Invite(Base):
    """An invitation to join an organization. Table name: "invites"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Stored lower-cased, so "Ada@Example.com" and "ada@example.com" match.
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # The role the new member will get. Chosen by the inviting Admin -- this is
    # the whole point of the table.
    role: Mapped[str] = mapped_column(String(50), nullable=False)

    # SHA-256 of the invite token. Unique and indexed: signup looks invites up
    # by this column.
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

    # Who sent it. SET NULL rather than CASCADE: removing the Admin who sent
    # an invite should not silently cancel it.
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship("Organization")

    @property
    def status(self) -> str:
        """
        "accepted", "revoked", "expired" or "pending" -- in that order of
        precedence, so an invite accepted yesterday does not later read as
        "expired". Only a "pending" invite can be used to sign up.
        """
        if self.accepted_at is not None:
            return "accepted"
        if self.revoked_at is not None:
            return "revoked"
        expires = self.expires_at
        # SQLite (the fast tests) returns naive datetimes; we always store UTC.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=datetime.timezone.utc)
        if expires <= datetime.datetime.now(datetime.timezone.utc):
            return "expired"
        return "pending"
