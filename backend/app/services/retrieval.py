"""
retrieval.py
============
WHAT THIS FILE DOES
-------------------
This is the "R" in RAG -- RETRIEVAL. It answers one question:

    "Out of every paragraph of every document in this project, which handful
     are most likely to contain the answer to what the user just asked?"

This file was the missing half of the system. Before it existed, documents
were being read, chunked and embedded into the database perfectly -- and then
nothing ever read those vectors back out. This is the piece that makes the
stored embeddings actually useful.

HOW SEMANTIC SEARCH ACTUALLY WORKS
----------------------------------
Ordinary search matches LETTERS. Searching "leave policy" finds only documents
containing those exact words, and misses a paragraph headed "Annual Holiday
Entitlement" entirely.

Semantic search matches MEANING:

    1. The user's question is converted into a vector -- a list of 384 numbers
       describing its meaning.  (embedding.py does this)
    2. Every chunk already has such a vector, computed when the document was
       uploaded.  (ingestion.py did this)
    3. We ask Postgres to sort every chunk by how CLOSE its vector is to the
       question's vector, and return the closest few.

"Close" is measured with COSINE DISTANCE, which compares the direction two
vectors point in rather than their length:

        distance 0.0  -> identical direction  -> same meaning
        distance 1.0  -> perpendicular        -> unrelated
        distance 2.0  -> opposite direction   -> opposite meaning

We convert that into an easier-to-read SIMILARITY score:

        similarity = 1 - distance      (1.0 = perfect match, 0 = unrelated)

WHY THE DATABASE DOES THE MATH
------------------------------
We could load all the vectors into Python and compare them there, but that
would mean pulling potentially millions of rows over the network on every
search. Instead, the `pgvector` extension teaches Postgres to understand
vectors natively, so the comparison and the sorting happen inside the database
and only the top few rows travel back. This is the single most important
performance decision in the whole retrieval path.

SECURITY -- THE PART THAT MATTERS MOST
--------------------------------------
This application is MULTI-TENANT: many organizations share one database. A
search MUST never return a chunk belonging to another organization. Every
query in this file therefore joins all the way up the ownership chain

        ChunkEmbedding -> Chunk -> Document -> Project -> Organization

and filters on the caller's own `org_id`. That check is not optional and is not
done in the frontend -- it is enforced here, in the query itself, so there is
no code path that can accidentally skip it.
"""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.models.project import Project
from app.services.embedding import EmbeddingService


class RetrievalService:
    """Semantic search over the stored document chunks."""

    @staticmethod
    async def search(
        db: AsyncSession,
        query: str,
        org_id: uuid.UUID,
        project_id: Optional[uuid.UUID] = None,
        document_id: Optional[uuid.UUID] = None,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find the chunks whose meaning is closest to `query`.

        Arguments:
            db          -- the open database session.
            query       -- the user's question, in plain English.
            org_id      -- the caller's organization. THE SECURITY BOUNDARY.
            project_id  -- optional: restrict the search to one project.
            document_id -- optional: restrict it further to a single document.
            top_k       -- how many results to return (defaults from settings).
            min_score   -- discard anything less similar than this.

        Returns a list of dictionaries, most similar first, each shaped like:
            {
              "chunk_id": UUID, "document_id": UUID,
              "filename": "handbook.pdf", "page_number": 12,
              "content": "the actual text of the chunk...",
              "score": 0.83, "chunk_index": 4,
            }

        The function is `async` because it waits on the database. While it
        waits, the server is free to handle other users' requests rather than
        sitting idle.
        """
        # `x if x is not None else default` rather than `x or default`, because
        # a deliberately passed 0 is falsy and would be wrongly replaced.
        top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
        min_score = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE

        # ----------------------------------------------------------
        # STEP 1: Turn the question into a vector.
        # ----------------------------------------------------------
        # Critically, this uses the SAME model that embedded the chunks at
        # upload time. Comparing vectors from two different models is
        # meaningless -- the numbers would not describe the same space.
        query_vector = EmbeddingService.get_embedding(query)

        if not query_vector:
            # Embedding failed entirely. Return nothing rather than running a
            # query with a broken vector and surfacing nonsense as "results".
            print("[Retrieval] Could not embed the query. Returning no results.")
            return []

        # ----------------------------------------------------------
        # STEP 2: Ask Postgres for the nearest chunks.
        # ----------------------------------------------------------
        # `.cosine_distance(...)` is provided by the pgvector package. It
        # compiles to Postgres's `<=>` operator, which is the vector distance
        # operator that the index is built on.
        distance = ChunkEmbedding.embedding.cosine_distance(query_vector)

        statement = (
            # Select the chunk, its parent document, and the computed distance.
            # `.label("distance")` names the calculated column so we can read
            # it back by name from each result row.
            select(Chunk, Document, distance.label("distance"))
            # Walk UP the ownership chain, one join per level:
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
            .join(Document, Chunk.document_id == Document.id)
            .join(Project, Document.project_id == Project.id)
            # >>> THE MULTI-TENANT SECURITY FILTER <<<
            # Without this line, one company could read another's documents.
            .where(Project.org_id == org_id)
            # Only search documents that finished processing. A document still
            # being chunked has partial embeddings and would give partial answers.
            .where(Document.status == "completed")
            # Closest first. This is the actual "search".
            .order_by(distance)
            # Stop after top_k rows. Combined with the index, Postgres does not
            # even score the rest of the table.
            .limit(top_k)
        )

        # Optional narrowing filters, applied only when the caller supplied them.
        if project_id is not None:
            statement = statement.where(Document.project_id == project_id)
        if document_id is not None:
            statement = statement.where(Document.id == document_id)

        result = await db.execute(statement)

        # ----------------------------------------------------------
        # STEP 3: Reshape the rows into plain dictionaries.
        # ----------------------------------------------------------
        # The API layer and the LLM prompt builder both want simple data, not
        # live SQLAlchemy objects that are tied to an open session.
        matches: List[Dict[str, Any]] = []

        for chunk, document, raw_distance in result.all():
            # Convert distance into an intuitive similarity score.
            similarity = 1.0 - float(raw_distance)

            # Drop weak matches. This is what allows an honest "I don't know":
            # without it, the closest chunk is always returned no matter how
            # irrelevant, and the LLM would try to answer from unrelated text.
            if similarity < min_score:
                continue

            matches.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": document.id,
                    "filename": document.filename,
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    # Rounded for display -- 0.83 reads better than
                    # 0.8300000000000001 in the UI and in JSON.
                    "score": round(similarity, 4),
                    "meta_data": chunk.meta_data,
                }
            )

        return matches

    @staticmethod
    async def build_history(
        db: AsyncSession, session_id: uuid.UUID, limit: int = 6
    ) -> List[Dict[str, str]]:
        """
        Load the recent back-and-forth of a chat session, for follow-up questions.

        Without this, every question would be treated as if it were the first.
        The user could not ask "and what about contractors?" -- the model would
        have no idea what "that" referred to.

        We return only the last `limit` messages because older turns rarely
        matter and every extra message makes the prompt slower and more costly.
        """
        # Imported here rather than at the top of the file to avoid a circular
        # import: the chat model module imports from places that import this one.
        from app.models.chat import ChatMessage

        statement = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            # Newest first, so LIMIT gives us the most RECENT messages...
            .order_by(
                ChatMessage.created_at.desc(),
                # ...with a TIE-BREAKER. created_at comes from Postgres now(),
                # which is the TRANSACTION start time, and a question and its
                # answer are saved in the same transaction -- so they share a
                # timestamp. Within such a pair the assistant row is the newer
                # one, so it sorts first here (and last after the reverse).
                case((ChatMessage.role == "assistant", 0), else_=1),
            )
            .limit(limit)
        )
        result = await db.execute(statement)
        rows = result.scalars().all()

        # ...then reverse back into chronological order, because a conversation
        # must be fed to the model oldest-first to read correctly.
        rows = list(reversed(rows))

        return [{"role": row.role, "content": row.content} for row in rows]
