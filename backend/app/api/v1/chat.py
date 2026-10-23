"""
chat.py
=======
WHAT THIS FILE DOES
-------------------
This is where the whole RAG pipeline finally comes together. Everything else in
the project -- extraction, OCR, chunking, embedding, retrieval, the LLM client
-- exists so that this file can answer a question about your documents.

THE FULL JOURNEY OF ONE QUESTION
--------------------------------
When a user types "How many leave days do I get?" and presses enter:

   1. The request arrives here with a JWT proving who they are, and passes
      the per-user rate limit (core/rate_limit.py).
   2. We verify the chat session belongs to them          (security)
      and that the organization is within its monthly token quota, then
      load the conversation so far (BEFORE saving the new question, so it
      is not sent to the model twice).
   3. We save their question as a ChatMessage             (role="user")
   4. REWRITE  : a follow-up ("and part-timers?") is turned into a standalone
                 SEARCH query using that history (services/query_rewrite.py).
   5. RETRIEVE : retrieval.py finds the closest chunks -- by meaning AND by
                 exact words (hybrid search) -- restricted to their
                 organization, their project and, if they ticked some, to
                 the selected documents.
   6. AUGMENT  : llm.py pastes those chunks into a prompt, numbered [1]..[5].
   7. GENERATE : GPT-OSS 120B on Groq writes an answer using only those excerpts.
   8. We build citations linking each [n] back to its file and page.
   9. We save the answer as a ChatMessage                 (role="assistant")
      along with the citations, the mode, the model, the agent's steps, the
      token count and how long it took -- and, in the same transaction, a
      usage event (services/usage.py).
  10. We return both messages so the UI can render the exchange.

TWO MODES (the request's `mode` field)
--------------------------------------
The journey above is mode="rag", the default: a FIXED pipeline, one search and
one model call. Fast, cheap and identical for every question.

mode="agent" swaps steps 4-7 for the Pydantic AI agent in services/agent.py:

   4'. The model is given TOOLS (search_documents, list_documents,
       read_document_section) instead of a fixed set of excerpts.
   5'. It decides which tools to call, possibly several times -- e.g. one
       search per half of a comparison question -- capped at AGENT_MAX_STEPS
       model calls.
   6'. Every chunk any tool surfaced gets one GLOBAL citation number, so the
       model's "[n]" lines up with the citations list we return.

TWO WAYS TO RECEIVE THE ANSWER
------------------------------
    POST .../messages          one JSON reply once everything is finished.
                               Simple; what scripts and API clients use.
    POST .../messages/stream   Server-Sent Events: the answer arrives word by
                               word as the model writes it, and in agent mode
                               every research step appears the moment the
                               agent takes it. What the web UI uses.

Both run THE SAME pipeline -- `_answer_events` below, an async generator of
events. The streaming endpoint forwards every event to the browser; the JSON
endpoint simply runs it to the end and returns the final one. So the two can
never disagree about how a question is answered, saved or metered.

THE STREAM, EVENT BY EVENT
--------------------------
Each event is a few lines of text, `event: <name>` then `data: <json>`, ended
by a blank line (the SSE format, readable by any HTTP client):

    start      {"session_id", "user_message_id",       the question is saved
                "mode", "title"}                       (not yet committed)
    query      {"search_query", "rewritten"}           rag: what was searched
    step       {"tool", "args", "summary"}             agent: one tool call
    citations  {"citations": [...]}                    the sources, BEFORE the
                                                       text that cites them
                                                       (agent: may repeat as
                                                       the research grows)
    token      {"text": "..."}                         the next piece of answer
    reset      {}                                      agent: discard the text
                                                       streamed so far; it was
                                                       not the final answer
    done       the full ChatResponse, exactly as the JSON endpoint returns it,
               including the answer's confidence verdict
    error      {"detail": "..."}                       something failed;
                                                       nothing was saved

The answer is saved to the database at the END, in one transaction with the
question and the usage event -- exactly as the JSON endpoint does. If the
browser disconnects half-way, nothing is saved: a half-written answer would be
worse than none.

ENDPOINTS DEFINED HERE
----------------------
    POST   /chat/sessions                      start a conversation
    GET    /chat/sessions[?project_id=]        list my conversations
    PATCH  /chat/sessions/{id}                 rename one
    DELETE /chat/sessions/{id}                 delete one
    GET    /chat/sessions/{id}/messages        read one conversation
    GET    /chat/sessions/{id}/export          download it as Markdown
    POST   /chat/sessions/{id}/messages        ask a question  <- the main one
    POST   /chat/sessions/{id}/messages/stream ask, streamed as SSE
    POST   /chat/messages/{id}/feedback        rate an answer
"""

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
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.project import Project
from app.models.user import User
from app.schemas.rag import (
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

# The placeholder title of a conversation nobody has asked anything in yet.
DEFAULT_TITLE = "New Chat"
TITLE_LENGTH = 60


# ======================================================================
# SHARED HELPERS
# ======================================================================


async def _get_owned_session(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> ChatSession:
    """
    Fetch a chat session, but ONLY if the requesting user owns it.

    This exists so the ownership check is written once and reused by every
    endpoint below. Security checks that are copy-pasted into five places are
    security checks that eventually get forgotten in a sixth.

    Note we return 404 (not found) rather than 403 (forbidden) when the session
    belongs to someone else. Replying "forbidden" would confirm that a session
    with that id exists, which leaks information; 404 reveals nothing.
    """
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
    """
    The automatic title for a conversation: its first question, tidied.

        "  How many   days of leave\\ndo I get?"  ->  "How many days of leave do I get?"

    Whitespace is collapsed, so a question pasted with line breaks makes a
    one-line sidebar entry. A long question is cut at a WORD boundary (never
    mid-word) and given an ellipsis, so the reader can tell it was shortened.
    """
    text = re.sub(r"\s+", " ", question or "").strip()
    if not text:
        return DEFAULT_TITLE
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Back up to the last space, unless that would throw away most of it.
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "..."


def _citations_from(chunks: List[Dict[str, Any]], *, numbered: bool) -> List[Citation]:
    """
    Map every excerpt the model saw back to its real source.

    `numbered=False` (rag): numbers come from the list order, 1, 2, 3... --
    the same enumeration build_context_prompt used, so "[2]" in the answer is
    the second entry here.

    `numbered=True` (agent): each chunk already carries the GLOBAL number its
    tool assigned. The model saw "EXCERPT [7]" and wrote "[7]", so the
    citation must be numbered 7 too.
    """
    return [
        Citation(
            number=chunk["number"] if numbered else index,
            chunk_id=chunk["chunk_id"],
            document_id=chunk["document_id"],
            filename=chunk["filename"],
            page_number=chunk.get("page_number"),
            score=chunk["score"],
            # A short preview for the collapsed citation card in the UI.
            snippet=chunk["content"][:300],
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def _words(text: str) -> List[str]:
    """Split text into word-sized pieces that join back to exactly `text`."""
    return re.findall(r"\S+\s*|\s+", text or "")


def sse(event: str, data: Any) -> str:
    """
    Format one Server-Sent Event.

        event: token
        data: {"text": "Hello "}
        <blank line>

    `data` is JSON on ONE line -- json.dumps never emits a raw newline, which
    matters because a newline inside `data:` would end the field early.
    """
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ======================================================================
# SESSION MANAGEMENT
# ======================================================================


@router.post("/sessions", response_model=ChatSessionOut,
             status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Start a new conversation scoped to one project.

    Tying a session to a project is what keeps answers relevant: a question
    asked in the "HR Policies" project will never retrieve text from the
    "Engineering Runbooks" project.
    """
    # Confirm the project exists AND belongs to the caller's organization.
    # Without the second half of that check, a user could open a chat against
    # another company's project and read its documents through the answers.
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
    # `commit()` writes the row permanently to the database.
    await db.commit()
    # `refresh()` re-reads the row so that database-generated columns --
    # created_at and updated_at, filled in by Postgres itself -- are populated
    # on our Python object before we return it.
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
        # Order by updated_at rather than created_at so a long-running
        # conversation you replied to a minute ago rises back to the top.
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
    """
    Rename a conversation.

    A title the user chose is kept from then on: the automatic title is only
    ever applied while a session is still called "New Chat".
    """
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
        # Ascending, because a transcript reads top to bottom. A question and
        # its answer are saved in one transaction and can share a timestamp,
        # so within a tie the user's message ("user" > "assistant") goes first.
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
    """
    Read the full transcript of one conversation, oldest message first.

    This is what the history sidebar loads to RESUME a conversation: each
    assistant message carries its citations, mode, model and research steps,
    so it renders exactly as it did live.
    """
    await _get_owned_session(db, session_id, current_user)
    return await _transcript(db, session_id)


@router.get("/sessions/{session_id}/export")
async def export_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Download a conversation as a Markdown file, citations included.

    The web UI builds the same file in the browser (lib/export.ts); this
    endpoint is for API clients and scripts.
    """
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
    """
    Delete a conversation and every message inside it.

    We only delete the session row. The messages disappear automatically
    because the relationship is declared with `cascade="all, delete-orphan"`
    in models/chat.py, and the foreign key uses ON DELETE CASCADE. That means
    the database itself guarantees no orphaned messages can ever be left behind.
    """
    chat_session = await _get_owned_session(db, session_id, current_user)
    await db.delete(chat_session)
    await db.commit()
    # 204 No Content means "it worked, and there is deliberately no response
    # body", which is the conventional reply to a successful DELETE.
    return None


# ======================================================================
# THE MAIN EVENT: ANSWER A QUESTION (the pipeline both endpoints share)
# ======================================================================

# (event name, payload) -- see "THE STREAM, EVENT BY EVENT" above.
Event = Tuple[str, Any]


async def _agent_events(*, stream: bool, **agent_kwargs: Any) -> AsyncIterator[Event]:
    """
    Run the agent, yielding events as they happen and, last, ("result", the
    run_agent dictionary). While streaming, the events are:

        ("step", step)          a tool call
        ("citations", chunks)   the sources so far, just before answer text
        ("token", text)         the next piece of the FINAL answer
        ("reset", None)         the text so far was not the answer after all

    Not streaming: simply await it -- the steps come back inside the result.

    Streaming: run_agent goes on a background task and reports into a queue
    through its on_step / on_token / on_reset / on_citations hooks. The loop
    waits for "something arrived" OR "the agent finished", whichever comes
    first, so every step and every word is forwarded the moment it exists.
    """
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
        # Events recorded in the agent's very last moments.
        while not queue.empty():
            yield queue.get_nowait()
        yield "result", task.result()
    finally:
        # The browser went away mid-answer: stop the agent as well, rather
        # than letting it keep calling the model for nobody.
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
    """
    The whole question-to-saved-answer pipeline, as a sequence of events.

    Ends with ("done", ChatResponse) once the answer is committed. Read the
    numbered steps to follow the complete RAG flow end to end.
    """
    session_id = chat_session.id
    started_at = time.perf_counter()

    # ---- STEP 1: Load the conversation so far -------------------------
    # BEFORE the new question is saved, so the history holds only the EARLIER
    # turns. Loaded after, it would end with this very question -- which the
    # prompt then adds again, sending it to the model twice (wasted tokens,
    # and the model reads it as the user repeating themselves). Both answer
    # paths also drop such a duplicate defensively.
    history = await RetrievalService.build_history(db, session_id)

    # ---- STEP 2: Save the user's question ----------------------------
    # We store it BEFORE calling the AI on purpose. If the LLM call then fails
    # or times out, the user's question is not lost -- they can retry without
    # retyping, and the transcript stays accurate.
    user_message = ChatMessage(
        session_id=session_id,
        role="user",
        content=payload.message,
        citations=[],
        steps=[],
    )
    db.add(user_message)
    await db.flush()  # assigns user_message.id without committing yet

    # A brand-new session is titled "New Chat". Title it after the first
    # question so the sidebar shows something recognisable, the way ChatGPT
    # and similar tools do. A title the user chose is left alone.
    if chat_session.title == DEFAULT_TITLE:
        chat_session.title = make_title(payload.message)

    yield "start", {
        "session_id": session_id,
        "user_message_id": user_message.id,
        "mode": payload.mode,
        "title": chat_session.title,
    }

    # An empty list means "no filter", the same as leaving it out.
    document_ids = list(payload.document_ids or []) or None
    rewrite = RewriteResult(query=payload.message)

    if payload.mode == "agent":
        # ---- AGENT MODE: steps 3-5 handled by the Pydantic AI agent ---
        agent_result: Dict[str, Any] = {}
        # The final answer's text as streamed so far (since the last reset).
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
        # Same dictionary keys as LLMService.generate_answer returns, so
        # STEP 6 below does not need to know which mode produced the answer.
        llm_result = agent_result
        citations = _citations_from(agent_result["citations_chunks"], numbered=True)
        steps = [AgentStep(**step) for step in agent_result["steps"]]
        # The complete list: the last tool may have run after the text began.
        yield "citations", {"citations": [c.model_dump(mode="json") for c in citations]}
        # The final answer normally streamed token by token above, straight
        # from the model. When it did not -- demo mode, a failed run, a
        # non-streaming caller -- or when what was streamed differs from the
        # saved output, (re)send the saved text word by word, so the screen
        # always ends up showing exactly what was saved.
        if "".join(streamed).strip() != llm_result["answer"].strip():
            if streamed:
                yield "reset", {}
            for piece in _words(llm_result["answer"]):
                yield "token", {"text": piece}
    else:
        # ---- STEP 3: REWRITE a follow-up into a standalone search -------
        # "and for part-timers?" searched as-is finds the wrong passages.
        # Only the SEARCH uses the rewrite; the model sees the real words.
        rewrite = await rewrite_query(payload.message, history)
        yield "query", {"search_query": rewrite.query, "rewritten": rewrite.rewritten}

        # ---- STEP 4: RETRIEVE --------------------------------------------
        # Find the passages in this project's documents -- or only in the
        # selected ones -- closest to the question. The "R" of RAG.
        chunks = await RetrievalService.search(
            db=db,
            query=rewrite.query,
            org_id=current_user.org_id,
            project_id=chat_session.project_id,
            top_k=payload.top_k,
            document_ids=document_ids,
        )
        # Built from the same list, in the same order, that
        # build_context_prompt enumerates, so the numbering matches the
        # model's "[n]" markers -- and sent BEFORE the text, so the sources
        # are on screen by the time the answer starts citing them.
        citations = _citations_from(chunks, numbered=False)
        yield "citations", {"citations": [c.model_dump(mode="json") for c in citations]}

        # ---- STEP 5: AUGMENT + GENERATE ----------------------------------
        # llm.py pastes the retrieved chunks into the prompt (augment) and asks
        # the model to answer using only those chunks (generate). Retried, and
        # falling back to LLM_FALLBACK_MODEL, on transient failures.
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

    # The rewrite's tokens are part of what this answer cost.
    extra_prompt = rewrite.prompt_tokens
    extra_completion = rewrite.completion_tokens
    total_tokens = int(llm_result.get("tokens_used") or 0) + extra_prompt + extra_completion
    model = llm_result.get("model") or "unknown"

    # ---- STEP 5b: How far can this answer be trusted? ---------------------
    # Measured, not asked of the model: the retrieval scores and how much of
    # the answer carries a valid citation (services/confidence.py). Saved with
    # the message so a reopened or shared answer keeps its verdict.
    citation_dicts = [citation.model_dump(mode="json") for citation in citations]
    verdict = confidence_service.assess(llm_result["answer"], citation_dicts, model=model)

    # ---- STEP 6: Save the answer -------------------------------------
    assistant_message = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=llm_result["answer"],
        tokens_used=total_tokens,
        latency=llm_result.get("latency"),
        # Citations are stored as JSONB, so they must be plain JSON-safe types.
        # `mode="json"` converts the UUID objects into strings for us.
        citations=citation_dicts,
        confidence=verdict,
        # Saved so a resumed conversation looks exactly like the live one.
        mode=payload.mode,
        model=model,
        steps=[step.model_dump(mode="json") for step in steps],
    )
    db.add(assistant_message)

    # ---- STEP 6b: Meter it ---------------------------------------------
    # One usage event per answer: tokens in and out, the model that actually
    # answered (the fallback's name if the main one failed) and its
    # estimated cost. Staged in the SAME transaction as the answer, so usage
    # is never recorded for an answer that was not saved, or vice versa.
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

    # One commit saves the question, the answer, its usage and the new title
    # together. Because it is a single transaction, we can never end up with
    # a question stored but its answer missing.
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)

    # ---- STEP 7: Reply -----------------------------------------------
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
    # At most RATE_LIMIT_CHAT questions per user (core/rate_limit.py). Every
    # question costs model tokens, so this is the most expensive endpoint.
    dependencies=[Depends(deps.rate_limit_by_user("chat"))],
)
async def ask_question(
    session_id: uuid.UUID,
    payload: ChatRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Ask a question and get an AI answer grounded in the project's documents.

    This is the endpoint the entire project was built to serve. The pipeline
    itself is `_answer_events` above; this endpoint runs it to the end and
    returns the final result as one JSON reply.
    """
    # ---- Authorise -------------------------------------------------------
    chat_session = await _get_owned_session(db, session_id, current_user)

    # ---- Is the organization within its monthly token quota? -------------
    # Checked BEFORE anything is saved or any model is called: a refused
    # question costs nothing and leaves no half-finished exchange behind.
    # Raises QuotaExceeded, which main.py turns into a 429 with Retry-After.
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
    # The same limit as the JSON endpoint: one question is one question,
    # however it is delivered.
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
    """
    Ask a question and receive the answer as a stream of Server-Sent Events.

    Everything that can be REFUSED is checked here, before the stream starts
    -- ownership (404), the rate limit and the token quota (429) -- so a
    refusal is an ordinary HTTP error the client handles like any other.
    Once streaming has begun the status is already 200, so a later failure
    arrives as an `error` event instead.

    WHY THE STREAM OPENS ITS OWN DATABASE SESSION
    The request's session (deps.get_db) belongs to the request handler, and
    may be closed as soon as this function RETURNS -- which is before the
    stream has produced its first byte. The generator therefore opens a
    session of its own, and closes it when the stream ends.
    """
    await _get_owned_session(db, session_id, current_user)
    await usage.check_token_quota(db, current_user.org_id)

    # Captured now: the generator runs after this function has returned.
    user_id = current_user.id
    request_id = observability.current_request_id()

    async def event_stream() -> AsyncIterator[str]:
        async with SessionLocal() as stream_db:
            try:
                # Reloaded in THIS session: ORM objects belong to the session
                # that loaded them.
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
                # Nothing is half-saved: the commit is the pipeline's last step.
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
            # Never cache a live stream, and tell nginx-style proxies not to
            # buffer it -- a buffered stream arrives all at once at the end,
            # which defeats the point.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ======================================================================
# FEEDBACK
# ======================================================================


@router.post("/messages/{message_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    message_id: uuid.UUID,
    payload: FeedbackCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Record a thumbs up or thumbs down on an assistant answer.

    In a production deployment this data is what tells you whether your
    retrieval settings are working. A cluster of downvotes on questions about
    one document usually means that document chunked or OCR'd badly. Admins
    see the summary at GET /org/feedback.
    """
    # Load the message and check the caller owns the session it belongs to.
    statement = select(ChatMessage).where(ChatMessage.id == message_id)
    result = await db.execute(statement)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found.",
        )

    # Reuse the same ownership helper via the message's parent session.
    await _get_owned_session(db, message.session_id, current_user)

    feedback = Feedback(
        message_id=message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(feedback)
    await db.commit()

    return {"status": "recorded", "rating": payload.rating}
