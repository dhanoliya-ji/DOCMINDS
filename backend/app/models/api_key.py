"""
api_key.py
==========
WHAT THIS FILE DOES
-------------------
Defines the API_KEYS table -- long-lived credentials for PROGRAMS rather than
people.

WHY BOTH JWTs AND API KEYS?
---------------------------
They serve different callers:

    JWT       for a human in a browser. Short-lived (30 min), obtained by
              typing a password, refreshed silently in the background.

    API key   for a script, a cron job or another service. There is nobody to
              type a password, so the credential must be long-lived and stored
              in the caller's configuration.

THE KEY IS HASHED, EXACTLY LIKE A PASSWORD
------------------------------------------
This is the important design point. When a key is generated it is shown to the
user ONCE and never stored in readable form. What the database keeps is:

    key_prefix   "sk_live_a3f9"   the first few characters, for display only
    hashed_key   SHA-256 of the whole key

To authenticate a request we hash the presented key and look for a matching
`hashed_key`. So a leaked database yields no usable credentials -- exactly the
same reasoning as `hashed_password` on the User model.

The prefix exists purely so a dashboard can say "sk_live_a3f9..." and let
someone identify which of their keys is which, without ever revealing one.
"""

import datetime
import uuid
from typing import TYPE_CHECKING, Optional


from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class APIKey(Base):
    """A programmatic credential."""

    # ---- WHY THIS IS SET EXPLICITLY ----
    # The automatic naming rule in db/base_class.py inserts an underscore before
    # every internal capital letter. "APIKey" is three capitals in a row, so it
    # would become the unreadable table name "a_p_i_keys". This is exactly the
    # irregular case the rule cannot handle, so we override it.
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # A human label, e.g. "CI pipeline" or "Nightly sync job", so keys can be
    # told apart and revoked confidently.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # The first few visible characters, for identification in a UI. Safe to
    # display: far too short to be guessed back into the full key.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # SHA-256 of the full key. Exactly 64 hex characters, hence String(64).
    #
    # `unique=True` prevents the same key existing twice; `index=True` because
    # every authenticated request looks a key up by this column, and a scan of
    # the whole table on each request would be unacceptable.
    #
    # NOTE: SHA-256 is used here rather than bcrypt, and that is correct.
    # Bcrypt is deliberately slow to defend SHORT, guessable human passwords.
    # An API key is long and randomly generated, so it cannot be brute-forced,
    # and a slow hash would just add latency to every single request.
    hashed_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # Who created the key, and which tenant it acts on behalf of. Both are
    # stored: the user for accountability, the organization because that is
    # what scopes the data the key can reach.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Lets a key be revoked instantly without deleting the row, so its audit
    # history survives.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # The most a request made with this key may do: Employee (viewer),
    # Manager (editor) or Admin. Capped further at request time by the role of
    # the user who owns the key, so demoting a person demotes their keys too.
    # `server_default` gives rows created before this column existed the
    # safest value rather than NULL.
    role: Mapped[str] = mapped_column(
        String(50), default="Employee", server_default="Employee", nullable=False
    )

    # When the key last authenticated a request, so an Admin can spot keys
    # nobody uses any more and revoke them. Updated at most every few minutes
    # to avoid a database write on every single request.
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When it was revoked. `is_active` says THAT it was; this says WHEN, for
    # the API keys screen and the audit trail.
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Optional expiry. Nullable means "never expires". Short-lived keys are the
    # safer default for anything handed to a third party.
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
