"""
search.py
=========
WHAT THIS FILE DOES
-------------------
Exposes semantic search over the web, as `POST /api/v1/search`.

This is retrieval WITHOUT the AI answer. You send a question, you get back the
document passages that most closely match its meaning, ranked by how close.

WHY HAVE THIS SEPARATELY FROM CHAT?
-----------------------------------
Two solid reasons:

  1. Sometimes you just want to FIND the passage, not read a summary of it.
     It is faster (no LLM call at all) and cheaper.

  2. It is the single best debugging tool in the project. If a chat answer
     looks wrong, the cause is either bad retrieval or bad generation. Calling
     this endpoint with the same question shows you exactly what the AI was
     given, which tells you immediately which half is at fault.

HOW A FASTAPI ENDPOINT IS PUT TOGETHER
--------------------------------------
    @router.post("/")            <- HTTP method and URL path
    async def semantic_search(   <- `async` so the server can serve others
                                    while this one waits on the database
        payload: SearchRequest,  <- FastAPI parses+validates the JSON body
        db = Depends(get_db),    <- "dependency injection": FastAPI supplies
        user = Depends(...),        a database session and the logged-in user
    )

`Depends(...)` is the important idea. Rather than every endpoint opening its
own database connection and decoding the auth token itself, it declares WHAT it
needs and FastAPI provides it. The authentication dependency also rejects the
request outright if the token is missing or invalid, so by the time the body of
the function runs, `current_user` is guaranteed to be a real, active user.
"""

import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.user import User
from app.schemas.rag import SearchRequest, SearchResponse, SearchResult
from app.services.retrieval import RetrievalService

# An APIRouter groups related endpoints. It is registered onto the main app in
# main.py with a URL prefix, which is why no path here repeats "/api/v1/search".
router = APIRouter()


@router.post(
    "/",
    response_model=SearchResponse,
    # At most RATE_LIMIT_SEARCH searches per user: each one embeds the query,
    # which costs CPU or an embedding API call (core/rate_limit.py).
    dependencies=[Depends(deps.rate_limit_by_user("search"))],
)
async def semantic_search(
    payload: SearchRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Search across documents by MEANING rather than by keyword.

    Unlike a keyword search, asking about "time off" will surface a paragraph
    headed "Annual Leave Entitlement" even though the two phrases share no
    words, because both map to nearby points in embedding space.

    Results are automatically limited to the caller's own organization.
    """
    # Start a stopwatch so we can report how long the search took. Users judge
    # a search box largely on speed, so it is worth measuring and displaying.
    started_at = time.perf_counter()

    # Hand off to the retrieval service. Note what is passed for `org_id`:
    # `current_user.org_id`, taken from the verified JWT -- NOT anything the
    # client sent. A client can ask for any project_id it likes, but it can
    # never widen the search beyond its own organization.
    matches = await RetrievalService.search(
        db=db,
        query=payload.query,
        org_id=current_user.org_id,
        project_id=payload.project_id,
        document_id=payload.document_id,
        top_k=payload.top_k,
        min_score=payload.min_score,
    )

    # Convert the plain dictionaries into validated response objects. If the
    # retrieval layer ever returned a malformed row, this is where it would be
    # caught rather than being silently sent to the browser.
    results = [SearchResult(**match) for match in matches]

    return SearchResponse(
        query=payload.query,
        results=results,
        count=len(results),
        took_seconds=round(time.perf_counter() - started_at, 4),
    )
