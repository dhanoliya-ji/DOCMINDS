"""Turns a FOLLOW-UP question into a STANDALONE search query, using the conversation so far."""
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_REFERRING_WORDS = {
    "it", "its", "they", "them", "their", "this", "that", "these", "those",
    "he", "she", "him", "her", "his", "one", "ones", "same", "above",
    "there", "then", "also", "else",
}
_CONTINUATION_OPENERS = ("and ", "what about", "how about", "or ", "but ", "also ", "same for")

_SHORT_QUESTION_WORDS = 4

REWRITE_PROMPT = (
    "You rewrite a user's latest message into ONE standalone search query for a "
    "document search engine. Use the conversation only to fill in what the "
    "latest message refers to (pronouns, omitted subjects). Do not answer the "
    "question. Do not add facts. Reply with the query only, no quotes, at most "
    "25 words. If the message already stands on its own, repeat it unchanged."
)


@dataclass
class RewriteResult:
    """The query to search with, and what producing it cost."""

    query: str
    rewritten: bool = False
    method: str = "none"  # "none" | "heuristic" | "llm"
    prompt_tokens: int = 0
    completion_tokens: int = 0


def needs_context(question: str) -> bool:
    """True when a question probably depends on the conversation before it."""
    text = question.strip().lower()
    words = re.findall(r"[a-z']+", text)
    if len(words) <= _SHORT_QUESTION_WORDS:
        return True
    if text.startswith(_CONTINUATION_OPENERS):
        return True
    return any(word in _REFERRING_WORDS for word in words)


def last_user_question(history: Optional[List[Dict[str, str]]], current: str) -> Optional[str]:
    """The most recent EARLIER user message, skipping a copy of `current`."""
    for turn in reversed(history or []):
        content = (turn.get("content") or "").strip()
        if turn.get("role") == "user" and content and content != current.strip():
            return content
    return None


def heuristic_rewrite(question: str, history: Optional[List[Dict[str, str]]]) -> RewriteResult:
    """Prefix a context-dependent follow-up with the previous question."""
    previous = last_user_question(history, question)
    if not previous or not needs_context(question):
        return RewriteResult(query=question)
    return RewriteResult(
        query=f"{previous} {question.strip()}"[:2000], rewritten=True, method="heuristic"
    )


def _clean_model_output(text: str, fallback: str) -> str:
    """One line, no surrounding quotes, sane length -- or the fallback."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = line.strip().strip('"').strip("'").strip()
    if not line or len(line) > 500:
        return fallback
    return line


async def rewrite_query(
    question: str, history: Optional[List[Dict[str, str]]]
) -> RewriteResult:
    """The query to SEARCH with for `question`, given the earlier `history`."""
    if not settings.QUERY_REWRITE_ENABLED or not last_user_question(history, question):
        return RewriteResult(query=question)

    if not needs_context(question):
        return RewriteResult(query=question)

    if settings.LLM_PROVIDER == "mock" or not settings.GROQ_API_KEY:
        return heuristic_rewrite(question, history)

    from app.services.llm import LLMService, describe

    transcript = "\n".join(
        f"{turn['role'].upper()}: {(turn.get('content') or '')[:600]}"
        for turn in (history or [])[-6:]
        if turn.get("role") in ("user", "assistant")
    )
    messages = [
        {"role": "system", "content": REWRITE_PROMPT},
        {
            "role": "user",
            "content": f"CONVERSATION:\n{transcript}\n\nLATEST MESSAGE: {question}\n\nSTANDALONE QUERY:",
        },
    ]
    try:
        client = LLMService._client()
        completion, _model = await LLMService._complete(client, messages)
    except Exception as exc:
        logger.warning("Query rewrite failed; using the heuristic", extra={"error": describe(exc)})
        return heuristic_rewrite(question, history)

    choices = getattr(completion, "choices", None) or []
    text = (choices[0].message.content if choices else "") or ""
    usage = getattr(completion, "usage", None)
    query = _clean_model_output(text, question)
    return RewriteResult(
        query=query,
        rewritten=query != question,
        method="llm",
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )
