"""
reranker.py
===========
WHAT THIS FILE DOES
-------------------
Optionally re-orders search results with a CROSS-ENCODER, a second, more
careful model, before the best few are handed to the LLM.

    RERANKER=off              (the default) results keep the fused order
    RERANKER=cross-encoder    candidates are rescored by RERANKER_MODEL

WHY A SECOND MODEL CAN DO BETTER THAN THE FIRST
-----------------------------------------------
The embedding search (a "bi-encoder") turns the question and every chunk into
vectors SEPARATELY and compares them. That is what makes it fast enough to
search millions of chunks -- the chunk vectors are computed once, at upload --
but the model never actually reads the question and the chunk side by side.

A cross-encoder does read them together, as one input:

        [question] [SEP] [chunk text]   ->   one relevance score

so it can notice that a chunk mentions "leave" but is about UNPAID leave when
the question asked about annual leave. Far more accurate, and far too slow to
run over a whole library -- so it only rescores the ~20 candidates the fast
search already found. That two-stage shape (fast recall, careful precision)
is the standard design for search systems.

WHY IT IS OFF BY DEFAULT
------------------------
It needs `sentence-transformers` and a ~90 MB model download, and costs tens
of milliseconds of CPU per search. Hybrid search already fixes the most common
retrieval failure (exact terms), so the reranker is an upgrade to turn on
once the evaluation harness (app/eval) shows it helps on YOUR documents.

FAILING SAFE
------------
If the package is missing or the model cannot load, the results are returned
in their original order and a warning is logged once. A broken optional
component must never turn into a broken search.
"""

import logging
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger(__name__)

# A scorer takes [(question, passage), ...] and returns one float per pair,
# higher = more relevant. Injectable so the tests need no model download.
Scorer = Callable[[Sequence[tuple]], Sequence[float]]


@lru_cache(maxsize=1)
def _load_cross_encoder(model_name: str) -> Optional[Scorer]:
    """Load the model once per process; None if it cannot be loaded."""
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        logger.warning(
            "RERANKER=cross-encoder but sentence-transformers is not installed; "
            "results keep their original order"
        )
        return None
    try:
        model = CrossEncoder(model_name)
    except Exception as exc:  # network, disk, a bad model name
        logger.warning(
            "Could not load the reranker model; results keep their original order",
            extra={"model": model_name, "error": f"{type(exc).__name__}: {exc}"},
        )
        return None
    return lambda pairs: [float(score) for score in model.predict(list(pairs))]


def rerank(
    query: str,
    matches: List[Dict[str, Any]],
    scorer: Optional[Scorer] = None,
) -> List[Dict[str, Any]]:
    """
    Return `matches` sorted by the cross-encoder's relevance score.

    Each match gains a "rerank_score" so the order can be explained. Called
    on a worker thread by retrieval.py, because scoring is CPU-bound.
    """
    if not matches:
        return matches
    scorer = scorer or _load_cross_encoder(settings.RERANKER_MODEL)
    if scorer is None:
        return matches

    try:
        scores = list(scorer([(query, match.get("content") or "") for match in matches]))
    except Exception as exc:
        logger.warning("Reranking failed; keeping the original order", extra={"error": repr(exc)})
        return matches
    if len(scores) != len(matches):
        return matches

    for match, score in zip(matches, scores, strict=True):
        match["rerank_score"] = round(float(score), 4)
    # `sorted` is stable: equal scores keep their fused order.
    return sorted(matches, key=lambda match: match["rerank_score"], reverse=True)
