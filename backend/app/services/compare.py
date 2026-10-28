"""Compares two documents: "what changed between the 2023 and the 2024 contract?" The answer is a STRUCTURED..."""
import json
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, ChunkEmbedding
from app.models import Document
from app.services.embedding import EmbeddingService
from app.services.llm import UNTRUSTED_CONTENT_RULE, LLMService, clean_label, wrap_excerpt
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

MAX_SECTIONS_PER_DOC = 80
MAX_PAIRS = 8
MAX_UNPAIRED = 4
FOCUS_TOP_K = 8
EXCERPT_CHARS = 1200
MAX_DIFFERENCES = 15

ALIGN_THRESHOLD = {"embedding": 0.6, "lexical": 0.2}
IDENTICAL = {"embedding": 0.985, "lexical": 0.9}

KINDS = ("added", "removed", "changed")

COMPARE_SYSTEM_PROMPT = f"""You compare two versions or two related documents for an
enterprise user.

You will be given numbered excerpts from DOCUMENT A (A1, A2, ...) and
DOCUMENT B (B1, B2, ...). Excerpts listed together as a PAIR cover the same
part of both documents. Excerpts listed as ONLY IN A or ONLY IN B had no
matching section in the other document.

Report the meaningful differences:
- "removed": present in A, absent from B.
- "added": present in B, absent from A.
- "changed": covered by both, but the content differs (a figure, a date, a
  party, an obligation, a condition).

Rules:
1. Use ONLY the excerpts. Never invent a difference, and ignore pure
   formatting, wording or ordering changes that do not change the meaning.
2. Every difference must cite the excerpts it is based on, by number: "a"
   lists A excerpt numbers, "b" lists B excerpt numbers (an "added" item has
   an empty "a", a "removed" item an empty "b").
3. {UNTRUSTED_CONTENT_RULE}
4. Reply with ONE JSON object and nothing else, in exactly this shape:
   {{"summary": "two or three sentences on how the documents differ overall",
     "differences": [{{"kind": "changed", "topic": "a short label",
       "detail": "one or two sentences: what A says, what B says",
       "a": [1], "b": [2]}}]}}
   At most {MAX_DIFFERENCES} differences, the most important first. If the
   excerpts show no meaningful difference, return an empty list and say so in
   the summary.
"""


def _sample(items: Sequence[Any], limit: int) -> List[Any]:
    """`limit` items spread evenly over `items`, first and last included."""
    if len(items) <= limit:
        return list(items)
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def _as_vector(value: Any) -> Optional[List[float]]:
    """A stored embedding as a list of floats, whatever the driver returned."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    try:
        vector = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    return vector or None


async def load_sections(
    db: AsyncSession, document: Document, chunk_ids: Optional[List[Any]] = None
) -> List[Dict[str, Any]]:
    """A document's sections in reading order, each with its stored vector"""
    statement = select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)
    if chunk_ids is not None:
        statement = statement.where(Chunk.id.in_(chunk_ids))
    chunks = _sample(list((await db.execute(statement)).scalars().all()), MAX_SECTIONS_PER_DOC)
    if not chunks:
        return []

    vectors: Dict[Any, Optional[List[float]]] = {}
    try:
        rows = await db.execute(
            select(ChunkEmbedding.chunk_id, ChunkEmbedding.embedding).where(
                ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
                ChunkEmbedding.model_name == EmbeddingService.current_model_name(),
            )
        )
        vectors = {chunk_id: _as_vector(vector) for chunk_id, vector in rows.all()}
    except Exception as exc:  # pragma: no cover - a broken vector falls back to words
        logger.warning("Could not load embeddings for comparison", extra={"error": repr(exc)})

    return [
        {
            "chunk_id": c.id,
            "document_id": document.id,
            "filename": document.filename,
            "chunk_index": c.chunk_index,
            "page_number": c.page_number,
            "content": c.content or "",
            "vector": vectors.get(c.id),
        }
        for c in chunks
    ]


async def focused_sections(
    db: AsyncSession, document: Document, focus: str, org_id: Any
) -> List[Dict[str, Any]]:
    """Only the sections retrieval ranks closest to `focus`, in reading order."""
    try:
        hits = await RetrievalService.search(
            db=db,
            query=focus,
            org_id=org_id,
            project_id=document.project_id,
            document_id=document.id,
            top_k=FOCUS_TOP_K,
        )
    except Exception as exc:
        logger.warning("Focused retrieval failed; comparing whole documents", extra={"error": repr(exc)})
        return await load_sections(db, document)
    ids = [hit["chunk_id"] for hit in hits]
    if not ids:
        return await load_sections(db, document)
    return await load_sections(db, document, chunk_ids=ids)


_WORD = re.compile(r"[\w']{3,}", re.UNICODE)


def _words(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text or "")}


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def align(
    sections_a: List[Dict[str, Any]], sections_b: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Pair the sections of A with those of B."""
    use_vectors = (
        bool(sections_a)
        and bool(sections_b)
        and all(s["vector"] for s in sections_a + sections_b)
        and len({len(s["vector"]) for s in sections_a + sections_b}) == 1
    )
    method = "embedding" if use_vectors else "lexical"
    words_a = [_words(s["content"]) for s in sections_a]
    words_b = [_words(s["content"]) for s in sections_b]

    def similarity(i: int, j: int) -> float:
        if use_vectors:
            return _cosine(sections_a[i]["vector"], sections_b[j]["vector"])
        return _jaccard(words_a[i], words_b[j])

    threshold = ALIGN_THRESHOLD[method]
    pairs: List[Tuple[int, int, float]] = []
    only_a: List[int] = []
    matched_b: set = set()
    for i in range(len(sections_a)):
        if not sections_b:
            only_a.append(i)
            continue
        best_j, best = max(((j, similarity(i, j)) for j in range(len(sections_b))), key=lambda t: t[1])
        if best >= threshold:
            pairs.append((i, best_j, best))
            matched_b.add(best_j)
        else:
            only_a.append(i)
    only_b = [j for j in range(len(sections_b)) if j not in matched_b]

    different = [p for p in pairs if p[2] < IDENTICAL[method]]
    identical = len(pairs) - len(different)
    chosen = sorted(sorted(different, key=lambda p: p[2])[:MAX_PAIRS], key=lambda p: p[0])
    return {
        "method": method,
        "pairs": [(sections_a[i], sections_b[j], round(sim, 4)) for i, j, sim in chosen],
        "only_a": [sections_a[i] for i in only_a[:MAX_UNPAIRED]],
        "only_b": [sections_b[j] for j in only_b[:MAX_UNPAIRED]],
        "identical": identical,
    }


def _location(section: Dict[str, Any]) -> str:
    page = section.get("page_number")
    return f"{section['filename']}, page {page}" if page else section["filename"]


def number_excerpts(alignment: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """Give every excerpt its label (A1.., B1..) and build the prompt body."""
    citations: List[Dict[str, Any]] = []
    numbers: Dict[Tuple[str, Any], int] = {}

    def label(side: str, section: Dict[str, Any]) -> str:
        key = (side, section["chunk_id"])
        if key not in numbers:
            numbers[key] = sum(1 for c in citations if c["side"] == side) + 1
            citations.append(
                {
                    "side": side,
                    "number": numbers[key],
                    "label": f"{side.upper()}{numbers[key]}",
                    "chunk_id": section["chunk_id"],
                    "document_id": section["document_id"],
                    "filename": section["filename"],
                    "page_number": section.get("page_number"),
                    "snippet": section["content"][:300],
                }
            )
        return f"{side.upper()}{numbers[key]}"

    def excerpt(side: str, section: Dict[str, Any]) -> str:
        name = label(side, section)
        return f"EXCERPT {name}:\n{wrap_excerpt(section['content'][:EXCERPT_CHARS], _location(section))}"

    parts: List[str] = []
    for index, (a, b, _sim) in enumerate(alignment["pairs"], 1):
        parts.append(f"PAIR {index}:\n{excerpt('a', a)}\n{excerpt('b', b)}")
    if alignment["only_a"]:
        parts.append("ONLY IN A:\n" + "\n".join(excerpt("a", s) for s in alignment["only_a"]))
    if alignment["only_b"]:
        parts.append("ONLY IN B:\n" + "\n".join(excerpt("b", s) for s in alignment["only_b"]))
    return citations, "\n\n".join(parts)


def parse_reply(text: str, citations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The model's JSON as {"summary", "differences"}, or None if it is not JSON."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    data: Any = None
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return None

    valid = {"a": {c["number"] for c in citations if c["side"] == "a"},
             "b": {c["number"] for c in citations if c["side"] == "b"}}

    def refs(value: Any, side: str) -> List[int]:
        out: List[int] = []
        for item in value if isinstance(value, list) else []:
            match = re.fullmatch(r"[ABab]?(\d+)", str(item).strip())
            if match and int(match.group(1)) in valid[side] and int(match.group(1)) not in out:
                out.append(int(match.group(1)))
        return out

    differences: List[Dict[str, Any]] = []
    for item in data.get("differences") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower().strip()
        topic = " ".join(str(item.get("topic") or "").split())[:200]
        detail = " ".join(str(item.get("detail") or "").split())[:1200]
        if kind not in KINDS or not (topic or detail):
            continue
        differences.append(
            {"kind": kind, "topic": topic or detail[:60], "detail": detail,
             "a": refs(item.get("a"), "a"), "b": refs(item.get("b"), "b")}
        )
        if len(differences) >= MAX_DIFFERENCES:
            break
    summary = " ".join(str(data.get("summary") or "").split())[:2000]
    return {"summary": summary, "differences": differences}


def heuristic_diff(alignment: Dict[str, Any], citations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The alignment itself, described: no model involved."""
    number = {(c["side"], c["chunk_id"]): c["number"] for c in citations}

    def topic(section: Dict[str, Any]) -> str:
        page = section.get("page_number")
        return f"Section on page {page}" if page else f"Section {section['chunk_index'] + 1}"

    def preview(section: Dict[str, Any]) -> str:
        text = " ".join(section["content"].split())
        return text[:160] + ("..." if len(text) > 160 else "")

    differences: List[Dict[str, Any]] = []
    for a, b, sim in alignment["pairs"]:
        differences.append({
            "kind": "changed", "topic": topic(a),
            "detail": f"These passages correspond but differ ({round(sim * 100)}% similar).",
            "a": [number[("a", a["chunk_id"])]], "b": [number[("b", b["chunk_id"])]],
        })
    for a in alignment["only_a"]:
        differences.append({"kind": "removed", "topic": topic(a), "detail": preview(a),
                            "a": [number[("a", a["chunk_id"])]], "b": []})
    for b in alignment["only_b"]:
        differences.append({"kind": "added", "topic": topic(b), "detail": preview(b),
                            "a": [], "b": [number[("b", b["chunk_id"])]]})
    summary = (
        f"{len(alignment['pairs'])} corresponding section(s) differ, "
        f"{len(alignment['only_a'])} appear only in the first document and "
        f"{len(alignment['only_b'])} only in the second "
        f"({alignment['identical']} matched section(s) are unchanged)."
    )
    return {"summary": summary, "differences": differences[:MAX_DIFFERENCES]}


async def compare_documents(
    db: AsyncSession,
    document_a: Document,
    document_b: Document,
    *,
    org_id: Any,
    focus: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare two documents the caller is allowed to read."""
    started_at = time.perf_counter()
    focus = " ".join((focus or "").split()) or None
    if focus:
        sections_a = await focused_sections(db, document_a, focus, org_id)
        sections_b = await focused_sections(db, document_b, focus, org_id)
    else:
        sections_a = await load_sections(db, document_a)
        sections_b = await load_sections(db, document_b)

    alignment = align(sections_a, sections_b)
    citations, body = number_excerpts(alignment)
    stats = {"model": "mock", "tokens_used": 0, "prompt_tokens": 0, "completion_tokens": 0}

    if not citations:
        result = {
            "summary": "No differences were found in the compared sections."
            if alignment["identical"] else "These documents have no text to compare yet.",
            "differences": [],
        }
    else:
        result = None
        if not LLMService.is_keyless():
            header = (
                f"DOCUMENT A: {clean_label(document_a.filename)}\n"
                f"DOCUMENT B: {clean_label(document_b.filename)}\n"
                + (f"FOCUS: compare only what concerns: {clean_label(focus, 300)}\n" if focus else "")
            )
            reply = await LLMService.complete([
                {"role": "system", "content": COMPARE_SYSTEM_PROMPT},
                {"role": "user", "content": f"{header}\n{body}\n\nReply with the JSON object only."},
            ])
            stats = {key: reply[key] for key in stats}
            result = parse_reply(reply["text"], citations)
            if result is None and reply["model"] != "error":
                logger.warning("The comparison reply was not valid JSON; using the alignment")
        if result is None:
            result = heuristic_diff(alignment, citations)

    return {
        **result,
        "citations": citations,
        "alignment": alignment["method"],
        "pairs": len(alignment["pairs"]),
        "identical": alignment["identical"],
        **stats,
        "latency": round(time.perf_counter() - started_at, 3),
    }
