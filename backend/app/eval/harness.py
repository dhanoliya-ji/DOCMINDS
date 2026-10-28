"""Measures how well the system answers questions, against a GOLDEN SET: a JSON file of questions whose right..."""
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SearchFn = Callable[[str, int], Awaitable[List[Dict[str, Any]]]]
AnswerFn = Callable[[str, List[Dict[str, Any]]], Awaitable[str]]
JudgeFn = Callable[[str, List[Dict[str, Any]], str], Awaitable[Optional[float]]]


@dataclass
class GoldenCase:
    """One question with known right sources."""

    id: str
    question: str
    expected_documents: List[str] = field(default_factory=list)
    expected_snippets: List[str] = field(default_factory=list)
    expected_keywords: List[str] = field(default_factory=list)
    reference_answer: Optional[str] = None


@dataclass
class GoldenSet:
    name: str
    cases: List[GoldenCase]
    top_k: Optional[int] = None


class GoldenSetError(ValueError):
    """The golden-set file is not in the expected shape."""


def _strings(raw: Dict[str, Any], case_id: str, key: str) -> List[str]:
    """One optional list-of-strings field of a case, validated."""
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GoldenSetError(f'Case "{case_id}": "{key}" must be a list of strings.')
    return value


def parse_golden_set(data: Dict[str, Any]) -> GoldenSet:
    """Validate and load a golden set from already-parsed JSON."""
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise GoldenSetError('A golden set is an object with a "cases" list.')

    cases: List[GoldenCase] = []
    seen: set = set()
    for position, raw in enumerate(data["cases"], 1):
        if not isinstance(raw, dict) or not str(raw.get("question") or "").strip():
            raise GoldenSetError(f"Case {position} has no question.")
        case_id = str(raw.get("id") or f"case-{position}")
        if case_id in seen:
            raise GoldenSetError(f'Two cases share the id "{case_id}".')
        seen.add(case_id)

        cases.append(
            GoldenCase(
                id=case_id,
                question=str(raw["question"]).strip(),
                expected_documents=_strings(raw, case_id, "expected_documents"),
                expected_snippets=_strings(raw, case_id, "expected_snippets"),
                expected_keywords=_strings(raw, case_id, "expected_keywords"),
                reference_answer=raw.get("reference_answer"),
            )
        )
    if not cases:
        raise GoldenSetError("The golden set has no cases.")
    top_k = data.get("top_k")
    return GoldenSet(name=str(data.get("name") or "golden set"), cases=cases,
                     top_k=int(top_k) if top_k else None)


def load_golden_set(path: str | Path) -> GoldenSet:
    """Read and validate a golden-set JSON file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenSetError(f"{path} is not valid JSON: {exc}") from exc
    return parse_golden_set(data)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def is_relevant(case: GoldenCase, result: Dict[str, Any]) -> bool:
    """Does this retrieval result count as a right source for `case`?"""
    if not case.expected_documents:
        return False
    if (result.get("filename") or "") not in case.expected_documents:
        return False
    if not case.expected_snippets:
        return True
    content = _norm(result.get("content") or "")
    return any(_norm(snippet) in content for snippet in case.expected_snippets)


def first_relevant_rank(case: GoldenCase, results: List[Dict[str, Any]]) -> Optional[int]:
    """1-based position of the first relevant result, or None."""
    for position, result in enumerate(results, 1):
        if is_relevant(case, result):
            return position
    return None


def retrieval_hit(case: GoldenCase, results: List[Dict[str, Any]]) -> bool:
    """A HIT is a relevant result in the list -- or, for a question that should"""
    if not case.expected_documents:
        return not results
    return first_relevant_rank(case, results) is not None


def keywords_present(case: GoldenCase, answer: str) -> Optional[bool]:
    """Every expected keyword in the answer? None when none are listed."""
    if not case.expected_keywords:
        return None
    text = _norm(answer)
    return all(_norm(keyword) in text for keyword in case.expected_keywords)


@dataclass
class CaseResult:
    id: str
    question: str
    hit: bool
    rank: Optional[int]
    retrieved: List[str]
    expects_source: bool = True
    answer: Optional[str] = None
    keywords_ok: Optional[bool] = None
    faithfulness: Optional[float] = None
    seconds: float = 0.0


@dataclass
class EvalReport:
    name: str
    top_k: int
    cases: List[CaseResult]
    hit_rate: float = 0.0
    mrr: float = 0.0
    keyword_rate: Optional[float] = None
    faithfulness: Optional[float] = None
    settings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def summarise(report: EvalReport) -> EvalReport:
    """Fill in the aggregate numbers from the per-case results."""
    cases = report.cases
    report.hit_rate = _mean([1.0 if c.hit else 0.0 for c in cases]) or 0.0
    ranked = [c for c in cases if c.expects_source]
    report.mrr = _mean([1.0 / c.rank if c.rank else 0.0 for c in ranked]) or 0.0
    report.keyword_rate = _mean([1.0 if c.keywords_ok else 0.0 for c in cases if c.keywords_ok is not None])
    report.faithfulness = _mean([c.faithfulness for c in cases if c.faithfulness is not None])
    return report


async def run_evaluation(
    golden: GoldenSet,
    search_fn: SearchFn,
    *,
    top_k: int = 5,
    answer_fn: Optional[AnswerFn] = None,
    judge_fn: Optional[JudgeFn] = None,
    settings_snapshot: Optional[Dict[str, Any]] = None,
) -> EvalReport:
    """Run every case: retrieve, score retrieval, and -- when `answer_fn` is"""
    results: List[CaseResult] = []
    for case in golden.cases:
        started = time.perf_counter()
        retrieved = await search_fn(case.question, top_k)
        result = CaseResult(
            id=case.id,
            question=case.question,
            hit=retrieval_hit(case, retrieved),
            rank=first_relevant_rank(case, retrieved),
            expects_source=bool(case.expected_documents),
            retrieved=[
                f"{r.get('filename')}" + (f" p.{r['page_number']}" if r.get("page_number") else "")
                for r in retrieved
            ],
        )
        if answer_fn is not None:
            result.answer = await answer_fn(case.question, retrieved)
            result.keywords_ok = keywords_present(case, result.answer)
            if judge_fn is not None and retrieved:
                result.faithfulness = await judge_fn(case.question, retrieved, result.answer)
        result.seconds = round(time.perf_counter() - started, 3)
        results.append(result)

    return summarise(
        EvalReport(name=golden.name, top_k=top_k, cases=results, settings=settings_snapshot or {})
    )


JUDGE_PROMPT = """You grade whether an ANSWER is faithful to the EXCERPTS it was written from.
Faithful means every factual claim in the answer is supported by the excerpts.
An answer saying the information could not be found is faithful if the excerpts
indeed do not contain it. Ignore style. Reply with JSON only:
{"score": <number from 0 to 1>, "reason": "<one sentence>"}"""


def parse_judge_reply(text: str) -> Optional[float]:
    """Pull the 0..1 score out of the judge's reply; None if it is unusable."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        score = float(json.loads(match.group(0)).get("score"))
    except (ValueError, TypeError, AttributeError):
        return None
    return max(0.0, min(1.0, score))


async def llm_judge(question: str, chunks: List[Dict[str, Any]], answer: str) -> Optional[float]:
    """Ask the configured model to score faithfulness; None without a key."""
    from app.core.config import settings
    from app.services.llm import LLMService, wrap_excerpt

    if settings.LLM_PROVIDER == "mock" or not settings.GROQ_API_KEY:
        return None
    excerpts = "\n\n".join(
        f"[{i}] " + wrap_excerpt(c.get("content") or "", c.get("filename") or "unknown")
        for i, c in enumerate(chunks, 1)
    )
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": f"QUESTION: {question}\n\nEXCERPTS:\n{excerpts}\n\nANSWER:\n{answer}"},
    ]
    try:
        completion, _model = await LLMService._complete(LLMService._client(), messages)
    except Exception as exc:
        logger.warning("The judge model failed", extra={"error": repr(exc)})
        return None
    choices = getattr(completion, "choices", None) or []
    return parse_judge_reply(choices[0].message.content if choices else "")


def format_report(report: EvalReport) -> str:
    """A plain-text table for the terminal."""

    def pct(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    lines = [
        f"Evaluation: {report.name}  (top_k={report.top_k}, {len(report.cases)} questions)",
        "",
        f"  retrieval hit rate   {pct(report.hit_rate)}",
        f"  MRR                  {report.mrr:.3f}",
        f"  answer keywords      {pct(report.keyword_rate)}",
        f"  faithfulness (judge) {pct(report.faithfulness)}",
        "",
    ]
    for case in report.cases:
        mark = "HIT " if case.hit else "MISS"
        rank = f"rank {case.rank}" if case.rank else "-"
        extra = ""
        if case.faithfulness is not None:
            extra = f"  faithful {case.faithfulness:.2f}"
        lines.append(f"  [{mark}] {case.id:<24} {rank:<8}{extra}  {case.question[:60]}")
    return "\n".join(lines)
