"""
deps.py  (dependencies)
=======================
WHAT THIS FILE DOES
-------------------
Provides the reusable pieces that endpoints declare they NEED, rather than
build for themselves:

    get_db                 an open database session
    get_current_user       the logged-in user, decoded from the JWT
    get_current_active_user  ...and confirmed not to be deactivated
    RoleChecker            ...and confirmed to hold a specific role

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

from typing import AsyncGenerator, List

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.token import TokenPayload

# ----------------------------------------------------------------------
# HOW THE TOKEN IS READ OFF THE REQUEST
# ----------------------------------------------------------------------
# This extracts the token from the standard header:
#
#     Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
#
# and returns just the token part. If the header is absent it raises 401
# automatically, before any of our code runs.
#
# `tokenUrl` does not affect that behaviour at all -- it tells the /docs page
# which endpoint to call when you click "Authorize", so you can try protected
# endpoints straight from the browser.
reusable_oauth2 = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


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


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(reusable_oauth2),
) -> User:
    """
    Verify the JWT and load the user it identifies.

    Three checks happen here, in order:
        1. Is the signature valid and the token unexpired?  (jwt.decode)
        2. Is it an ACCESS token, not a refresh token?
        3. Does the user it names still exist?
    """
    try:
        # `jwt.decode` verifies the signature and the `exp` claim, raising if
        # the token was tampered with or has expired. It is the actual security
        # boundary -- everything after it can trust the payload.
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        token_data = TokenPayload(**payload)

        # ---- CRITICAL CHECK ----
        # Refresh tokens are valid signatures too, but they last SEVEN DAYS.
        # Accepting one here would let a stolen refresh token be used directly
        # as an API credential for a week, defeating the entire point of having
        # short-lived access tokens.
        if token_data.type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type, access token required.",
            )

        if token_data.sub is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials: sub claim missing.",
            )

    except (JWTError, ValueError):
        # JWTError covers a bad signature, a malformed token and expiry.
        # ValueError covers a payload that does not fit TokenPayload.
        #
        # Note the message is deliberately vague. Telling an attacker WHICH
        # check failed -- "expired" versus "bad signature" -- hands them free
        # information about how their forgery attempt went wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials: signature invalid or expired.",
        ) from None

    # The token is genuine, but the user may have been deleted since it was
    # issued -- a token stays valid for its full lifetime regardless.
    statement = select(User).where(User.id == token_data.sub)
    result = await db.execute(statement)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

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
        if current_user.role not in self.allowed_roles:
            # 403 Forbidden, NOT 401 Unauthorized. The distinction is real:
            #   401 -- "I do not know who you are."
            #   403 -- "I know exactly who you are, and you may not do this."
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The user does not have enough privileges.",
            )
        return current_user
