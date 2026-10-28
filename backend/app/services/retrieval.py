"""This is the "R" in RAG -- RETRIEVAL."""
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
        """Find the chunks most relevant to `query`."""
        top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
        min_score = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
        hybrid = settings.RETRIEVAL_HYBRID if hybrid is None else hybrid

        widen = hybrid or settings.RERANKER != "off"
        candidates = max(top_k, settings.RETRIEVAL_CANDIDATES) if widen else top_k

        query_vector = await asyncio.to_thread(EmbeddingService.get_embedding, query)
        model_name = EmbeddingService.current_model_name()

        if not query_vector:
            logger.warning("Could not embed the query; returning no results")
            return []

        distance = ChunkEmbedding.embedding.cosine_distance(query_vector)

        def scoped(statement):
            """The joins and filters EVERY query here shares -- written once, so"""
            statement = (
                statement.join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
                .join(Document, Chunk.document_id == Document.id)
                .join(Project, Document.project_id == Project.id)
                .where(Project.org_id == org_id)
                .where(Document.status == "completed")
                .where(ChunkEmbedding.model_name == model_name)
            )
            if project_id is not None:
                statement = statement.where(Document.project_id == project_id)
            if document_id is not None:
                statement = statement.where(Document.id == document_id)
            if document_ids:
                statement = statement.where(Document.id.in_(list(document_ids)))
            return statement

        vector_statement = (
            scoped(select(Chunk, Document, distance.label("distance")).select_from(Chunk))
            .order_by(distance)
            .limit(candidates)
        )
        vector_rows = (await db.execute(vector_statement)).all()

        rows: Dict[Any, Dict[str, Any]] = {}
        vector_ranking: List[Any] = []
        for chunk, document, raw_distance in vector_rows:
            similarity = 1.0 - float(raw_distance)
            if similarity < min_score:
                continue
            rows[chunk.id] = _as_match(chunk, document, similarity)
            vector_ranking.append(chunk.id)

        keyword_ranking: List[Any] = []
        if hybrid:
            tsquery = func.websearch_to_tsquery(FTS_CONFIG, query)
            rank = func.ts_rank_cd(fts_document(), tsquery)
            keyword_statement = (
                scoped(
                    select(Chunk, Document, distance.label("distance"), rank.label("rank"))
                    .select_from(Chunk)
                )
                .where(fts_document().op("@@")(tsquery))
                .order_by(rank.desc())
                .limit(candidates)
            )
            keyword_rows = (await db.execute(keyword_statement)).all()
            for chunk, document, raw_distance, _rank in keyword_rows:
                keyword_ranking.append(chunk.id)
                rows.setdefault(chunk.id, _as_match(chunk, document, 1.0 - float(raw_distance)))

        fused = reciprocal_rank_fusion(
            [vector_ranking, keyword_ranking], k=settings.RETRIEVAL_RRF_K
        )
        in_vector, in_keyword = set(vector_ranking), set(keyword_ranking)
        ordered = sorted(
            fused,
            key=lambda chunk_id: (fused[chunk_id], rows[chunk_id]["score"]),
            reverse=True,
        )
        matches: List[Dict[str, Any]] = []
        for chunk_id in ordered:
            match = rows[chunk_id]
            match["match_type"] = match_type(chunk_id in in_vector, chunk_id in in_keyword)
            matches.append(match)

        if settings.RERANKER != "off" and len(matches) > 1:
            matches = await asyncio.to_thread(reranker.rerank, query, matches)

        return matches[:top_k]

    @staticmethod
    async def build_history(
        db: AsyncSession, session_id: uuid.UUID, limit: int = 6
    ) -> List[Dict[str, str]]:
        """Load the recent back-and-forth of a chat session, for follow-up questions."""
        from app.models.chat import ChatMessage

        statement = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(
                ChatMessage.created_at.desc(),
                case((ChatMessage.role == "assistant", 0), else_=1),
            )
            .limit(limit)
        )
        result = await db.execute(statement)
        rows = result.scalars().all()

        rows = list(reversed(rows))

        return [{"role": row.role, "content": row.content} for row in rows]


FTS_CONFIG = "english"


def fts_document():
    """The tsvector expression for a chunk's text."""
    return func.to_tsvector(FTS_CONFIG, Chunk.content)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Any]], k: int = 60
) -> Dict[Any, float]:
    """Merge several ranked lists into one score per item."""
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
        "score": round(similarity, 4),
        "meta_data": chunk.meta_data,
    }
