"""
confidence.py
=============
WHAT THIS FILE DOES
-------------------
Gives every answer a CONFIDENCE verdict -- high, medium or low -- and the
reasons for it, so the reader knows how much checking an answer needs
before they act on it.

    {"level": "medium", "score": 0.58, "retrieval": 0.71, "coverage": 0.45,
     "reasons": [{"code": "strong_match", "value": 0.71},
                 {"code": "partly_cited", "value": 0.45}]}

THE TWO SIGNALS, AND WHY THESE TWO
----------------------------------
A language model cannot honestly rate its own certainty -- asked "how sure
are you?" it will produce a confident-sounding number whatever the truth.
So the verdict is computed from things we MEASURED instead:

  1. RETRIEVAL STRENGTH -- how close the best excerpts were to the question.
     The cosine similarity retrieval.py already reports. Weak matches mean
     the documents may simply not cover the question, and the model had
     little to work from.

         retrieval = 0.6 * best score + 0.4 * mean of the top three

     The best match matters most (one excellent passage is often enough);
     the mean stops a single lucky hit from carrying the whole verdict.
     Excerpts that were READ by the agent rather than found by a search
     carry score 0.0 (agent.py) and are left out: no score is not a bad
     score.

  2. CITATION COVERAGE -- how much of the answer is backed by a citation
     marker that points at a real source. Each sentence-like line of the
     answer counts as one claim; a claim is covered when it carries "[n]"
     for an n that exists in the citations list. Markers pointing at a
     number that does NOT exist are counted separately -- the model
     inventing a source is a stronger warning than leaving one out.

     Table rows, chart blocks and headings are not claims (a table is
     usually cited once, under it), so they are ignored rather than counted
     as uncited.

THE VERDICT
-----------
    score = 0.5 * retrieval + 0.5 * coverage, then penalties:
        invented citation numbers   -0.15
        only one source             -0.05 (a single passage is thin support)

    high    score >= 0.65 and retrieval >= 0.45
    medium  score >= 0.40
    low     everything else

Two answers are always "low" whatever the numbers: the "not found" sentence
(the model itself said the documents do not answer it), and a failed answer
(model "error"). Demo mode (model "mock") is marked with its own reason so
nobody mistakes a raw excerpt for a written answer.

The thresholds are deliberately simple and live here, in one place, so they
can be tuned against the eval harness (app/eval) without touching anything
else.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from app.services.llm import NOT_FOUND_ANSWER

# Score boundaries for a single excerpt, used for the retrieval reason.
STRONG_MATCH = 0.55
MODERATE_MATCH = 0.35

HIGH_SCORE = 0.65
HIGH_MIN_RETRIEVAL = 0.45
MEDIUM_SCORE = 0.40

INVENTED_CITATION_PENALTY = 0.15
SINGLE_SOURCE_PENALTY = 0.05

# "[3]" or "[1, 2]" or "[1][2]" -- every bracketed number in the answer.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# Code fences (charts, tables pasted as code): skipped wholesale.
_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)


def _claims(answer: str) -> List[str]:
    """
    Split an answer into the units that should each carry a citation.

    Lines first (bullets and paragraphs are the model's own unit), then
    sentences within a line. Headings, table rows and very short fragments
    ("Summary:") are not claims.
    """
    text = _FENCE.sub("", answer or "")
    claims: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "|")) or set(line) <= set("-*_=: "):
            continue
        # Sentence ends: ". ", "? ", "! " -- but a citation marker straight
        # after the full stop belongs to the sentence before it.
        for sentence in re.split(r"(?<=[.!?])\s+(?!\[)", line):
            words = re.sub(r"[*_`>-]", " ", sentence).split()
            if len(words) >= 4:
                claims.append(sentence)
    return claims


def _cited_numbers(text: str) -> List[int]:
    numbers: List[int] = []
    for group in _MARKER.findall(text or ""):
        numbers.extend(int(n) for n in re.split(r"\s*,\s*", group))
    return numbers


def assess(
    answer: str,
    citations: Iterable[Dict[str, Any]],
    *,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    The confidence verdict for one answer.

    `citations` are the citation dictionaries saved with the message (each
    with "number" and "score"); Citation models work too, via model_dump.
    """
    entries = [
        c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in citations or []
    ]
    numbers = {int(c.get("number", 0)) for c in entries}
    reasons: List[Dict[str, Any]] = []

    # ---- The verdicts that need no arithmetic ------------------------
    if model == "error":
        return _verdict("low", 0.0, 0.0, 0.0, [{"code": "model_error"}])
    stripped = (answer or "").strip()
    if not entries or stripped == NOT_FOUND_ANSWER:
        return _verdict("low", 0.0, 0.0, 0.0, [{"code": "no_sources" if not entries else "not_found"}])

    # ---- 1. Retrieval strength ------------------------------------------
    scores = sorted((float(c.get("score") or 0.0) for c in entries), reverse=True)
    searched = [s for s in scores if s > 0]
    if searched:
        top3 = searched[:3]
        retrieval = 0.6 * searched[0] + 0.4 * (sum(top3) / len(top3))
        if searched[0] >= STRONG_MATCH:
            reasons.append({"code": "strong_match", "value": round(searched[0], 2)})
        elif searched[0] >= MODERATE_MATCH:
            reasons.append({"code": "moderate_match", "value": round(searched[0], 2)})
        else:
            reasons.append({"code": "weak_match", "value": round(searched[0], 2)})
    else:
        # Only read sections, no search scores: neutral, not bad.
        retrieval = 0.5
        reasons.append({"code": "read_sections"})
    retrieval = max(0.0, min(1.0, retrieval))

    # ---- 2. Citation coverage --------------------------------------------
    claims = _claims(stripped)
    cited_anywhere = _cited_numbers(stripped)
    invented = sorted({n for n in cited_anywhere if n not in numbers})
    if claims:
        covered = sum(1 for claim in claims if any(n in numbers for n in _cited_numbers(claim)))
        coverage = covered / len(claims)
    else:
        # Nothing claim-like (a bare table or chart): cited at all is enough.
        coverage = 1.0 if any(n in numbers for n in cited_anywhere) else 0.0

    if model == "mock":
        reasons.append({"code": "demo_mode"})
    elif coverage >= 0.8:
        reasons.append({"code": "well_cited", "value": round(coverage, 2)})
    elif coverage >= 0.4:
        reasons.append({"code": "partly_cited", "value": round(coverage, 2)})
    else:
        reasons.append({"code": "few_citations", "value": round(coverage, 2)})

    distinct_sources = {
        str(c.get("document_id") or c.get("filename") or "")
        for c in entries
        if int(c.get("number", 0)) in cited_anywhere
    }

    # ---- The verdict ---------------------------------------------------
    score = 0.5 * retrieval + 0.5 * coverage
    if invented:
        score -= INVENTED_CITATION_PENALTY
        reasons.append({"code": "invented_citations", "value": len(invented)})
    if len(distinct_sources) == 1:
        score -= SINGLE_SOURCE_PENALTY
        reasons.append({"code": "single_source"})
    elif len(distinct_sources) > 1:
        reasons.append({"code": "multiple_sources", "value": len(distinct_sources)})
    score = max(0.0, min(1.0, score))

    if model != "mock" and score >= HIGH_SCORE and retrieval >= HIGH_MIN_RETRIEVAL:
        level = "high"
    elif score >= MEDIUM_SCORE:
        level = "medium"
    else:
        level = "low"
    return _verdict(level, score, retrieval, coverage, reasons)


def _verdict(
    level: str, score: float, retrieval: float, coverage: float, reasons: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "level": level,
        "score": round(score, 3),
        "retrieval": round(retrieval, 3),
        "coverage": round(coverage, 3),
        "reasons": reasons,
    }
