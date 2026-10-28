"""Provides the reusable pieces that endpoints declare they NEED, rather than build for themselves."""
import datetime
import logging
import uuid
from typing import AsyncGenerator, Optional

import jwt  # PyJWT
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import observability, rate_limit, roles, security
from app.core.client_ip import client_ip
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import APIKey
from app.models import User
from app.schemas import TokenPayload
from app.services import sessions

logger = logging.getLogger(__name__)

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login", auto_error=False
)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_LAST_USED_RESOLUTION = datetime.timedelta(minutes=5)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Open a database session for one request, and close it afterwards."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def decode_access_token(token: str) -> TokenPayload:
    """Verify an access token's signature, expiry and type, and return its claims."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized(
            "Could not validate credentials: signature invalid or expired."
        ) from None

    if token_data.type != "access":
        raise _unauthorized("Invalid token type, access token required.")

    if token_data.sub is None:
        raise _unauthorized("Could not validate credentials: sub claim missing.")

    return token_data


async def _user_from_api_key(db: AsyncSession, raw_key: str) -> User:
    """Authenticate a program by its API key."""
    result = await db.execute(
        select(APIKey).where(APIKey.hashed_key == security.hash_token(raw_key))
    )
    key = result.scalar_one_or_none()

    invalid = _unauthorized("Invalid, expired or revoked API key.")
    if key is None or not key.is_active or key.revoked_at is not None:
        raise invalid
    if sessions.is_expired(key.expires_at):
        raise invalid

    user = await db.get(User, key.user_id)
    if user is None or user.org_id != key.org_id:
        raise invalid

    user.api_key_role = key.role
    observability.bind(user_id=user.id, org_id=user.org_id, api_key=key.key_prefix)

    now = sessions.utcnow()
    last = sessions.as_aware(key.last_used_at)
    if last is None or now - last > _LAST_USED_RESOLUTION:
        key.last_used_at = now
        await db.commit()

    return user


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
    api_key: Optional[str] = Depends(api_key_header),
) -> User:
    """Identify the caller, from an API key or an access token."""
    if api_key:
        return await _user_from_api_key(db, api_key)

    if not token:
        raise _unauthorized("Not authenticated.")

    token_data = decode_access_token(token)

    if await sessions.is_access_token_revoked(db, token_data.jti):
        raise _unauthorized("This session has been signed out.")

    try:
        user_id = uuid.UUID(token_data.sub)
    except ValueError:
        raise _unauthorized("Could not validate credentials.") from None
    user = await db.get(User, user_id)
    if not user:
        raise _unauthorized("Could not validate credentials.")

    changed = sessions.as_aware(user.password_changed_at)
    if changed is not None:
        if token_data.iat is None or token_data.iat < changed.timestamp():
            raise _unauthorized("Your password was changed. Please sign in again.")

    observability.bind(user_id=user.id, org_id=user.org_id)
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """The user from the token, plus a check that the account is still enabled."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is inactive.",
        )
    return current_user


def effective_role(user: User) -> Optional[str]:
    """The role this REQUEST acts with."""
    return roles.lower_of(user.role, getattr(user, "api_key_role", None))


def require_role(minimum: str):
    """Build a dependency that admits `minimum` and every role ranked above it."""
    wanted = roles.normalize_role(minimum)

    async def checker(current_user: User = Depends(get_current_active_user)) -> User:
        if not roles.at_least(effective_role(current_user), wanted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {wanted} role or higher.",
            )
        return current_user

    checker.__name__ = f"require_{wanted.lower()}"
    return checker


require_viewer = require_role(roles.VIEWER)
require_editor = require_role(roles.EDITOR)
require_admin = require_role(roles.ADMIN)


async def _enforce_rate_limit(name: str, identity: str, response: Response) -> None:
    decision = await rate_limit.check(name, identity)
    if decision is None:  # rate limiting, or this one limit, is switched off
        return

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    if decision.allowed:
        return

    logger.warning(
        "Rate limit exceeded",
        extra={"limit_name": name, "identity": identity, "retry_after": decision.retry_after},
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=rate_limit.refusal_message(name, decision.retry_after),
        headers={
            "Retry-After": str(decision.retry_after),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


def rate_limit_by_ip(name: str):
    """Limit by the client's IP address -- for endpoints used BEFORE anyone is"""
    rate_limit.require_known(name)

    async def limit_by_ip(request: Request, response: Response) -> None:
        await _enforce_rate_limit(name, f"ip:{client_ip(request) or 'unknown'}", response)

    limit_by_ip.__name__ = f"rate_limit_{name}_by_ip"
    return limit_by_ip


def rate_limit_by_user(name: str):
    """Limit by the signed-in user -- for chat, search and upload."""
    rate_limit.require_known(name)

    async def limit_by_user(
        response: Response, current_user: User = Depends(get_current_active_user)
    ) -> None:
        await _enforce_rate_limit(name, f"user:{current_user.id}", response)

    limit_by_user.__name__ = f"rate_limit_{name}_by_user"
    return limit_by_user
