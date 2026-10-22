"""
refresh_token.py
================
WHAT THIS FILE DOES
-------------------
Defines the REFRESH_TOKENS table: the server's record of every refresh token it
has issued, so that each one can be ROTATED and REVOKED.

WHY A STATELESS JWT IS NOT ENOUGH FOR A REFRESH TOKEN
-----------------------------------------------------
A JWT is valid until it expires, full stop. For a 30-minute access token that
is tolerable. For a 7-day refresh token it means:

    * "Log out" cannot actually log anyone out -- the token keeps working.
    * A stolen refresh token gives a thief a week of fresh access tokens, and
      nothing the real user does can stop it.

Keeping a row per token fixes both. Logout sets `revoked_at`; /auth/refresh
refuses any token whose row is revoked, expired or missing.

ROTATION, AND HOW IT CATCHES A THIEF
------------------------------------
Every successful /auth/refresh REVOKES the presented token and issues a new
one in the same `family_id`. Normally that is invisible: the browser always
holds the newest token.

But suppose a token is stolen. Now two parties hold it. Whoever uses it first
gets a new token; when the OTHER party later presents the old, already-rotated
one, the server sees a revoked token being reused -- which can only happen if
it was copied. It then revokes the WHOLE family, signing out both the thief
and the victim. The victim signs in again with their password; the thief
cannot.

WHAT IS STORED
--------------
Only the SHA-256 hash of the token, never the token itself, for the same
reason passwords are hashed: a database leak must not hand out live sessions.
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class RefreshToken(Base):
    """One issued refresh token. Table name: "refresh_tokens"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # Every token descended from one sign-in shares a family. Reuse of a
    # rotated token revokes the whole family -- see the module docstring.
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
