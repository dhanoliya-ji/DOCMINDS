"""
chat.py
=======
WHAT THIS FILE DOES
-------------------
Defines the three tables that store conversations:

    ChatSession   one conversation thread
    ChatMessage   one message in it, from the user or the assistant
    Feedback      a thumbs up or down on an assistant answer

WHY CONVERSATIONS ARE STORED AT ALL
-----------------------------------
Three reasons, and the second is the one that changes how the app behaves:

  1. HISTORY. Users can reopen an old conversation and reread the answers.

  2. FOLLOW-UP QUESTIONS. This is the important one. Before generating an
     answer, `RetrievalService.build_history` loads the last few messages and
     includes them in the prompt. That is what lets someone ask "and what about
     part-time staff?" and be understood. Without stored messages, every
     question would be treated as the first.

  3. EVALUATION. Storing latency, token usage and the citations actually used
     is what makes it possible to measure whether the system is working.

THE CITATIONS COLUMN IS THE INTERESTING ONE
-------------------------------------------
Each assistant message stores the exact excerpts its answer was built from --
file, page, similarity score and a snippet. That is what makes the answer
verifiable rather than something you simply have to trust, and it is why the UI
can show expandable source cards under every reply.
"""

import datetime
import uuid
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.user import User


class ChatSession(Base):
    """
    One conversation. Table name: "chat_sessions".

    Scoped to BOTH a user and a project:
      * the user, so people only see their own conversations,
      * the project, so retrieval searches the right documents.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Starts as "New Chat" and is renamed to the first question asked, so the
    # sidebar shows something recognisable rather than a list of identical
    # placeholder titles.
    title: Mapped[str] = mapped_column(
        String(255), default="New Chat", nullable=False
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Determines WHICH documents this conversation can retrieve from.
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Bumped on every new message, so the sidebar can sort by recent activity
    # rather than by creation date -- a long-running thread you replied to a
    # minute ago belongs at the top.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")
    project: Mapped["Project"] = relationship(
        "Project", back_populates="chat_sessions"
    )
    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """One message in a conversation. Table name: "chat_messages"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )

    # "user" or "assistant". The frontend uses this to decide which side of the
    # screen to render the bubble on, and the LLM uses it to read the
    # conversation correctly when history is replayed.
    role: Mapped[str] = mapped_column(String(50), nullable=False)

    # `Text` rather than `String(n)` because answers have no fixed maximum.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # ---- Metrics. Populated on ASSISTANT messages only ----
    # Both nullable, because a user's own message has neither.

    # Tokens consumed by the API call -- prompt plus completion. Real usage
    # data, reported by the provider rather than estimated.
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=True)

    # How many seconds generation took. Shown in the UI, and the first number
    # to look at when someone reports the app feeling slow.
    latency: Mapped[float] = mapped_column(Float, nullable=True)

    # >>> THE CITATIONS <<<
    # A JSON list of the excerpts this answer was built from, each holding:
    #     number, chunk_id, document_id, filename, page_number, score, snippet
    #
    # `number` matches the bracketed marker the model wrote in its text, so
    # "[2]" in the answer maps to the second entry here.
    #
    # Stored as JSONB rather than as a separate table because citations are
    # always read together with their message and never queried independently.
    # A join table would add complexity for no benefit.
    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # ---- How the answer was produced. ASSISTANT messages only ----
    # Saved so a conversation reopened from the history sidebar looks exactly
    # as it did live: the mode badge, the model name and the agent's research
    # steps all come back. Before these columns existed the steps were only on
    # the live response and vanished on reload.

    # "rag" or "agent". NULL on user messages and on answers saved before the
    # column was added (migration a7b9c1d3e5f8).
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # The model that actually answered -- the fallback's name when the main
    # model failed, "mock" in demo mode, "error" when nothing could answer.
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # The agent's tool calls, in order: [{"tool", "args", "summary"}, ...].
    # Always [] for rag answers and user messages.
    steps: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )

    # How far the answer can be trusted, worked out from the retrieval scores
    # and how much of it is backed by citations (services/confidence.py):
    #     {"level": "high"|"medium"|"low", "score": 0.0-1.0,
    #      "retrieval": 0.0-1.0, "coverage": 0.0-1.0,
    #      "reasons": [{"code": "strong_match", "value": 0.82}, ...]}
    # Stored rather than recomputed so a reopened or shared answer shows the
    # verdict it was given at the time, even if the formula is tuned later.
    # NULL on user messages and on answers saved before migration
    # b8d0f2a4c6e8.
    confidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="messages"
    )
    feedbacks: Mapped[List["Feedback"]] = relationship(
        "Feedback", back_populates="message", cascade="all, delete-orphan"
    )


class Feedback(Base):
    """
    A rating on one assistant answer. Table name: "feedbacks".

    WHY COLLECT THIS
    In a real deployment this is the only honest signal of whether retrieval is
    actually working. A cluster of downvotes on questions about one particular
    document almost always means that document chunked or OCR'd badly -- which
    is invisible from the logs but obvious from the ratings.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )

    # 1 for thumbs up, -1 for thumbs down. An integer rather than a boolean
    # leaves room for a finer scale later without a migration, and summing the
    # column gives a net score directly.
    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional free text, which is usually where the actually useful detail is.
    comment: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    message: Mapped["ChatMessage"] = relationship(
        "ChatMessage", back_populates="feedbacks"
    )
