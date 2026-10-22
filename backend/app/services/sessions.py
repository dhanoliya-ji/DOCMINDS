"""
sessions.py
===========
WHAT THIS FILE DOES
-------------------
The server-side half of signing in and out. core/security.py MAKES tokens; this
file decides which ones are still ALIVE, using three tables:

    refresh_tokens   every refresh token issued, so it can be rotated/revoked
    revoked_tokens   access tokens signed out before they expired (denylist)
    user_tokens      single-use emailed links (password reset, verification)

WHY THIS IS NOT IN THE ENDPOINTS
--------------------------------
Login, refresh, logout, password reset and member deactivation all need some
of these operations. Written inline, the same "revoke every session of this
user" would exist in four places -- and the one that drifted would be the
security bug. Here each rule exists once.

NOTHING HERE COMMITS
--------------------
Every function only stages changes in the caller's session. The endpoint
commits once, so "password changed" and "every old session revoked" land in
the same transaction or not at all.

A NOTE ON TIMEZONES
-------------------
Columns are `timestamptz`, which Postgres returns timezone-AWARE. Other
databases (SQLite, used by the fast tests) hand back NAIVE datetimes, and
Python refuses to compare naive with aware. `as_aware` treats a naive value as
UTC, which is what we always store, so the comparisons work everywhere.
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.models.refresh_token import RefreshToken
from app.models.revoked_token import RevokedToken
from app.models.user import User
from app.models.user_token import UserToken


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def as_aware(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """Attach UTC to a naive datetime; leave an aware one alone."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=datetime.timezone.utc)


def is_expired(value: Optional[datetime.datetime]) -> bool:
    """Has this moment passed? A missing expiry never expires."""
    return value is not None and as_aware(value) <= utcnow()


# ----------------------------------------------------------------------
# ACCESS + REFRESH TOKEN PAIRS
# ----------------------------------------------------------------------


async def issue_token_pair(
    db: AsyncSession, user: User, family_id: Optional[uuid.UUID] = None
) -> dict:
    """
    Mint an access token and a refresh token, and record the refresh token.

    `family_id` is passed on rotation so the new token joins the old one's
    family; a fresh sign-in starts a new family. See models/refresh_token.py
    for why families exist.
    """
    access_token = security.create_access_token(
        subject=user.id,
        # Embedded so a request can be authorised from the token alone.
        additional_claims={"org_id": str(user.org_id), "role": user.role},
    )
    refresh_token = security.create_refresh_token(subject=user.id)

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(refresh_token),
            family_id=family_id or uuid.uuid4(),
            expires_at=utcnow() + datetime.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


async def find_refresh_token(db: AsyncSession, token: str) -> Optional[RefreshToken]:
    """The stored row for a presented refresh token, or None."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(token))
    )
    return result.scalar_one_or_none()


async def revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    """Revoke every still-live token descended from one sign-in."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Sign a user out everywhere: revoke all their refresh tokens.

    Used on password reset, deactivation and removal. Their CURRENT access
    tokens die separately -- through `password_changed_at` after a reset, or
    the `is_active` check after a deactivation -- within the same request.
    """
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


# ----------------------------------------------------------------------
# THE ACCESS-TOKEN DENYLIST
# ----------------------------------------------------------------------


async def deny_access_token(
    db: AsyncSession, jti: Optional[str], expires_at: Optional[datetime.datetime]
) -> None:
    """
    Put an access token's jti on the denylist until it would have expired.

    Also prunes entries whose tokens have expired anyway: a cheap, indexed
    delete that keeps the table at roughly one token-lifetime of sign-outs
    without needing a scheduled cleanup job.
    """
    if not jti:
        return
    await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))
    if await db.get(RevokedToken, jti) is None:
        db.add(
            RevokedToken(
                jti=jti,
                expires_at=expires_at
                or utcnow() + datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            )
        )


async def is_access_token_revoked(db: AsyncSession, jti: Optional[str]) -> bool:
    if not jti:
        return False
    return await db.get(RevokedToken, jti) is not None


# ----------------------------------------------------------------------
# SINGLE-USE EMAILED TOKENS
# ----------------------------------------------------------------------


async def create_user_token(
    db: AsyncSession, user_id: uuid.UUID, purpose: str, lifetime: datetime.timedelta
) -> str:
    """
    Create a reset or verification token, returning the PLAINTEXT to email.

    Any earlier unused token of the same purpose is marked used first, so
    only the newest email's link works. That matters for reset: if an old
    email sits in a compromised inbox, asking for a new link kills it.
    """
    await db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user_id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    token = security.generate_token()
    db.add(
        UserToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=security.hash_token(token),
            expires_at=utcnow() + lifetime,
        )
    )
    return token


async def redeem_user_token(db: AsyncSession, token: str, purpose: str) -> Optional[UserToken]:
    """
    Look a token up and USE it: returns the row (now marked used) only when
    it exists, has the right purpose, is unused and unexpired. Otherwise None
    -- and the caller gives one generic error, never which check failed.
    """
    result = await db.execute(
        select(UserToken).where(UserToken.token_hash == security.hash_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.purpose != purpose or row.used_at is not None:
        return None
    if is_expired(row.expires_at):
        return None
    row.used_at = utcnow()
    return row
