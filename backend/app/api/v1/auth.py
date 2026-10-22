"""
auth.py
=======
WHAT THIS FILE DOES
-------------------
Handles everything to do with proving who you are:

    POST /auth/signup    create an account (and usually an organization)
    POST /auth/login     exchange email + password for tokens
    POST /auth/refresh   exchange a refresh token for a new pair

HOW SIGNUP CREATES A TENANT
---------------------------
Signup does double duty, decided by which field you send:

    org_name supplied  ->  create a NEW organization, and make this user its
                           Admin. This is how a company onboards.

    org_id supplied    ->  join an EXISTING organization with the requested
                           role. This is how a colleague is added.

That is why the first user of any organization is always an Admin: somebody has
to be able to administer it, and at that moment they are the only person there.

WHY LOGIN LOOKS DIFFERENT FROM EVERY OTHER ENDPOINT
---------------------------------------------------
It takes `OAuth2PasswordRequestForm` rather than a JSON body, so the request is
`application/x-www-form-urlencoded` and the email arrives under the field name
`username`.

That is not an oversight -- it is the OAuth2 password-flow standard. Following
it means the interactive docs at /docs get a working "Authorize" button, and
any standard OAuth2 client library can talk to this API unmodified.
"""

import jwt  # PyJWT
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import security
from app.core.config import settings
from app.models.organization import Organization
from app.models.user import User
from app.schemas.token import Token, TokenPayload, TokenRefreshRequest
from app.schemas.user import UserCreate, UserOut

router = APIRouter()


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(
    user_in: UserCreate,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Register a new user, creating or joining an organization.

    Returns `UserOut`, which contains no password field of any kind -- see
    schemas/user.py for why that omission is a real security control.
    """
    # ---- Is this email already taken? ----
    # The database also enforces this with a unique constraint. Checking here
    # too lets us return a friendly message instead of a raw integrity error.
    statement = select(User).where(User.email == user_in.email)
    result = await db.execute(statement)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists in the system.",
        )

    # ---- Decide the tenant ----
    if user_in.org_name:
        # Creating a brand-new organization.
        organization = Organization(name=user_in.org_name)
        db.add(organization)
        # `flush` sends the INSERT so Postgres assigns the id we need below,
        # but does NOT commit -- so if anything fails afterwards, the whole
        # thing rolls back and no half-created organization is left behind.
        await db.flush()

        # Whoever creates the organization administers it. The `role` they
        # requested is deliberately ignored here.
        role = "Admin"

    elif user_in.org_id:
        # Joining an existing organization.
        statement = select(Organization).where(Organization.id == user_in.org_id)
        result = await db.execute(statement)
        organization = result.scalar_one_or_none()

        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The specified organization was not found.",
            )

        role = user_in.role

    else:
        # Neither field supplied. Pydantic cannot express "exactly one of
        # these", so the rule is enforced here.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Either org_name (create organization) or org_id "
                "(join organization) must be provided."
            ),
        )

    db_user = User(
        email=user_in.email,
        # >>> The plain password is hashed HERE and never stored as given. <<<
        hashed_password=security.get_password_hash(user_in.password),
        role=role,
        org_id=organization.id,
        is_active=True,
        # Groundwork for email verification, which is not enforced yet.
        is_verified=False,
    )

    db.add(db_user)
    # One commit saves the organization (if new) and the user together, as a
    # single transaction.
    await db.commit()
    # Reload so database-generated columns (the timestamps) are populated.
    await db.refresh(db_user)

    return db_user


@router.post("/login", response_model=Token)
async def login(
    db: AsyncSession = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Verify credentials and issue an access + refresh token pair.

    Remember the email arrives as `form_data.username` -- an OAuth2 convention,
    not a mistake.
    """
    statement = select(User).where(User.email == form_data.username)
    result = await db.execute(statement)
    user = result.scalar_one_or_none()

    # ---- One combined check, deliberately ----
    # "user does not exist" and "wrong password" return the SAME message. If
    # they differed, an attacker could enumerate which email addresses have
    # accounts simply by watching which error comes back.
    if not user or not security.verify_password(
        form_data.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    # A deactivated account gets a distinct message. That is fine here: the
    # credentials were already correct, so nothing is being leaked to someone
    # who did not already know them.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has been deactivated.",
        )

    # ---- Embed tenant and role in the access token ----
    # This is what lets the API authorise a request straight from the verified
    # token, with no database lookup on every call.
    access_token = security.create_access_token(
        subject=user.id,
        additional_claims={"org_id": str(user.org_id), "role": user.role},
    )

    # The refresh token carries NO role or org. Its only job is to identify the
    # user well enough to mint a new access token -- which is then built from
    # their CURRENT role, so a permission change takes effect on next refresh
    # rather than being frozen for seven days.
    refresh_token = security.create_refresh_token(subject=user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=Token)
async def refresh_token(
    payload: TokenRefreshRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Exchange a valid refresh token for a fresh access + refresh pair.

    This is what keeps a user signed in for days while their ACCESS token still
    expires every 30 minutes. The frontend calls it transparently when a
    request comes back 401.
    """
    try:
        token_data = jwt.decode(
            payload.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        refresh_payload = TokenPayload(**token_data)

        # ---- CRITICAL ----
        # Reject an ACCESS token presented here. Without this check, the two
        # token types would be interchangeable, and the whole point of having a
        # short-lived credential separate from a long-lived one collapses.
        if refresh_payload.type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provided token is not a refresh token.",
            )

    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from None

    # Re-read the user from the database rather than trusting anything cached
    # in the token. In the days since it was issued they may have been
    # deactivated, or had their role changed.
    statement = select(User).where(User.id == refresh_payload.sub)
    result = await db.execute(statement)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is deactivated.",
        )

    # Built from the user's CURRENT role, freshly read above.
    access_token = security.create_access_token(
        subject=user.id,
        additional_claims={"org_id": str(user.org_id), "role": user.role},
    )

    # A NEW refresh token is issued too, extending the window from now. This is
    # called "refresh token rotation": each use replaces the previous token, so
    # a user who stays active never gets logged out, while an abandoned token
    # still expires on schedule.
    new_refresh_token = security.create_refresh_token(subject=user.id)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }
