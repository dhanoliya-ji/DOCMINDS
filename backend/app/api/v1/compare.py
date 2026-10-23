"""
compare.py  (endpoint)
======================
WHAT THIS FILE DOES
-------------------
    POST /compare   {"document_a", "document_b", "focus"?}
                    -> a structured diff: summary, differences (added /
                       removed / changed), each citing excerpts on both sides

The work is in services/compare.py. This file does what an endpoint must:

    * both documents must be in the caller's organization (404 otherwise,
      the same answer as for an id that does not exist), finished processing
      (409 otherwise) and different from each other (422);
    * the organization's monthly token quota is checked first, and the
      model's tokens are metered afterwards (mode "compare"), exactly like a
      chat answer;
    * per-user rate limit "compare" (RATE_LIMIT_COMPARE): one comparison is
      one large model call.

Viewer and above: comparing changes nothing, like asking a question.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import observability
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.collab import ComparedDocument, ComparisonOut, ComparisonRequest
from app.services import usage
from app.services.compare import compare_documents

router = APIRouter()


async def _readable(db: AsyncSession, document_id: uuid.UUID, user: User) -> Document:
    document = (
        await db.execute(
            select(Document)
            .join(Project, Project.id == Document.project_id)
            .where(Document.id == document_id, Project.org_id == user.org_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{document.filename} is still '{document.status}' and cannot be compared yet.",
        )
    return document


@router.post(
    "",
    response_model=ComparisonOut,
    dependencies=[Depends(deps.rate_limit_by_user("compare"))],
)
async def compare(
    payload: ComparisonRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_viewer),
):
    """Compare two documents and list what was added, removed or changed."""
    if payload.document_a == payload.document_b:
        raise HTTPException(status_code=422, detail="Pick two different documents to compare.")
    document_a = await _readable(db, payload.document_a, current_user)
    document_b = await _readable(db, payload.document_b, current_user)
    await usage.check_token_quota(db, current_user.org_id)

    result = await compare_documents(
        db, document_a, document_b, org_id=current_user.org_id, focus=payload.focus
    )

    if result["model"] not in ("mock", "error") or result["tokens_used"]:
        usage.record_chat_usage(
            db, org_id=current_user.org_id, user_id=current_user.id, mode="compare",
            model=result["model"], prompt_tokens=result["prompt_tokens"],
            completion_tokens=result["completion_tokens"], total_tokens=result["tokens_used"],
            request_id=observability.current_request_id(),
        )
        await db.commit()

    return ComparisonOut(
        document_a=ComparedDocument(id=document_a.id, filename=document_a.filename),
        document_b=ComparedDocument(id=document_b.id, filename=document_b.filename),
        summary=result["summary"],
        differences=result["differences"],
        citations=result["citations"],
        alignment=result["alignment"],
        pairs=result["pairs"],
        identical=result["identical"],
        model=result["model"],
        tokens_used=result["tokens_used"],
        latency=result["latency"],
    )
