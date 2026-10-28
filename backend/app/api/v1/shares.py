"""Public, read-only SHARE LINKS for an answer, a conversation or a project."""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import roles
from app.models.chat import ChatMessage, ChatSession
from app.models.project import Project
from app.models.share import ShareLink
from app.models.user import User
from app.schemas.collab import (
    PublicShare,
    ShareKind,
    ShareLinkCreate,
    ShareLinkCreated,
    ShareLinkOut,
)
from app.services import audit, sharing

router = APIRouter()
public_router = APIRouter()

MAX_ACTIVE_LINKS_PER_TARGET = 25


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


def _out(link: ShareLink) -> ShareLinkOut:
    return ShareLinkOut.model_validate(link).model_copy(
        update={"target_id": link.target_id, "status": sharing.status_of(link)}
    )


async def _owned_session(db: AsyncSession, session_id: uuid.UUID, user: User) -> ChatSession:
    """The caller's own conversation, inside their org -- or 404."""
    chat_session = (
        await db.execute(
            select(ChatSession)
            .join(Project, Project.id == ChatSession.project_id)
            .where(ChatSession.id == session_id, Project.org_id == user.org_id)
        )
    ).scalar_one_or_none()
    if chat_session is None or chat_session.user_id != user.id:
        raise _not_found("Conversation")
    return chat_session


async def _check_target(db: AsyncSession, kind: str, target_id: uuid.UUID, user: User) -> None:
    """Raise unless `user` may share (and so manage links to) this target."""
    if kind == "message":
        message = await db.get(ChatMessage, target_id)
        if message is None or message.role != "assistant":
            raise _not_found("Answer")
        await _owned_session(db, message.session_id, user)
    elif kind == "session":
        await _owned_session(db, target_id, user)
    else:
        project = await db.get(Project, target_id)
        if project is None or project.org_id != user.org_id:
            raise _not_found("Project")
        if not roles.at_least(deps.effective_role(user), roles.EDITOR):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sharing a project requires the Manager role or higher.",
            )


def _target_column(kind: str):
    return {"message": ShareLink.message_id, "session": ShareLink.session_id,
            "project": ShareLink.project_id}[kind]


@router.post("", response_model=ShareLinkCreated, status_code=status.HTTP_201_CREATED)
async def create_share_link(
    payload: ShareLinkCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Make a public read-only link. The token is returned in THIS response"""
    await _check_target(db, payload.kind, payload.target_id, current_user)

    active = [
        link
        for link in (
            await db.execute(
                select(ShareLink).where(
                    ShareLink.org_id == current_user.org_id,
                    _target_column(payload.kind) == payload.target_id,
                    ShareLink.revoked_at.is_(None),
                )
            )
        ).scalars()
        if sharing.status_of(link) == "active"
    ]
    if len(active) >= MAX_ACTIVE_LINKS_PER_TARGET:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This already has the maximum number of active links. Revoke one first.",
        )

    token = sharing.new_token()
    expires_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    link = ShareLink(
        org_id=current_user.org_id,
        created_by=current_user.id,
        kind=payload.kind,
        token_hash=sharing.token_hash(token),
        token_prefix=token[: sharing.DISPLAY_PREFIX_LENGTH],
        expires_at=expires_at,
        view_count=0,
        **{f"{payload.kind}_id": payload.target_id},
    )
    db.add(link)
    await db.flush()
    await audit.record(
        db, "share.create", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={"share_id": link.id, "kind": payload.kind, "target_id": payload.target_id,
                 "expires_at": expires_at.isoformat() if expires_at else None},
    )
    await db.commit()
    await db.refresh(link)
    return ShareLinkCreated(
        **_out(link).model_dump(), token=token, path=f"/share/{token}"
    )


@router.get("", response_model=List[ShareLinkOut])
async def list_share_links(
    kind: Optional[ShareKind] = Query(None, description="Only links of this kind."),
    target_id: Optional[uuid.UUID] = Query(None, description="Only links to this target (needs kind)."),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """The links the caller may manage, newest first."""
    statement = (
        select(ShareLink)
        .where(ShareLink.org_id == current_user.org_id)
        .order_by(ShareLink.created_at.desc())
        .limit(200)
    )
    if target_id is not None:
        if kind is None:
            raise HTTPException(status_code=422, detail="target_id needs kind as well.")
        await _check_target(db, kind, target_id, current_user)
        statement = statement.where(_target_column(kind) == target_id)
    else:
        if kind is not None:
            statement = statement.where(ShareLink.kind == kind)
        if not roles.at_least(deps.effective_role(current_user), roles.ADMIN):
            statement = statement.where(ShareLink.created_by == current_user.id)
    return [_out(link) for link in (await db.execute(statement)).scalars()]


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(
    share_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Revoke a link. It stops working at once; the row stays for the record."""
    link = await db.get(ShareLink, share_id)
    if link is None or link.org_id != current_user.org_id:
        raise _not_found("Share link")
    role = deps.effective_role(current_user)
    allowed = (
        link.created_by == current_user.id
        or roles.at_least(role, roles.ADMIN)
        or (link.kind == "project" and roles.at_least(role, roles.EDITOR))
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the person who made this link, or an admin, can revoke it.")
    if link.revoked_at is None:
        link.revoked_at = datetime.datetime.now(datetime.timezone.utc)
        await audit.record(
            db, "share.revoke", org_id=current_user.org_id, user_id=current_user.id,
            request=request, details={"share_id": link.id, "kind": link.kind,
                                      "target_id": link.target_id},
        )
        await db.commit()
    return None


@public_router.get(
    "/shares/{token}",
    response_model=PublicShare,
    dependencies=[Depends(deps.rate_limit_by_ip("share_view"))],
)
async def view_shared(
    token: str,
    response: Response,
    db: AsyncSession = Depends(deps.get_db),
):
    """Read a shared answer, conversation or project. No login needed."""
    link = await sharing.resolve(db, token)
    view = await sharing.public_view(db, link) if link is not None else None
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link does not exist, has expired or was revoked.",
        )
    await sharing.record_view(db, link)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    return view
