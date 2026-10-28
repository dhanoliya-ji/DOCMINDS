"""This is where the whole RAG pipeline finally comes together."""
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import observability
from app.db.session import SessionLocal
from app.models import ChatMessage, ChatSession, Feedback
from app.models import Project
from app.models import User
from app.schemas import (
    AgentStep,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionUpdate,
    Citation,
    FeedbackCreate,
)
from app.services import confidence as confidence_service
from app.services import export, usage
from app.services.agent import run_agent
from app.services.llm import LLMService
from app.services.query_rewrite import RewriteResult, rewrite_query
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_TITLE = "New Chat"
TITLE_LENGTH = 60


async def _get_owned_session(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> ChatSession:
    """Fetch a chat session, but ONLY if the requesting user owns it."""
    statement = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(statement)
    chat_session = result.scalar_one_or_none()

    if not chat_session or chat_session.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found.",
        )

    return chat_session


def make_title(question: str, limit: int = TITLE_LENGTH) -> str:
    """The automatic title for a conversation: its first question, tidied."""
    text = re.sub(r"\s+", " ", question or "").strip()
    if not text:
        return DEFAULT_TITLE
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "..."


def _citations_from(chunks: List[Dict[str, Any]], *, numbered: bool) -> List[Citation]:
    """Map every excerpt the model saw back to its real source."""
    return [
        Citation(
            number=chunk["number"] if numbered else index,
            chunk_id=chunk["chunk_id"],
            document_id=chunk["document_id"],
            filename=chunk["filename"],
            page_number=chunk.get("page_number"),
            score=chunk["score"],
            snippet=chunk["content"][:300],
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def _words(text: str) -> List[str]:
    """Split text into word-sized pieces that join back to exactly `text`."""
    return re.findall(r"\S+\s*|\s+", text or "")


def sse(event: str, data: Any) -> str:
    """Format one Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/sessions", response_model=ChatSessionOut,
             status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Start a new conversation scoped to one project."""
    statement = select(Project).where(Project.id == payload.project_id)
    result = await db.execute(statement)
    project = result.scalar_one_or_none()

    if not project or project.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )

    chat_session = ChatSession(
        title=payload.title or DEFAULT_TITLE,
        user_id=current_user.id,
        project_id=payload.project_id,
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)

    return chat_session


@router.get("/sessions", response_model=List[ChatSessionOut])
async def list_chat_sessions(
    project_id: Optional[uuid.UUID] = Query(
        None, description="Only conversations about this project (the sidebar's view)."
    ),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List the current user's conversations, most recently used first."""
    statement = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    )
    if project_id is not None:
        statement = statement.where(ChatSession.project_id == project_id)
    result = await db.execute(statement)
    return result.scalars().all()


@router.patch("/sessions/{session_id}", response_model=ChatSessionOut)
async def rename_chat_session(
    session_id: uuid.UUID,
    payload: ChatSessionUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Rename a conversation."""
    chat_session = await _get_owned_session(db, session_id, current_user)
    chat_session.title = re.sub(r"\s+", " ", payload.title).strip() or DEFAULT_TITLE
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


async def _transcript(db: AsyncSession, session_id: uuid.UUID) -> List[ChatMessage]:
    """Every message of a session, oldest first."""
    statement = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.role.desc())
    )
    result = await db.execute(statement)
    return list(result.scalars().all())


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageOut])
async def list_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Read the full transcript of one conversation, oldest message first."""
    await _get_owned_session(db, session_id, current_user)
    return await _transcript(db, session_id)


@router.get("/sessions/{session_id}/export")
async def export_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Download a conversation as a Markdown file, citations included."""
    chat_session = await _get_owned_session(db, session_id, current_user)
    messages = [
        ChatMessageOut.model_validate(message).model_dump(mode="json")
        for message in await _transcript(db, session_id)
    ]
    markdown = export.conversation_to_markdown(chat_session.title, messages)
    filename = export.safe_filename(chat_session.title)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Delete a conversation and every message inside it."""
    chat_session = await _get_owned_session(db, session_id, current_user)
    await db.delete(chat_session)
    await db.commit()
    return None


Event = Tuple[str, Any]


async def _agent_events(*, stream: bool, **agent_kwargs: Any) -> AsyncIterator[Event]:
    """Run the agent, yielding events as they happen and, last, ("result", the"""
    if not stream:
        yield "result", await run_agent(**agent_kwargs)
        return

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        run_agent(
            on_step=lambda step: queue.put_nowait(("step", step)),
            on_token=lambda text: queue.put_nowait(("token", text)),
            on_reset=lambda: queue.put_nowait(("reset", None)),
            on_citations=lambda chunks: queue.put_nowait(("citations", chunks)),
            **agent_kwargs,
        )
    )
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            break
        while not queue.empty():
            yield queue.get_nowait()
        yield "result", task.result()
    finally:
        if not task.done():
            task.cancel()


async def _answer_events(
    db: AsyncSession,
    *,
    chat_session: ChatSession,
    payload: ChatRequest,
    current_user: User,
    request_id: Optional[str],
    stream: bool,
) -> AsyncIterator[Event]:
    """The whole question-to-saved-answer pipeline, as a sequence of events."""
    session_id = chat_session.id
    started_at = time.perf_counter()

    history = await RetrievalService.build_history(db, session_id)

    user_message = ChatMessage(
        session_id=session_id,
        role="user",
        content=payload.message,
        citations=[],
        steps=[],
    )
    db.add(user_message)
    await db.flush()  # assigns user_message.id without committing yet

    if chat_session.title == DEFAULT_TITLE:
        chat_session.title = make_title(payload.message)

    yield "start", {
        "session_id": session_id,
        "user_message_id": user_message.id,
        "mode": payload.mode,
        "title": chat_session.title,
    }

    document_ids = list(payload.document_ids or []) or None
    rewrite = RewriteResult(query=payload.message)

    if payload.mode == "agent":
        agent_result: Dict[str, Any] = {}
        streamed: List[str] = []
        async for kind, value in _agent_events(
            stream=stream,
            db=db,
            question=payload.message,
            org_id=current_user.org_id,
            project_id=chat_session.project_id,
            top_k=payload.top_k,
            history=history,
            document_ids=document_ids,
        ):
            if kind == "step":
                yield "step", value
            elif kind == "citations":
                live = _citations_from(value, numbered=True)
                yield "citations", {"citations": [c.model_dump(mode="json") for c in live]}
            elif kind == "token":
                streamed.append(value)
                yield "token", {"text": value}
            elif kind == "reset":
                streamed.clear()
                yield "reset", {}
            else:
                agent_result = value
        llm_result = agent_result
        citations = _citations_from(agent_result["citations_chunks"], numbered=True)
        steps = [AgentStep(**step) for step in agent_result["steps"]]
        yield "citations", {"citations": [c.model_dump(mode="json") for c in citations]}
        if "".join(streamed).strip() != llm_result["answer"].strip():
            if streamed:
                yield "reset", {}
            for piece in _words(llm_result["answer"]):
                yield "token", {"text": piece}
    else:
        rewrite = await rewrite_query(payload.message, history)
        yield "query", {"search_query": rewrite.query, "rewritten": rewrite.rewritten}

        chunks = await RetrievalService.search(
            db=db,
            query=rewrite.query,
            org_id=current_user.org_id,
            project_id=chat_session.project_id,
            top_k=payload.top_k,
            document_ids=document_ids,
        )
        citations = _citations_from(chunks, numbered=False)
        yield "citations", {"citations": [c.model_dump(mode="json") for c in citations]}

        if stream:
            stats: Dict[str, Any] = {}
            pieces: List[str] = []
            async for piece in LLMService.stream_answer(
                payload.message, chunks, history, stats=stats
            ):
                pieces.append(piece)
                yield "token", {"text": piece}
            llm_result = {
                "answer": "".join(pieces).strip(),
                "latency": round(time.perf_counter() - started_at, 3),
                **stats,
            }
        else:
            llm_result = await LLMService.generate_answer(
                question=payload.message,
                chunks=chunks,
                history=history,
            )
        steps = []

    extra_prompt = rewrite.prompt_tokens
    extra_completion = rewrite.completion_tokens
    total_tokens = int(llm_result.get("tokens_used") or 0) + extra_prompt + extra_completion
    model = llm_result.get("model") or "unknown"

    citation_dicts = [citation.model_dump(mode="json") for citation in citations]
    verdict = confidence_service.assess(llm_result["answer"], citation_dicts, model=model)

    assistant_message = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=llm_result["answer"],
        tokens_used=total_tokens,
        latency=llm_result.get("latency"),
        citations=citation_dicts,
        confidence=verdict,
        mode=payload.mode,
        model=model,
        steps=[step.model_dump(mode="json") for step in steps],
    )
    db.add(assistant_message)

    usage.record_chat_usage(
        db,
        org_id=current_user.org_id,
        user_id=current_user.id,
        mode=payload.mode,
        model=model,
        prompt_tokens=int(llm_result.get("prompt_tokens") or 0) + extra_prompt,
        completion_tokens=int(llm_result.get("completion_tokens") or 0) + extra_completion,
        total_tokens=total_tokens,
        request_id=request_id,
    )

    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)

    yield "done", ChatResponse(
        session_id=session_id,
        user_message=user_message,
        assistant_message=assistant_message,
        citations=citations,
        model=model,
        mode=payload.mode,
        steps=steps,
        search_query=rewrite.query if payload.mode == "rag" else None,
        confidence=verdict,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatResponse,
    dependencies=[Depends(deps.rate_limit_by_user("chat"))],
)
async def ask_question(
    session_id: uuid.UUID,
    payload: ChatRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Ask a question and get an AI answer grounded in the project's documents."""
    chat_session = await _get_owned_session(db, session_id, current_user)

    await usage.check_token_quota(db, current_user.org_id)

    response: Optional[ChatResponse] = None
    async for kind, value in _answer_events(
        db,
        chat_session=chat_session,
        payload=payload,
        current_user=current_user,
        request_id=observability.current_request_id(),
        stream=False,
    ):
        if kind == "done":
            response = value
    return response


@router.post(
    "/sessions/{session_id}/messages/stream",
    dependencies=[Depends(deps.rate_limit_by_user("chat"))],
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-Sent Events -- see the chat.py module docstring.",
        }
    },
)
async def ask_question_stream(
    session_id: uuid.UUID,
    payload: ChatRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Ask a question and receive the answer as a stream of Server-Sent Events."""
    await _get_owned_session(db, session_id, current_user)
    await usage.check_token_quota(db, current_user.org_id)

    user_id = current_user.id
    request_id = observability.current_request_id()

    async def event_stream() -> AsyncIterator[str]:
        async with SessionLocal() as stream_db:
            try:
                user = await stream_db.get(User, user_id)
                chat_session = await stream_db.get(ChatSession, session_id)
                if user is None or chat_session is None:
                    yield sse("error", {"detail": "Chat session not found."})
                    return
                async for kind, value in _answer_events(
                    stream_db,
                    chat_session=chat_session,
                    payload=payload,
                    current_user=user,
                    request_id=request_id,
                    stream=True,
                ):
                    if kind == "done":
                        yield sse("done", value.model_dump(mode="json"))
                    else:
                        yield sse(kind, value)
            except Exception as exc:
                await stream_db.rollback()
                logger.error(
                    "Streaming answer failed",
                    extra={"error": f"{type(exc).__name__}: {exc}"},
                    exc_info=exc,
                )
                yield sse("error", {"detail": "Could not get an answer. Please try again."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/messages/{message_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    message_id: uuid.UUID,
    payload: FeedbackCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Record a thumbs up or thumbs down on an assistant answer."""
    statement = select(ChatMessage).where(ChatMessage.id == message_id)
    result = await db.execute(statement)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found.",
        )

    await _get_owned_session(db, message.session_id, current_user)

    feedback = Feedback(
        message_id=message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(feedback)
    await db.commit()

    return {"status": "recorded", "rating": payload.rating}
