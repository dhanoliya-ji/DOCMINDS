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

ONLY VECTORS FROM THE CURRENT MODEL ARE COMPARED
------------------------------------------------
Every stored vector records the model that made it (`model_name`, see
embedding.py's current_model_name). After EMBEDDING_PROVIDER or the model is
changed, older documents still hold vectors from the previous model. Those
numbers describe a DIFFERENT space: a cosine distance between a new-model
question and an old-model chunk is meaningless, and would surface random
chunks with plausible-looking scores. So the search only considers chunks
embedded by the model in use now. Older documents simply stop matching until
they are re-processed -- an honest "not found" instead of a confident wrong one.

HYBRID SEARCH: MEANING *AND* EXACT WORDS
----------------------------------------
Semantic search has one blind spot: EXACT terms. "Policy HR-114", a product
code, a surname -- an embedding captures what a passage is ABOUT, not which
rare token it contains. Postgres full-text search is the opposite: perfect at
exact (stemmed) words, useless at paraphrase.

So with RETRIEVAL_HYBRID on (the default) every search runs both:

    vector   ORDER BY embedding <=> question          (meaning)
    keyword  WHERE to_tsvector(content) @@ query      (words, GIN-indexed)

and merges the two ranked lists with RECIPROCAL RANK FUSION -- see
reciprocal_rank_fusion() at the bottom of this file. An optional
cross-encoder reranker (RERANKER, off by default; services/reranker.py) can
then reorder the merged candidates before the top_k are kept.

NEVER BLOCKING THE SERVER
-------------------------
Embedding the question is SYNCHRONOUS work -- a local model crunching numbers,
or an HTTP call to OpenAI/Gemini. Run directly in this async function it would
freeze the event loop, and with it every other user's request, for its whole
duration. `asyncio.to_thread` runs it on a worker thread instead.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.models.project import Project
from app.services import reranker
from app.services.embedding import EmbeddingService

logger = logging.getLogger(__name__)


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
        document_ids: Optional[Sequence[uuid.UUID]] = None,
        hybrid: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find the chunks most relevant to `query`.

        Arguments:
            db           -- the open database session.
            query        -- the user's question, in plain English.
            org_id       -- the caller's organization. THE SECURITY BOUNDARY.
            project_id   -- optional: restrict the search to one project.
            document_id  -- optional: restrict it further to a single document.
            top_k        -- how many results to return (defaults from settings).
            min_score    -- discard SEMANTIC matches less similar than this.
            document_ids -- optional: restrict it to SEVERAL documents (the
                            "search only these files" filter in the UI). An
                            empty list means "no filter", not "nothing".
            hybrid       -- force hybrid search on or off; None follows
                            RETRIEVAL_HYBRID.

        Returns a list of dictionaries, best first, each shaped like:
            {
              "chunk_id": UUID, "document_id": UUID,
              "filename": "handbook.pdf", "page_number": 12,
              "content": "the actual text of the chunk...",
              "score": 0.83, "chunk_index": 4,
              "match_type": "both",   # "vector" | "keyword" | "both"
            }

        `score` is always the cosine SIMILARITY (0..1), even for a chunk that
        only the keyword search found, so the UI's "83% match" badge means the
        same thing for every result. The ORDER comes from the fused ranking.

        The function is `async` because it waits on the database. While it
        waits, the server is free to handle other users' requests rather than
        sitting idle.
        """
        # `x if x is not None else default` rather than `x or default`, because
        # a deliberately passed 0 is falsy and would be wrongly replaced.
        top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
        min_score = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
        hybrid = settings.RETRIEVAL_HYBRID if hybrid is None else hybrid

        # Each half of a hybrid search contributes this many candidates. With
        # hybrid off and no reranker, exactly top_k is enough.
        widen = hybrid or settings.RERANKER != "off"
        candidates = max(top_k, settings.RETRIEVAL_CANDIDATES) if widen else top_k

        # ----------------------------------------------------------
        # STEP 1: Turn the question into a vector.
        # ----------------------------------------------------------
        # Critically, this uses the SAME model that embedded the chunks at
        # upload time. Comparing vectors from two different models is
        # meaningless -- the numbers would not describe the same space.
        # On a thread: see "NEVER BLOCKING THE SERVER" above.
        query_vector = await asyncio.to_thread(EmbeddingService.get_embedding, query)
        model_name = EmbeddingService.current_model_name()

        if not query_vector:
            # Embedding failed entirely. Return nothing rather than running a
            # query with a broken vector and surfacing nonsense as "results".
            logger.warning("Could not embed the query; returning no results")
            return []

        # `.cosine_distance(...)` is provided by the pgvector package. It
        # compiles to Postgres's `<=>` operator, which is the vector distance
        # operator that the index is built on.
        distance = ChunkEmbedding.embedding.cosine_distance(query_vector)

        def scoped(statement):
            """
            The joins and filters EVERY query here shares -- written once, so
            the keyword query cannot forget the tenant filter the vector
            query has.
            """
            statement = (
                # Walk UP the ownership chain, one join per level:
                statement.join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
                .join(Document, Chunk.document_id == Document.id)
                .join(Project, Document.project_id == Project.id)
                # >>> THE MULTI-TENANT SECURITY FILTER <<<
                # Without this line, one company could read another's documents.
                .where(Project.org_id == org_id)
                # Only search documents that finished processing. A document
                # still being chunked has partial embeddings and would give
                # partial answers.
                .where(Document.status == "completed")
                # Only vectors from the model that embedded the question -- see
                # "ONLY VECTORS FROM THE CURRENT MODEL ARE COMPARED" above.
                .where(ChunkEmbedding.model_name == model_name)
            )
            # Optional narrowing filters, applied only when the caller supplied them.
            if project_id is not None:
                statement = statement.where(Document.project_id == project_id)
            if document_id is not None:
                statement = statement.where(Document.id == document_id)
            if document_ids:
                statement = statement.where(Document.id.in_(list(document_ids)))
            return statement

        # ----------------------------------------------------------
        # STEP 2a: SEMANTIC -- the nearest vectors.
        # ----------------------------------------------------------
        vector_statement = (
            scoped(select(Chunk, Document, distance.label("distance")).select_from(Chunk))
            # Closest first. This is the actual "search".
            .order_by(distance)
            # Combined with the HNSW index, Postgres does not even score the
            # rest of the table.
            .limit(candidates)
        )
        vector_rows = (await db.execute(vector_statement)).all()

        rows: Dict[Any, Dict[str, Any]] = {}
        vector_ranking: List[Any] = []
        for chunk, document, raw_distance in vector_rows:
            # Convert distance into an intuitive similarity score.
            similarity = 1.0 - float(raw_distance)
            # Drop weak matches. This is what allows an honest "I don't know":
            # without it, the closest chunk is always returned no matter how
            # irrelevant, and the LLM would try to answer from unrelated text.
            if similarity < min_score:
                continue
            rows[chunk.id] = _as_match(chunk, document, similarity)
            vector_ranking.append(chunk.id)

        # ----------------------------------------------------------
        # STEP 2b: KEYWORD -- Postgres full-text search (hybrid only).
        # ----------------------------------------------------------
        keyword_ranking: List[Any] = []
        if hybrid:
            # websearch_to_tsquery understands what people type into search
            # boxes -- quotes for phrases, "or", a leading "-" to exclude --
            # and, unlike to_tsquery, never raises a syntax error on odd input.
            tsquery = func.websearch_to_tsquery(FTS_CONFIG, query)
            rank = func.ts_rank_cd(fts_document(), tsquery)
            keyword_statement = (
                scoped(
                    select(Chunk, Document, distance.label("distance"), rank.label("rank"))
                    .select_from(Chunk)
                )
                # `@@` is "does this text match this query". The left side is
                # EXACTLY the expression the GIN index was built on (migration
                # a7b9c1d3e5f8), which is what lets Postgres use the index.
                .where(fts_document().op("@@")(tsquery))
                .order_by(rank.desc())
                .limit(candidates)
            )
            keyword_rows = (await db.execute(keyword_statement)).all()
            for chunk, document, raw_distance, _rank in keyword_rows:
                keyword_ranking.append(chunk.id)
                # A keyword hit is kept even below min_score: every word of
                # the query appears in it, which is relevance of a different
                # kind -- the exact-term case semantic search is bad at.
                rows.setdefault(chunk.id, _as_match(chunk, document, 1.0 - float(raw_distance)))

        # ----------------------------------------------------------
        # STEP 3: Fuse the two rankings, optionally rerank, cut to top_k.
        # ----------------------------------------------------------
        fused = reciprocal_rank_fusion(
            [vector_ranking, keyword_ranking], k=settings.RETRIEVAL_RRF_K
        )
        in_vector, in_keyword = set(vector_ranking), set(keyword_ranking)
        ordered = sorted(
            fused,
            # Highest fused score first; ties broken by similarity so the
            # order is stable and sensible.
            key=lambda chunk_id: (fused[chunk_id], rows[chunk_id]["score"]),
            reverse=True,
        )
        matches: List[Dict[str, Any]] = []
        for chunk_id in ordered:
            match = rows[chunk_id]
            match["match_type"] = match_type(chunk_id in in_vector, chunk_id in in_keyword)
            matches.append(match)

        if settings.RERANKER != "off" and len(matches) > 1:
            # A CPU-bound model call: on a thread, like the embedding above.
            matches = await asyncio.to_thread(reranker.rerank, query, matches)

        return matches[:top_k]

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


# ======================================================================
# HYBRID SEARCH HELPERS
# ======================================================================

# The text-search configuration: how words are split, stemmed ("policies"
# -> "polici", so it also matches "policy") and which stop words are
# ignored. MUST equal the one in migration a7b9c1d3e5f8's index, or the
# index is never used.
FTS_CONFIG = "english"


def fts_document():
    """
    The tsvector expression for a chunk's text.

    One function, so the WHERE clause in search() is always character for
    character the expression the GIN index was built on -- Postgres only
    uses an expression index for the identical expression.
    """
    return func.to_tsvector(FTS_CONFIG, Chunk.content)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Any]], k: int = 60
) -> Dict[Any, float]:
    """
    Merge several ranked lists into one score per item.

        score(item) = sum over the lists of  1 / (k + rank in that list)

    with ranks counted from 1. An item missing from a list gains nothing
    from it.

    WHY RANKS AND NOT SCORES: the two lists being merged score on scales
    that cannot be compared -- a cosine similarity of 0.62 and a ts_rank_cd
    of 0.1 say nothing about each other. RRF ignores the scores and uses only
    the positions, so it needs no tuning to combine them.

    An item near the top of BOTH lists beats one at the very top of just
    one, which is what we want: two very different methods agreeing is
    strong evidence.

        >>> scores = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
        >>> max(scores, key=scores.get)
        'b'
    """
    scores: Dict[Any, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return scores


def match_type(in_vector: bool, in_keyword: bool) -> str:
    """Which search found a result: "both", "vector" or "keyword"."""
    if in_vector and in_keyword:
        return "both"
    return "vector" if in_vector else "keyword"


def _as_match(chunk: Chunk, document: Document, similarity: float) -> Dict[str, Any]:
    """One result row, as the plain dictionary every caller expects."""
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "filename": document.filename,
        "page_number": chunk.page_number,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        # Rounded for display -- 0.83 reads better than 0.8300000000000001
        # in the UI and in JSON.
        "score": round(similarity, 4),
        "meta_data": chunk.meta_data,
    }
