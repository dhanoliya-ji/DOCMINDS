"""file decides which ones are still ALIVE, using three tables."""
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


async def issue_token_pair(
    db: AsyncSession, user: User, family_id: Optional[uuid.UUID] = None
) -> dict:
    """Mint an access token and a refresh token, and record the refresh token."""
    access_token = security.create_access_token(
        subject=user.id,
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
    """Sign a user out everywhere: revoke all their refresh tokens."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def deny_access_token(
    db: AsyncSession, jti: Optional[str], expires_at: Optional[datetime.datetime]
) -> None:
    """Put an access token's jti on the denylist until it would have expired."""
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


async def create_user_token(
    db: AsyncSession, user_id: uuid.UUID, purpose: str, lifetime: datetime.timedelta
) -> str:
    """Create a reset or verification token, returning the PLAINTEXT to email."""
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
    """Look a token up and USE it: returns the row (now marked used) only when"""
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
