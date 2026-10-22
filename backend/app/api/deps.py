"""
deps.py  (dependencies)
=======================
WHAT THIS FILE DOES
-------------------
Provides the reusable pieces that endpoints declare they NEED, rather than
build for themselves:

    get_db                 an open database session
    get_current_user       the caller, from a JWT *or* an X-API-Key header
    get_current_active_user  ...and confirmed not to be deactivated
    require_role("editor") ...and confirmed to hold at least that role
    require_viewer / require_editor / require_admin   the three ready-made
    RoleChecker            the older list-based check, kept for compatibility

WHAT "DEPENDENCY INJECTION" MEANS IN FASTAPI
--------------------------------------------
An endpoint states its requirements in its signature:

    async def list_projects(
        db: AsyncSession = Depends(deps.get_db),
        current_user: User = Depends(deps.get_current_active_user),
    ):

FastAPI reads that, runs each dependency first, and passes the results in. The
endpoint body never opens a connection or parses a token.

WHY THIS IS A SECURITY FEATURE, NOT JUST TIDINESS
--------------------------------------------------
If the token is missing, malformed, expired, or belongs to a deactivated
account, the dependency raises `HTTPException` and the endpoint body NEVER
RUNS. Authentication cannot be forgotten, because it is not something the
endpoint has to remember to do -- it is a precondition of being called at all.

By the time your code executes, `current_user` is guaranteed to be a real,
active user. There is no unauthenticated code path.
"""

import datetime
import uuid
from typing import AsyncGenerator, List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import roles, security
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.api_key import APIKey
from app.models.user import User
from app.schemas.token import TokenPayload
from app.services import sessions

# ----------------------------------------------------------------------
# HOW THE CREDENTIAL IS READ OFF THE REQUEST
# ----------------------------------------------------------------------
# Two ways to prove who you are:
#
#     Authorization: Bearer eyJhbGciOiJIUzI1NiIs...   a person, via login
#     X-API-Key: dm_Xq3...                            a program, via API key
#
# `auto_error=False` on both: by default each would raise 401 the moment ITS
# header was missing, which would make the other way impossible. Instead both
# return None when absent, and `get_current_user` decides.
#
# `tokenUrl` does not affect behaviour at all -- it tells the /docs page which
# endpoint to call when you click "Authorize".
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login", auto_error=False
)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# How stale `api_keys.last_used_at` may get before a request refreshes it.
# Writing it on EVERY request would turn every read into a write.
_LAST_USED_RESOLUTION = datetime.timedelta(minutes=5)


def _unauthorized(detail: str) -> HTTPException:
    # The WWW-Authenticate header is what the HTTP spec asks a 401 to carry.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Open a database session for one request, and close it afterwards.

    The `finally` block runs even if the endpoint raises, so a connection is
    never leaked. Leaked connections are insidious: everything works fine until
    the pool is exhausted and the whole API stops responding at once.
    """
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def decode_access_token(token: str) -> TokenPayload:
    """
    Verify an access token's signature, expiry and type, and return its claims.

    Shared by get_current_user and /auth/logout (which needs the jti and the
    expiry of the very token it is revoking).
    """
    try:
        # `jwt.decode` verifies the signature and the `exp` claim, raising if
        # the token was tampered with or has expired. It is the actual security
        # boundary -- everything after it can trust the payload.
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (JWTError, ValueError):
        # JWTError covers a bad signature, a malformed token and expiry.
        # ValueError covers a payload that does not fit TokenPayload.
        #
        # Note the message is deliberately vague. Telling an attacker WHICH
        # check failed -- "expired" versus "bad signature" -- hands them free
        # information about how their forgery attempt went wrong.
        raise _unauthorized(
            "Could not validate credentials: signature invalid or expired."
        ) from None

    # ---- CRITICAL CHECK ----
    # Refresh tokens are valid signatures too, but they last SEVEN DAYS.
    # Accepting one here would let a stolen refresh token be used directly as
    # an API credential for a week, defeating the entire point of having
    # short-lived access tokens.
    if token_data.type != "access":
        raise _unauthorized("Invalid token type, access token required.")

    if token_data.sub is None:
        raise _unauthorized("Could not validate credentials: sub claim missing.")

    return token_data


async def _user_from_api_key(db: AsyncSession, raw_key: str) -> User:
    """
    Authenticate a program by its API key.

    The key is hashed and looked up -- the plaintext is never stored, so there
    is nothing else to compare against. The key must be active, not revoked
    and not expired, and its owner must still exist in the same organization.

    The returned user carries `api_key_role`, so role checks use the LOWER of
    the key's role and the owner's current role. A "viewer" key stays a viewer
    key even though an Admin created it.
    """
    result = await db.execute(
        select(APIKey).where(APIKey.hashed_key == security.hash_token(raw_key))
    )
    key = result.scalar_one_or_none()

    # One message for every failure, for the same reason as login: the caller
    # learns nothing about WHICH check their key failed.
    invalid = _unauthorized("Invalid, expired or revoked API key.")
    if key is None or not key.is_active or key.revoked_at is not None:
        raise invalid
    if sessions.is_expired(key.expires_at):
        raise invalid

    user = await db.get(User, key.user_id)
    # Scoped to the key's organization: a key can never reach outside the
    # tenant it was created in, even if something about its owner changed.
    if user is None or user.org_id != key.org_id:
        raise invalid

    user.api_key_role = key.role

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
    """
    Identify the caller, from an API key or an access token.

    For an access token, these checks happen in order:
        1. Is the signature valid and the token unexpired?  (jwt.decode)
        2. Is it an ACCESS token, not a refresh token?
        3. Has it been signed out (its jti is on the denylist)?
        4. Does the user it names still exist?
        5. Was it issued before the user's last password reset?
    """
    if api_key:
        return await _user_from_api_key(db, api_key)

    if not token:
        raise _unauthorized("Not authenticated.")

    token_data = decode_access_token(token)

    # 3. Signed out. Without this, logout would only forget the token in the
    # browser while any copy of it kept working until expiry.
    if await sessions.is_access_token_revoked(db, token_data.jti):
        raise _unauthorized("This session has been signed out.")

    # 4. The token is genuine, but the user may have been deleted since it was
    # issued -- a token stays valid for its full lifetime regardless.
    try:
        user_id = uuid.UUID(token_data.sub)
    except ValueError:
        raise _unauthorized("Could not validate credentials.") from None
    user = await db.get(User, user_id)
    if not user:
        raise _unauthorized("Could not validate credentials.")

    # 5. A password reset signs out every session that used the old password.
    # `iat` is fractional (see core/security.py), so this is exact: a token
    # minted a moment after the reset passes, one minted before it does not.
    changed = sessions.as_aware(user.password_changed_at)
    if changed is not None:
        if token_data.iat is None or token_data.iat < changed.timestamp():
            raise _unauthorized("Your password was changed. Please sign in again.")

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    The user from the token, plus a check that the account is still enabled.

    Note this dependency itself DEPENDS on another dependency. FastAPI resolves
    the chain automatically: decode the token, load the user, then check the
    flag. Layering it this way means the deactivation check cannot be skipped by
    accident.

    Deactivating an account takes effect immediately, even though the user's
    existing token has not yet expired -- which is exactly what you want when
    someone leaves the company.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is inactive.",
        )
    return current_user


def effective_role(user: User) -> Optional[str]:
    """
    The role this REQUEST acts with.

    Normally just the user's role. For an API-key request it is the lower of
    the key's role and the owner's role -- see `_user_from_api_key`.
    """
    return roles.lower_of(user.role, getattr(user, "api_key_role", None))


def require_role(minimum: str):
    """
    Build a dependency that admits `minimum` and every role ranked above it.

        current_user: User = Depends(deps.require_role("editor"))

    Accepts the generic names (viewer / editor / admin) or the stored ones
    (Employee / Manager / Admin) -- see core/roles.py. An unknown name raises
    at IMPORT time, so a typo can never quietly lock everyone out or let
    everyone in.

    WHY A FUNCTION THAT RETURNS A FUNCTION
    A dependency cannot take arguments of its own at request time, so the
    argument is captured when the endpoint is DEFINED: `require_role("editor")`
    runs once, at import, and returns the `checker` FastAPI calls on every
    request.
    """
    wanted = roles.normalize_role(minimum)

    async def checker(current_user: User = Depends(get_current_active_user)) -> User:
        if not roles.at_least(effective_role(current_user), wanted):
            # 403 Forbidden, NOT 401 Unauthorized:
            #   401 -- "I do not know who you are."
            #   403 -- "I know exactly who you are, and you may not do this."
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {wanted} role or higher.",
            )
        return current_user

    checker.__name__ = f"require_{wanted.lower()}"
    return checker


# The three ready-made levels. Endpoints use these by name so the intent reads
# straight off the signature:
#     viewer  read, search, chat
#     editor  + upload / delete / re-index documents, manage projects
#     admin   + members, invites, API keys, audit log
require_viewer = require_role(roles.VIEWER)
require_editor = require_role(roles.EDITOR)
require_admin = require_role(roles.ADMIN)


class RoleChecker:
    """
    A dependency that also requires the user to hold one of several roles.

    Used like this:

        current_user: User = Depends(deps.RoleChecker(["Admin", "Manager"]))

    WHY A CLASS RATHER THAN A FUNCTION?
    Because it needs configuring per endpoint. A plain function dependency takes
    no arguments of its own. Writing `RoleChecker(["Admin"])` creates an
    instance holding that list, and FastAPI then calls the instance -- Python
    allows this because of `__call__` below. It is a small, standard trick for
    building parameterised dependencies.
    """

    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_active_user)) -> User:
        """
        `__call__` makes an instance behave like a function, so FastAPI can
        treat it as a dependency. Its own parameter is another dependency, so
        authentication and the active check still run first.
        """
        if effective_role(current_user) not in self.allowed_roles:
            # 403 Forbidden, NOT 401 Unauthorized. The distinction is real:
            #   401 -- "I do not know who you are."
            #   403 -- "I know exactly who you are, and you may not do this."
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The user does not have enough privileges.",
            )
        return current_user
