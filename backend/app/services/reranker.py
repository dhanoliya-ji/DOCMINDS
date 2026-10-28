"""Optionally re-orders search results with a CROSS-ENCODER, a second, more careful model, before the best..."""
import logging
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger(__name__)

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
    """Return `matches` sorted by the cross-encoder's relevance score."""
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
    return sorted(matches, key=lambda match: match["rerank_score"], reverse=True)
