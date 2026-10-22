"""
auth.py
=======
WHAT THIS FILE DOES
-------------------
Handles everything to do with proving who you are:

    POST /auth/signup                    create an account
    POST /auth/login                     exchange email + password for tokens
    POST /auth/refresh                   rotate a refresh token for a new pair
    POST /auth/logout                    end the session, server side
    GET  /auth/invites/{token}           what an invite link is for
    POST /auth/password-reset/request    email a reset link
    POST /auth/password-reset/confirm    set a new password with that link
    POST /auth/verify-email              confirm an email address
    POST /auth/verify-email/resend       send the confirmation email again

HOW SIGNUP DECIDES THE TENANT -- AND WHY THE CLIENT NO LONGER CAN
-----------------------------------------------------------------
    no invite token    ->  a NEW organization is created and this user is its
                           Admin. This is how a company onboards.

    invite token       ->  join the organization that sent the invite, with
                           the role the inviting ADMIN chose. This is the ONLY
                           way into an existing organization.

Signup used to accept `org_id` and `role` from the request body. Organization
ids are not secrets, so anybody who learned one could join that company as an
Admin. Now the client never picks either: `role` is ignored, and `org_id`
without an invite is refused with an explanation.

SESSIONS: SHORT ACCESS TOKENS, ROTATING REFRESH TOKENS
------------------------------------------------------
Login returns a 30-minute access token and a 7-day refresh token. The refresh
token is RECORDED server side (models/refresh_token.py), which is what makes
the two things a stateless JWT cannot do possible:

    rotation    every /auth/refresh revokes the old refresh token and issues a
                new one. Presenting an already-rotated token is treated as
                theft and revokes that whole sign-in.
    logout      revokes the refresh token AND denylists the access token, so
                neither keeps working after "Sign out".

WHY LOGIN LOOKS DIFFERENT FROM EVERY OTHER ENDPOINT
---------------------------------------------------
It takes `OAuth2PasswordRequestForm` rather than a JSON body, so the request is
`application/x-www-form-urlencoded` and the email arrives under the field name
`username`. That is the OAuth2 password-flow standard; following it gives the
/docs page a working "Authorize" button.
"""

import datetime
import uuid
from typing import Optional

import jwt  # PyJWT
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import roles, security
from app.core.config import settings
from app.models.invite import Invite
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.user_token import EMAIL_VERIFY, PASSWORD_RESET
from app.schemas.account import (
    EmailVerifyRequest,
    InvitePreview,
    Message,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.schemas.token import LogoutRequest, Token, TokenPayload, TokenRefreshRequest
from app.schemas.user import UserCreate, UserOut
from app.services import audit, email, sessions

router = APIRouter()


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------


def enforce_password_policy(password: str, user_email: Optional[str]) -> None:
    """
    Refuse a weak password with every problem listed in one message.

    422, the same status a malformed body gets, because it IS a validation
    failure -- just one that needs the email to evaluate, which a Pydantic
    field validator cannot see.
    """
    problems = security.password_problems(password, user_email)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=" ".join(problems),
        )


async def find_usable_invite(db: AsyncSession, token: str) -> Optional[Invite]:
    """The pending invite a token belongs to, or None if it cannot be used."""
    result = await db.execute(
        select(Invite).where(Invite.token_hash == security.hash_token(token))
    )
    invite = result.scalar_one_or_none()
    if invite is None or invite.status != "pending":
        return None
    return invite


async def _start_email_verification(
    db: AsyncSession, background_tasks: BackgroundTasks, user: User
) -> None:
    """Create a verification token and queue the email (sent after the reply)."""
    token = await sessions.create_user_token(
        db,
        user.id,
        EMAIL_VERIFY,
        datetime.timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS),
    )
    background_tasks.add_task(email.send_verification_email, user.email, token)


# ----------------------------------------------------------------------
# SIGNUP
# ----------------------------------------------------------------------


@router.post(
    "/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    # Per client IP (core/rate_limit.py): every sign-up creates an organization.
    dependencies=[Depends(deps.rate_limit_by_ip("signup"))],
)
async def signup(
    user_in: UserCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Register a new user: create an organization, or join one by invite.

    Returns `UserOut`, which contains no password field of any kind -- see
    schemas/user.py for why that omission is a real security control.
    """
    # ---- Is this email already taken? ----
    # The database also enforces this with a unique constraint. Checking here
    # too lets us return a friendly message instead of a raw integrity error.
    #
    # DOCUMENTED TRADE-OFF: this message does confirm that an address is
    # registered. Hiding it would mean a silent "check your email" for every
    # signup, which is confusing when email verification is off (the default,
    # and SMTP may not even be configured). The oracle is throttled by
    # RATE_LIMIT_SIGNUP per IP, and login/password reset reveal nothing.
    existing = await db.execute(
        select(User).where(func.lower(User.email) == user_in.email.lower())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists in the system.",
        )

    enforce_password_policy(user_in.password, user_in.email)

    invite: Optional[Invite] = None

    # ---- Decide the tenant ----
    if user_in.invite_token:
        invite = await find_usable_invite(db, user_in.invite_token)
        if invite is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invite link is invalid, has expired, or has already been used.",
            )
        # The invite is for a PERSON, not for whoever holds the link. Without
        # this check, a forwarded invite email would let anyone in.
        if invite.email.lower() != user_in.email.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invite was sent to a different email address.",
            )
        organization_id = invite.org_id
        # >>> The role comes from the invite. `user_in.role` is ignored. <<<
        role = invite.role
        invite.accepted_at = sessions.utcnow()

    elif user_in.org_id:
        # The old way in. Refused with an explanation rather than silently
        # creating a new organization the user did not ask for.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Joining an existing organization requires an invite. Ask an "
                "Admin of that organization to invite your email address."
            ),
        )

    else:
        name = (user_in.org_name or "").strip() or f"{user_in.email.split('@')[0]}'s organization"
        organization = Organization(name=name)
        db.add(organization)
        # `flush` sends the INSERT so Postgres assigns the id we need below,
        # but does NOT commit -- so if anything fails afterwards, the whole
        # thing rolls back and no half-created organization is left behind.
        await db.flush()
        organization_id = organization.id
        # Whoever creates the organization administers it.
        role = roles.ADMIN

    db_user = User(
        email=user_in.email,
        # >>> The plain password is hashed HERE and never stored as given. <<<
        hashed_password=security.get_password_hash(user_in.password),
        role=role,
        org_id=organization_id,
        is_active=True,
        # Following an emailed invite link already proves the address is
        # theirs; a self-service signup still has to confirm it.
        is_verified=invite is not None,
    )
    db.add(db_user)
    await db.flush()

    await audit.record(
        db, "user.signup", org_id=organization_id, user_id=db_user.id, request=request,
        details={"email": db_user.email, "role": role, "via_invite": invite is not None},
    )
    if invite is not None:
        await audit.record(
            db, "invite.accept", org_id=organization_id, user_id=db_user.id, request=request,
            details={"invite_id": invite.id, "email": invite.email, "role": invite.role},
        )
    else:
        await _start_email_verification(db, background_tasks, db_user)

    # One commit saves the organization (if new), the user, the invite's
    # acceptance and the audit rows together, as a single transaction.
    await db.commit()
    await db.refresh(db_user)
    return db_user


# ----------------------------------------------------------------------
# LOGIN, REFRESH, LOGOUT
# ----------------------------------------------------------------------


@router.post(
    "/login",
    response_model=Token,
    # Per client IP: what makes password guessing hopeless (core/rate_limit.py).
    dependencies=[Depends(deps.rate_limit_by_ip("login"))],
)
async def login(
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Verify credentials and issue an access + refresh token pair.

    Remember the email arrives as `form_data.username` -- an OAuth2 convention,
    not a mistake.
    """
    # Case-insensitive, exactly like signup, password reset and invites: an
    # account created as "Alice@x.com" must be reachable as "alice@x.com".
    result = await db.execute(
        select(User).where(func.lower(User.email) == form_data.username.strip().lower())
    )
    user = result.scalar_one_or_none()

    # ---- ALWAYS run bcrypt, even for an unknown email ----
    # The identical error message below is not enough on its own: if an
    # unknown email skipped bcrypt it would answer in ~1 ms while a real one
    # takes ~100 ms, and the TIMING would reveal which accounts exist. So an
    # unknown email is checked against a throwaway hash of equal cost.
    password_ok = security.verify_password(
        form_data.password, user.hashed_password if user else _dummy_hash()
    )

    # ---- One combined check, deliberately ----
    # "user does not exist" and "wrong password" return the SAME message. If
    # they differed, an attacker could enumerate which email addresses have
    # accounts simply by watching which error comes back.
    if user is None or not password_ok:
        if user is not None:
            # Recorded for the account's Admins (a burst of these is what a
            # password-guessing attack looks like) but never reflected in the
            # response.
            await audit.record(
                db, "user.login_failed", org_id=user.org_id, user_id=user.id, request=request,
            )
            await db.commit()
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

    if settings.REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your email address first. Check your inbox for the link.",
        )

    tokens = await sessions.issue_token_pair(db, user)
    await audit.record(db, "user.login", org_id=user.org_id, user_id=user.id, request=request)
    await db.commit()
    return tokens


_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    """A bcrypt hash of a random secret, computed once on first use (not at
    import, so starting the app stays fast). Used by login to spend the same
    time on an unknown email as on a real one."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = security.get_password_hash(uuid.uuid4().hex)
    return _DUMMY_HASH


@router.post("/refresh", response_model=Token)
async def refresh_token(
    payload: TokenRefreshRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Exchange a valid refresh token for a fresh access + refresh pair.

    The presented token is REVOKED and a new one issued in the same family
    ("rotation"). Presenting a token that was already rotated means two
    parties hold copies of it -- so the whole family is revoked and both must
    sign in again. See models/refresh_token.py.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token.",
    )
    try:
        token_data = jwt.decode(
            payload.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        refresh_payload = TokenPayload(**token_data)
    except (jwt.InvalidTokenError, ValueError):
        raise invalid from None

    # ---- CRITICAL ----
    # Reject an ACCESS token presented here. Without this check, the two token
    # types would be interchangeable.
    if refresh_payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided token is not a refresh token.",
        )

    stored = await sessions.find_refresh_token(db, payload.refresh_token)
    # Signed by us but never recorded: issued before server-side sessions
    # existed, or its row was deleted with the user. Either way, sign in again.
    if stored is None or sessions.is_expired(stored.expires_at):
        raise invalid

    if stored.revoked_at is not None:
        # REUSE of a rotated or signed-out token. Revoke the whole family.
        await sessions.revoke_family(db, stored.family_id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has ended. Please sign in again.",
        )

    # Re-read the user rather than trusting anything cached in the token: they
    # may have been deactivated, or had their role changed, since.
    user = await db.get(User, stored.user_id)
    if not user:
        raise invalid
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is deactivated.",
        )

    # ---- Claim the token ATOMICALLY ----
    # Checking `revoked_at is None` above and then setting it in Python is a
    # race: two requests with the same token (an attacker replaying a stolen
    # copy at the same moment as the real client, or two browser tabs) could
    # both pass the check and both receive a live pair. A conditional UPDATE
    # lets the database decide: exactly one request flips revoked_at from
    # NULL, and the loser sees rowcount 0 and is treated as reuse.
    claimed = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == stored.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=sessions.utcnow())
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await sessions.revoke_family(db, stored.family_id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has ended. Please sign in again.",
        )
    # Built from the user's CURRENT role, freshly read above.
    tokens = await sessions.issue_token_pair(db, user, family_id=stored.family_id)
    await db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    body: Optional[LogoutRequest] = None,
    token: Optional[str] = Depends(deps.reusable_oauth2),
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Sign out, server side.

    Two things are revoked, each if supplied:
        * the ACCESS token in the Authorization header -> denylisted by jti
          until it would have expired anyway
        * the REFRESH token in the body -> its whole sign-in family revoked

    Always 204, even when there was nothing valid to revoke. Logout is
    idempotent, and a client that is signing out does not need to know its
    token had already expired.
    """
    user_id = None

    if token:
        try:
            access = deps.decode_access_token(token)
        except HTTPException:
            access = None
        if access is not None:
            expires_at = (
                datetime.datetime.fromtimestamp(access.exp, datetime.timezone.utc)
                if access.exp
                else None
            )
            await sessions.deny_access_token(db, access.jti, expires_at)
            user_id = access.sub

    if body is not None and body.refresh_token:
        stored = await sessions.find_refresh_token(db, body.refresh_token)
        # Only revoke a refresh token belonging to the same person as the
        # access token, when there is one -- no signing out somebody else.
        if stored is not None and (user_id is None or str(stored.user_id) == user_id):
            await sessions.revoke_family(db, stored.family_id)
            user_id = str(stored.user_id)

    if user_id is not None:
        parsed = _as_uuid(user_id)
        user = await db.get(User, parsed) if parsed else None
        if user is not None:
            await audit.record(db, "user.logout", org_id=user.org_id, user_id=user.id, request=request)

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# INVITE PREVIEW
# ----------------------------------------------------------------------


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: AsyncSession = Depends(deps.get_db)):
    """
    Describe an invite, so the signup page can say "Join Acme as Manager" and
    prefill (and lock) the email field.

    Public on purpose -- the visitor has no account yet. It reveals only what
    the invite email already told its recipient.
    """
    invite = await find_usable_invite(db, token)
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This invite link is invalid, has expired, or has already been used.",
        )
    organization = await db.get(Organization, invite.org_id)
    return InvitePreview(
        email=invite.email,
        role=invite.role,
        org_name=organization.name if organization else "",
        expires_at=invite.expires_at,
    )


# ----------------------------------------------------------------------
# PASSWORD RESET
# ----------------------------------------------------------------------

_RESET_ACK = "If an account exists for that email, a password reset link has been sent."


@router.post(
    "/password-reset/request",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    # Per client IP. Each request sends an email, so without a limit this
    # endpoint could be used to flood someone's inbox.
    dependencies=[Depends(deps.rate_limit_by_ip("password_reset"))],
)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Email a single-use reset link.

    THE RESPONSE IS IDENTICAL WHETHER OR NOT THE EMAIL HAS AN ACCOUNT. If it
    differed, this endpoint would let anyone test which addresses are
    registered. The email itself is sent in the background, after the reply,
    so the response time does not give it away either.
    """
    result = await db.execute(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    user = result.scalar_one_or_none()

    if user is not None and user.is_active:
        token = await sessions.create_user_token(
            db,
            user.id,
            PASSWORD_RESET,
            datetime.timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
        await audit.record(
            db, "password.reset_requested", org_id=user.org_id, user_id=user.id, request=request,
        )
        await db.commit()
        background_tasks.add_task(email.send_password_reset_email, user.email, token)

    return Message(detail=_RESET_ACK)


@router.post(
    "/password-reset/confirm",
    response_model=Message,
    # Shares the password-reset budget: slows down guessing reset tokens.
    dependencies=[Depends(deps.rate_limit_by_ip("password_reset"))],
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    Set a new password using the emailed token.

    On success EVERY existing session of the account ends: refresh tokens are
    revoked and `password_changed_at` makes outstanding access tokens fail.
    The usual reason to reset a password is suspecting someone else knows it.
    """
    row = await sessions.redeem_user_token(db, payload.token, PASSWORD_RESET)
    user = await db.get(User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Please request a new one.",
        )

    # If this raises, nothing is committed -- including the token being marked
    # used -- so the user can try again with a better password and the same link.
    enforce_password_policy(payload.new_password, user.email)

    user.hashed_password = security.get_password_hash(payload.new_password)
    user.password_changed_at = sessions.utcnow()
    # Receiving the email proved they own the address.
    user.is_verified = True
    await sessions.revoke_all_sessions(db, user.id)
    await audit.record(db, "password.reset", org_id=user.org_id, user_id=user.id, request=request)
    await db.commit()

    return Message(detail="Your password has been changed. Please sign in with the new one.")


# ----------------------------------------------------------------------
# EMAIL VERIFICATION
# ----------------------------------------------------------------------


@router.post("/verify-email", response_model=Message)
async def verify_email(
    payload: EmailVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    """Mark the account's email as confirmed, using the emailed token."""
    row = await sessions.redeem_user_token(db, payload.token, EMAIL_VERIFY)
    user = await db.get(User, row.user_id) if row is not None else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired. Please request a new one.",
        )

    user.is_verified = True
    await audit.record(db, "user.email_verified", org_id=user.org_id, user_id=user.id, request=request)
    await db.commit()
    return Message(detail="Your email address has been confirmed.")


@router.post(
    "/verify-email/resend",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    # Per user: every call sends an email and writes a token row, so an
    # unthrottled loop could flood an inbox and the user_tokens table.
    dependencies=[Depends(deps.rate_limit_by_user("verify_resend"))],
)
async def resend_verification(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Send a fresh verification email to the signed-in user. The previous
    link stops working, so only the newest email counts."""
    if current_user.is_verified:
        return Message(detail="Your email address is already confirmed.")

    await _start_email_verification(db, background_tasks, current_user)
    await db.commit()
    return Message(detail="A new confirmation link has been sent.")
