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

   1. The request arrives here with a JWT proving who they are.
   2. We verify the chat session belongs to them          (security)
   3. We save their question as a ChatMessage             (role="user")
   4. RETRIEVE : retrieval.py embeds the question and finds the 5 closest
                 chunks in Postgres, restricted to their organization.
   5. AUGMENT  : llm.py pastes those 5 chunks into a prompt, numbered [1]..[5].
   6. GENERATE : GPT-OSS 120B on Groq writes an answer using only those excerpts.
   7. We build citations linking each [n] back to its file and page.
   8. We save the answer as a ChatMessage                 (role="assistant")
      along with the citations, token count and how long it took.
   9. We return both messages so the UI can render the exchange.

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
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.project import Project
from app.models.user import User
from app.schemas.rag import (
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ChatSessionCreate,
    ChatSessionOut,
    Citation,
    FeedbackCreate,
)
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


@router.post("/sessions/{session_id}/messages", response_model=ChatResponse)
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

    # ---- STEP 4: Load conversation history ---------------------------
    # So the user can ask a follow-up such as "and for part-time staff?" and
    # have it understood in the context of the previous question.
    history = await RetrievalService.build_history(db, session_id)

    # ---- STEP 5: AUGMENT + GENERATE ----------------------------------
    # llm.py pastes the retrieved chunks into the prompt (augment) and asks
    # GPT-OSS 120B on Groq to answer using only those chunks (generate).
    llm_result = LLMService.generate_answer(
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

    # One commit saves the question, the answer and the renamed title together.
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
