"""
revoked_token.py
================
WHAT THIS FILE DOES
-------------------
Defines the REVOKED_TOKENS table -- the "denylist" of ACCESS tokens that were
signed out before they expired.

WHY THIS IS NEEDED
------------------
An access token is a signed JWT. The server does not look it up anywhere to
decide whether it is valid; it checks the signature and the expiry.

The cost is that the server cannot "un-issue" one. Without this table, signing
out would only delete the token from the browser, while a copy (in a proxy log,
on a shared machine, on a stolen laptop) went on working for up to 30 minutes.

So logout records the token's `jti` (its unique id, see core/security.py) here,
and `get_current_user` refuses any token whose jti is listed.

WHY THE TABLE STAYS SMALL
-------------------------
A row only matters until the token it names would have expired anyway -- after
that, the expiry check rejects the token on its own. `expires_at` records that
moment, and logout deletes rows past it as it goes, so the table holds at most
one access-token lifetime of sign-outs.

Redis would be a natural home for this (a key with a TTL). Postgres is used so
that there is ONE required datastore for authentication: the API must work in
INGESTION_MODE=inline deployments, which have no Redis at all.
"""

import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class RevokedToken(Base):
    """A signed-out access token. Table name: "revoked_tokens"."""

    # The token's `jti` claim (32 hex characters from uuid4().hex). It is the
    # primary key because the only query ever run is "is this jti here?".
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)

    # When the token would have expired by itself; rows past this are useless
    # and are pruned.
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
