"""Exposes semantic search over the web, as `POST /api/v1/search`."""
import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models import User
from app.schemas import SearchRequest, SearchResponse, SearchResult
from app.services.retrieval import RetrievalService

router = APIRouter()


@router.post(
    "/",
    response_model=SearchResponse,
    dependencies=[Depends(deps.rate_limit_by_user("search"))],
)
async def semantic_search(
    payload: SearchRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Search across documents by MEANING -- and, with hybrid search on, by"""
    started_at = time.perf_counter()

    matches = await RetrievalService.search(
        db=db,
        query=payload.query,
        org_id=current_user.org_id,
        project_id=payload.project_id,
        document_id=payload.document_id,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
        min_score=payload.min_score,
    )

    results = [SearchResult(**match) for match in matches]

    return SearchResponse(
        query=payload.query,
        results=results,
        count=len(results),
        took_seconds=round(time.perf_counter() - started_at, 4),
    )
