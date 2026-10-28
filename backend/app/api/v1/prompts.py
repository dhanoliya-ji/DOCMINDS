"""The PROMPT LIBRARY: reusable prompts a user can drop into the chat box."""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import roles
from app.models import PromptTemplate
from app.models import User
from app.schemas import PromptTemplateCreate, PromptTemplateOut, PromptTemplateUpdate
from app.services.prompt_library import BUILTIN_TEMPLATES, builtin_id

router = APIRouter()

MAX_TEMPLATES_PER_USER = 200


def _is_editor(user: User) -> bool:
    return roles.at_least(deps.effective_role(user), roles.EDITOR)


def _may_edit(template: PromptTemplate, user: User) -> bool:
    if template.scope == "personal":
        return template.user_id == user.id
    return _is_editor(user)


def _out(template: PromptTemplate, user: User) -> PromptTemplateOut:
    return PromptTemplateOut(
        id=str(template.id), title=template.title, body=template.body, scope=template.scope,
        editable=_may_edit(template, user), owner_id=template.user_id,
        created_at=template.created_at, updated_at=template.updated_at,
    )


def _forbid_org_scope() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Sharing a prompt with the whole organization requires the Manager role or higher.",
    )


async def _visible(db: AsyncSession, template_id: uuid.UUID, user: User) -> PromptTemplate:
    template = await db.get(PromptTemplate, template_id)
    if (
        template is None
        or template.org_id != user.org_id
        or (template.scope == "personal" and template.user_id != user.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found.")
    return template


@router.get("", response_model=List[PromptTemplateOut])
async def list_prompts(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Built-ins first, then mine, then the organization's."""
    builtins = [
        PromptTemplateOut(id=builtin_id(t["key"]), key=t["key"], title=t["title"],
                          body=t["body"], scope="builtin", editable=False)
        for t in BUILTIN_TEMPLATES
    ]
    rows = (
        await db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.org_id == current_user.org_id,
                or_(
                    PromptTemplate.scope == "org",
                    and_(PromptTemplate.scope == "personal", PromptTemplate.user_id == current_user.id),
                ),
            )
            .order_by(PromptTemplate.title)
        )
    ).scalars().all()
    mine = [_out(t, current_user) for t in rows if t.scope == "personal"]
    shared = [_out(t, current_user) for t in rows if t.scope == "org"]
    return builtins + mine + shared


@router.post("", response_model=PromptTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_prompt(
    payload: PromptTemplateCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Save a prompt to my library, or (editor+) to the organization's."""
    if payload.scope == "org" and not _is_editor(current_user):
        raise _forbid_org_scope()
    count = len(
        (await db.execute(select(PromptTemplate.id).where(PromptTemplate.user_id == current_user.id))).all()
    )
    if count >= MAX_TEMPLATES_PER_USER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Your prompt library is full. Delete a prompt first.")
    template = PromptTemplate(
        org_id=current_user.org_id, user_id=current_user.id, scope=payload.scope,
        title=payload.title, body=payload.body,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _out(template, current_user)


@router.patch("/{template_id}", response_model=PromptTemplateOut)
async def update_prompt(
    template_id: uuid.UUID,
    payload: PromptTemplateUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    template = await _visible(db, template_id, current_user)
    if not _may_edit(template, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only a Manager or Admin can change the organization's prompts.")
    if payload.scope is not None and payload.scope != template.scope:
        if payload.scope == "org" and not _is_editor(current_user):
            raise _forbid_org_scope()
        if payload.scope == "personal":
            template.user_id = current_user.id
        template.scope = payload.scope
    if payload.title is not None:
        template.title = payload.title
    if payload.body is not None:
        template.body = payload.body
    await db.commit()
    await db.refresh(template)
    return _out(template, current_user)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    template = await _visible(db, template_id, current_user)
    if not _may_edit(template, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only a Manager or Admin can delete the organization's prompts.")
    await db.delete(template)
    await db.commit()
    return None
