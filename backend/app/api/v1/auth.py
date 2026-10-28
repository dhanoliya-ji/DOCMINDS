"""Handles everything to do with proving who you are."""
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
from app.models import Invite
from app.models import Organization
from app.models import RefreshToken
from app.models import User
from app.models import EMAIL_VERIFY, PASSWORD_RESET
from app.schemas import (
    EmailVerifyRequest,
    InvitePreview,
    Message,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.schemas import LogoutRequest, Token, TokenPayload, TokenRefreshRequest
from app.schemas import UserCreate, UserOut
from app.services import audit, email, sessions

router = APIRouter()


def enforce_password_policy(password: str, user_email: Optional[str]) -> None:
    """Refuse a weak password with every problem listed in one message."""
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


@router.post(
    "/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.rate_limit_by_ip("signup"))],
)
async def signup(
    user_in: UserCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """Register a new user: create an organization, or join one by invite."""
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

    if user_in.invite_token:
        invite = await find_usable_invite(db, user_in.invite_token)
        if invite is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invite link is invalid, has expired, or has already been used.",
            )
        if invite.email.lower() != user_in.email.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invite was sent to a different email address.",
            )
        organization_id = invite.org_id
        role = invite.role
        invite.accepted_at = sessions.utcnow()

    elif user_in.org_id:
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
        await db.flush()
        organization_id = organization.id
        role = roles.ADMIN

    db_user = User(
        email=user_in.email,
        hashed_password=security.get_password_hash(user_in.password),
        role=role,
        org_id=organization_id,
        is_active=True,
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

    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.post(
    "/login",
    response_model=Token,
    dependencies=[Depends(deps.rate_limit_by_ip("login"))],
)
async def login(
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Verify credentials and issue an access + refresh token pair."""
    result = await db.execute(
        select(User).where(func.lower(User.email) == form_data.username.strip().lower())
    )
    user = result.scalar_one_or_none()

    password_ok = security.verify_password(
        form_data.password, user.hashed_password if user else _dummy_hash()
    )

    if user is None or not password_ok:
        if user is not None:
            await audit.record(
                db, "user.login_failed", org_id=user.org_id, user_id=user.id, request=request,
            )
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

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
    """A bcrypt hash of a random secret, computed once on first use (not at"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = security.get_password_hash(uuid.uuid4().hex)
    return _DUMMY_HASH


@router.post("/refresh", response_model=Token)
async def refresh_token(
    payload: TokenRefreshRequest,
    db: AsyncSession = Depends(deps.get_db),
):
    """Exchange a valid refresh token for a fresh access + refresh pair."""
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

    if refresh_payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provided token is not a refresh token.",
        )

    stored = await sessions.find_refresh_token(db, payload.refresh_token)
    if stored is None or sessions.is_expired(stored.expires_at):
        raise invalid

    if stored.revoked_at is not None:
        await sessions.revoke_family(db, stored.family_id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has ended. Please sign in again.",
        )

    user = await db.get(User, stored.user_id)
    if not user:
        raise invalid
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is deactivated.",
        )

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
    """Sign out, server side."""
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


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: AsyncSession = Depends(deps.get_db)):
    """Describe an invite, so the signup page can say "Join Acme as Manager" and"""
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


_RESET_ACK = "If an account exists for that email, a password reset link has been sent."


@router.post(
    "/password-reset/request",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(deps.rate_limit_by_ip("password_reset"))],
)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """Email a single-use reset link."""
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
    dependencies=[Depends(deps.rate_limit_by_ip("password_reset"))],
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    """Set a new password using the emailed token."""
    row = await sessions.redeem_user_token(db, payload.token, PASSWORD_RESET)
    user = await db.get(User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Please request a new one.",
        )

    enforce_password_policy(payload.new_password, user.email)

    user.hashed_password = security.get_password_hash(payload.new_password)
    user.password_changed_at = sessions.utcnow()
    user.is_verified = True
    await sessions.revoke_all_sessions(db, user.id)
    await audit.record(db, "password.reset", org_id=user.org_id, user_id=user.id, request=request)
    await db.commit()

    return Message(detail="Your password has been changed. Please sign in with the new one.")


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
    dependencies=[Depends(deps.rate_limit_by_user("verify_resend"))],
)
async def resend_verification(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Send a fresh verification email to the signed-in user. The previous"""
    if current_user.is_verified:
        return Message(detail="Your email address is already confirmed.")

    await _start_email_verification(db, background_tasks, current_user)
    await db.commit()
    return Message(detail="A new confirmation link has been sent.")
