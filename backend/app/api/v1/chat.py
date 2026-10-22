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
   4. RETRIEVE : retrieval.py embeds the question and finds the 5 closest
                 chunks in Postgres, restricted to their organization.
   5. AUGMENT  : llm.py pastes those 5 chunks into a prompt, numbered [1]..[5].
   6. GENERATE : GPT-OSS 120B on Groq writes an answer using only those excerpts.
   7. We build citations linking each [n] back to its file and page.
   8. We save the answer as a ChatMessage                 (role="assistant")
      along with the citations, token count and how long it took --
      and, in the same transaction, a usage event (services/usage.py).
   9. We return both messages so the UI can render the exchange.

TWO MODES (the request's `mode` field)
--------------------------------------
The journey above is mode="rag", the default: a FIXED pipeline, one search and
one model call. Fast, cheap and identical for every question.

mode="agent" swaps steps 4-6 for the Pydantic AI agent in services/agent.py:

   4'. The model is given TOOLS (search_documents, list_documents,
       read_document_section) instead of a fixed set of excerpts.
   5'. It decides which tools to call, possibly several times -- e.g. one
       search per half of a comparison question -- capped at AGENT_MAX_STEPS
       model calls.
   6'. Every chunk any tool surfaced gets one GLOBAL citation number, so the
       model's "[n]" lines up with the citations list we return.

Steps 1-3 and 7-9 are shared. The agent's tool trace is returned to the UI as
`steps`, but is not stored in the database.

ENDPOINTS DEFINED HERE
----------------------
    POST   /chat/sessions                     start a conversation
    GET    /chat/sessions                     list my conversations
    GET    /chat/sessions/{id}/messages       read one conversation
    POST   /chat/sessions/{id}/messages       ask a question  <- the main one
    DELETE /chat/sessions/{id}                delete a conversation
    POST   /chat/messages/{id}/feedback       rate an answer
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core import observability
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
    Citation,
    FeedbackCreate,
)
from app.services import usage
from app.services.agent import run_agent
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService

router = APIRouter()


# ======================================================================
# SHARED HELPER
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
        title=payload.title or "New Chat",
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
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List the current user's conversations, most recently used first."""
    statement = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        # Order by updated_at rather than created_at so a long-running
        # conversation you replied to a minute ago rises back to the top.
        .order_by(ChatSession.updated_at.desc())
    )
    result = await db.execute(statement)
    return result.scalars().all()


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageOut])
async def list_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Read the full transcript of one conversation, oldest message first."""
    await _get_owned_session(db, session_id, current_user)

    statement = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        # Ascending, because a transcript reads top to bottom.
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(statement)
    return result.scalars().all()


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
# THE MAIN EVENT: ASK A QUESTION (full RAG pipeline)
# ======================================================================


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

    This is the endpoint the entire project was built to serve. Read the
    numbered steps below to follow the complete RAG flow end to end.
    """
    # ---- STEP 1: Authorise -------------------------------------------
    chat_session = await _get_owned_session(db, session_id, current_user)

    # ---- STEP 1b: Is the organization within its monthly token quota? --
    # Checked BEFORE anything is saved or any model is called: a refused
    # question costs nothing and leaves no half-finished exchange behind.
    # Raises QuotaExceeded, which main.py turns into a 429 with Retry-After.
    await usage.check_token_quota(db, current_user.org_id)

    # ---- STEP 1c: Load the conversation so far -----------------------
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
    )
    db.add(user_message)
    await db.flush()  # assigns user_message.id without committing yet

    # A brand-new session is titled "New Chat". Rename it after the first
    # question so the sidebar shows something recognisable, the way ChatGPT
    # and similar tools do.
    if chat_session.title == "New Chat":
        title = payload.message[:60]
        # Add an ellipsis only if we actually truncated the text.
        chat_session.title = title + ("..." if len(payload.message) > 60 else "")

    if payload.mode == "agent":
        # ---- AGENT MODE: steps 3-6 handled by the Pydantic AI agent ---
        agent_result = await run_agent(
            db=db,
            question=payload.message,
            org_id=current_user.org_id,
            project_id=chat_session.project_id,
            top_k=payload.top_k,
            history=history,
        )
        # Same dictionary keys as LLMService.generate_answer returns, so
        # STEP 7 below does not need to know which mode produced the answer.
        llm_result = agent_result

        # Citations use the GLOBAL numbers the agent's tools assigned, not
        # enumerate(): the model saw "EXCERPT [7]" and wrote "[7]", so the
        # citation must be numbered 7 too.
        citations = [
            Citation(
                number=chunk["number"],
                chunk_id=chunk["chunk_id"],
                document_id=chunk["document_id"],
                filename=chunk["filename"],
                page_number=chunk.get("page_number"),
                score=chunk["score"],
                snippet=chunk["content"][:300],
            )
            for chunk in agent_result["citations_chunks"]
        ]
        steps = [AgentStep(**step) for step in agent_result["steps"]]
    else:
        # ---- STEP 3: RETRIEVE --------------------------------------------
        # Find the passages in this project's documents that are closest in meaning
        # to the question. This is the "R" of RAG.
        chunks = await RetrievalService.search(
            db=db,
            query=payload.message,
            org_id=current_user.org_id,
            project_id=chat_session.project_id,
            top_k=payload.top_k,
        )

        # ---- STEP 4: Conversation history (loaded in STEP 1c) -----------
        # So the user can ask a follow-up such as "and for part-time staff?" and
        # have it understood in the context of the previous question.

        # ---- STEP 5: AUGMENT + GENERATE ----------------------------------
        # llm.py pastes the retrieved chunks into the prompt (augment) and asks
        # GPT-OSS 120B on Groq to answer using only those chunks (generate).
        # Awaited: the model call waits on the network WITHOUT blocking the
        # server (llm.py uses Groq's async client), and is retried / falls
        # back to LLM_FALLBACK_MODEL on transient failures.
        llm_result = await LLMService.generate_answer(
            question=payload.message,
            chunks=chunks,
            history=history,
        )

        # ---- STEP 6: Build citations -------------------------------------
        # Map every excerpt we sent to the model back to its real source, so the
        # user can click "[2]" and see the exact file and page it came from.
        # The numbering here MUST match the numbering used in build_context_prompt,
        # which also enumerates from 1 over the same list in the same order.
        citations = [
            Citation(
                number=index,
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
        steps = []

    # ---- STEP 7: Save the answer -------------------------------------
    assistant_message = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=llm_result["answer"],
        tokens_used=llm_result["tokens_used"],
        latency=llm_result["latency"],
        # Citations are stored as JSONB, so they must be plain JSON-safe types.
        # `mode="json"` converts the UUID objects into strings for us.
        citations=[citation.model_dump(mode="json") for citation in citations],
    )
    db.add(assistant_message)

    # ---- STEP 7b: Meter it ---------------------------------------------
    # One usage event per answer: tokens in and out, the model that actually
    # answered (the fallback's name if the main one failed) and its
    # estimated cost. Staged in the SAME transaction as the answer, so usage
    # is never recorded for an answer that was not saved, or vice versa.
    usage.record_chat_usage(
        db,
        org_id=current_user.org_id,
        user_id=current_user.id,
        mode=payload.mode,
        model=llm_result["model"],
        prompt_tokens=llm_result.get("prompt_tokens", 0),
        completion_tokens=llm_result.get("completion_tokens", 0),
        total_tokens=llm_result.get("tokens_used", 0),
        request_id=observability.current_request_id(),
    )

    # One commit saves the question, the answer, its usage and the renamed title together.
    # Because it is a single transaction, we can never end up with a question
    # stored but its answer missing.
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)

    # ---- STEP 8: Reply -----------------------------------------------
    return ChatResponse(
        session_id=session_id,
        user_message=user_message,
        assistant_message=assistant_message,
        citations=citations,
        model=llm_result["model"],
        mode=payload.mode,
        steps=steps,
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
    one document usually means that document chunked or OCR'd badly.
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
