"""
user_token.py
=============
WHAT THIS FILE DOES
-------------------
Defines the USER_TOKENS table: single-use, expiring tokens that are emailed to
a user to prove they control their address.

Two kinds share the table, told apart by `purpose`:

    password_reset   "Forgot your password?" -- the link lets you set a new one
    email_verify     "Confirm your email"    -- the link sets is_verified

WHY ONE TABLE FOR BOTH
----------------------
They have exactly the same shape and the same rules: random, hashed, short
lived, usable once. Two tables would be the same code written twice.

THE RULES THE CODE ENFORCES
---------------------------
    * Only the SHA-256 hash is stored, so a database leak yields no live links.
    * `used_at` is set the moment a token is redeemed; a second click fails.
    * Asking for a new link invalidates any older unused one of the same kind
      for the same user, so only the most recent email works.
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

# The two values `purpose` may hold.
PASSWORD_RESET = "password_reset"
EMAIL_VERIFY = "email_verify"


class UserToken(Base):
    """A single-use emailed token. Table name: "user_tokens"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # PASSWORD_RESET or EMAIL_VERIFY (constants above).
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
