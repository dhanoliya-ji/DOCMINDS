"""Everything an organization's ADMINS manage about who can get in, and the record of what they did once inside."""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import roles, security
from app.core.config import settings
from app.models import APIKey
from app.models import AuditLog
from app.models import Invite
from app.models import Organization
from app.models import User
from app.schemas import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyOut,
    AuditLogOut,
    AuditLogPage,
    InviteCreate,
    InviteCreated,
    InviteOut,
    MemberOut,
    MemberUpdate,
)
from app.schemas import FeedbackSummary
from app.schemas import UsageSummary
from app.services import audit, email, feedback, sessions, usage

router = APIRouter(dependencies=[Depends(deps.require_admin)])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


async def _get_member(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> User:
    """A member of THIS organization, or 404 -- never another tenant's user."""
    result = await db.execute(select(User).where(User.id == user_id, User.org_id == org_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise _not_found("Member")
    return member


async def _active_admin_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.org_id == org_id, User.role == roles.ADMIN, User.is_active.is_(True))
    )
    return result.scalar_one()


async def _ensure_not_last_admin(db: AsyncSession, member: User) -> None:
    """Refuse a change that would leave the organization without an active Admin."""
    if member.role == roles.ADMIN and member.is_active:
        if await _active_admin_count(db, member.org_id) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An organization must keep at least one active Admin.",
            )


def _refuse_self(current_user: User, member: User, verb: str) -> None:
    if member.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You cannot {verb} your own account. Ask another Admin.",
        )


@router.get("/members", response_model=List[MemberOut])
async def list_members(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Every member of the caller's organization, oldest first."""
    result = await db.execute(
        select(User).where(User.org_id == current_user.org_id).order_by(User.created_at)
    )
    return result.scalars().all()


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member(
    user_id: uuid.UUID,
    payload: MemberUpdate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Change a member's role and/or deactivate or reactivate them."""
    member = await _get_member(db, current_user.org_id, user_id)

    if payload.role is not None:
        new_role = roles.normalize_role(payload.role)
        if new_role != member.role:
            _refuse_self(current_user, member, "change the role of")
            if roles.rank(new_role) < roles.rank(member.role):
                await _ensure_not_last_admin(db, member)
            old_role = member.role
            member.role = new_role
            await audit.record(
                db, "member.role_change", org_id=current_user.org_id, user_id=current_user.id,
                request=request,
                details={"member_id": member.id, "email": member.email, "from": old_role, "to": new_role},
            )

    if payload.is_active is not None and payload.is_active != member.is_active:
        if payload.is_active is False:
            _refuse_self(current_user, member, "deactivate")
            await _ensure_not_last_admin(db, member)
            member.is_active = False
            await sessions.revoke_all_sessions(db, member.id)
            action = "member.deactivate"
        else:
            member.is_active = True
            action = "member.reactivate"
        await audit.record(
            db, action, org_id=current_user.org_id, user_id=current_user.id, request=request,
            details={"member_id": member.id, "email": member.email},
        )

    await db.commit()
    await db.refresh(member)
    return member


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Remove a member from the organization permanently."""
    member = await _get_member(db, current_user.org_id, user_id)
    _refuse_self(current_user, member, "remove")
    await _ensure_not_last_admin(db, member)

    await audit.record(
        db, "member.remove", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"member_id": member.id, "email": member.email, "role": member.role},
    )
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _invite_out(invite: Invite) -> dict:
    """Serialise an invite with its derived `status`."""
    data = InviteOut.model_validate(invite).model_dump()
    data["status"] = invite.status
    return data


@router.post("/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Invite an email address to join this organization with a given role."""
    target_email = payload.email.lower()
    role = roles.normalize_role(payload.role)

    existing = await db.execute(select(User).where(func.lower(User.email) == target_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already has an account.",
        )

    now = sessions.utcnow()
    pending = await db.execute(
        select(Invite).where(
            Invite.org_id == current_user.org_id,
            Invite.email == target_email,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
    )
    for old in pending.scalars().all():
        old.revoked_at = now

    token = security.generate_token()
    invite = Invite(
        org_id=current_user.org_id,
        email=target_email,
        role=role,
        token_hash=security.hash_token(token),
        expires_at=now + datetime.timedelta(days=settings.INVITE_EXPIRE_DAYS),
        invited_by=current_user.id,
    )
    db.add(invite)
    await db.flush()

    await audit.record(
        db, "invite.create", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"invite_id": invite.id, "email": target_email, "role": role},
    )
    await db.commit()
    await db.refresh(invite)

    organization = await db.get(Organization, current_user.org_id)
    background_tasks.add_task(
        email.send_invite_email,
        target_email,
        organization.name if organization else settings.PROJECT_NAME,
        role,
        token,
    )

    return {**_invite_out(invite), "token": token, "invite_url": email.invite_link(token)}


@router.get("/invites", response_model=List[InviteOut])
async def list_invites(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Every invite this organization has sent, newest first, with its status."""
    result = await db.execute(
        select(Invite)
        .where(Invite.org_id == current_user.org_id)
        .order_by(Invite.created_at.desc())
    )
    return [_invite_out(invite) for invite in result.scalars().all()]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke a pending invite: its link stops working immediately."""
    result = await db.execute(
        select(Invite).where(Invite.id == invite_id, Invite.org_id == current_user.org_id)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise _not_found("Invite")
    if invite.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invite has already been accepted. Manage the member instead.",
        )

    if invite.revoked_at is None:
        invite.revoked_at = sessions.utcnow()
        await audit.record(
            db, "invite.revoke", org_id=current_user.org_id, user_id=current_user.id,
            request=request, details={"invite_id": invite.id, "email": invite.email},
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api-keys", response_model=APIKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: APIKeyCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Create an API key for programs to call this API with `X-API-Key`."""
    role = roles.normalize_role(payload.role)
    if roles.rank(role) > roles.rank(deps.effective_role(current_user)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An API key cannot have a higher role than its creator.",
        )

    plaintext, prefix, hashed = security.generate_api_key()
    expires_at = (
        sessions.utcnow() + datetime.timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    key = APIKey(
        name=payload.name,
        key_prefix=prefix,
        hashed_key=hashed,
        user_id=current_user.id,
        org_id=current_user.org_id,
        role=role,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(key)
    await db.flush()
    await audit.record(
        db, "api_key.create", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"api_key_id": key.id, "name": key.name, "prefix": prefix, "role": role},
    )
    await db.commit()
    await db.refresh(key)

    return {**APIKeyOut.model_validate(key).model_dump(), "key": plaintext}


@router.get("/api-keys", response_model=List[APIKeyOut])
async def list_api_keys(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Every API key in the organization, newest first. Prefixes only."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.org_id == current_user.org_id)
        .order_by(APIKey.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke a key. Requests using it fail from the very next one."""
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.org_id == current_user.org_id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise _not_found("API key")

    if key.revoked_at is None:
        key.is_active = False
        key.revoked_at = sessions.utcnow()
        await audit.record(
            db, "api_key.revoke", org_id=current_user.org_id, user_id=current_user.id,
            request=request,
            details={"api_key_id": key.id, "name": key.name, "prefix": key.key_prefix},
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit-logs", response_model=AuditLogPage)
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(
        None,
        max_length=255,
        description='Exact action ("document.upload"), or a prefix ending in "." ("member.").',
    ),
    user_id: Optional[uuid.UUID] = Query(None, description="Only events by this member."),
    since: Optional[datetime.datetime] = Query(None, description="Only events at or after this time."),
    until: Optional[datetime.datetime] = Query(None, description="Only events before this time."),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """The organization's audit trail, newest first, one page at a time."""
    filters = [AuditLog.org_id == current_user.org_id]
    if action:
        if action.endswith("."):
            filters.append(AuditLog.action.startswith(action, autoescape=True))
        else:
            filters.append(AuditLog.action == action)
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if since:
        filters.append(AuditLog.created_at >= since)
    if until:
        filters.append(AuditLog.created_at < until)

    total = (
        await db.execute(select(func.count()).select_from(AuditLog).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [AuditLogOut.model_validate(row) for row in result.scalars().all()]
    return AuditLogPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/usage", response_model=UsageSummary)
async def get_usage(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """This calendar month's AI usage for the organization: chat answers,"""
    return await usage.usage_summary(db, current_user.org_id)


@router.get("/feedback", response_model=FeedbackSummary)
async def get_feedback_summary(
    days: int = Query(30, ge=1, le=365, description="Look back this many days."),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """How people rated the organization's answers over the last `days` days:"""
    since = sessions.utcnow() - datetime.timedelta(days=days)
    return await feedback.summarise(db, current_user.org_id, since=since)
