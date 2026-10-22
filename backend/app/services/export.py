"""
export.py
=========
WHAT THIS FILE DOES
-------------------
Turns a conversation into a Markdown document, for
GET /chat/sessions/{id}/export.

    # Leave policy questions

    _Exported from DocMinds on 2026-10-23 14:02 UTC_

    ## Question
    How many days of annual leave do I get?

    ## Answer  (Quick answer · openai/gpt-oss-120b)
    Full-time staff get 25 days [1].

    **Sources**
    1. handbook.pdf, page 12 -- "Employees accrue 1.75 days..."

WHY MARKDOWN
------------
It is readable as plain text, renders nicely almost everywhere (GitHub, Notion,
Confluence, Obsidian, most chat tools), and it is what the model already
writes its answers in -- so the answers keep their lists and bold text with no
conversion at all.

The browser has its own copy of this logic (frontend/lib/export.ts) so it can
export without a round trip; this one is for API clients and scripts. The two
produce the same layout.
"""

import datetime
from typing import Any, Dict, Iterable, List, Optional

MODE_LABELS = {"rag": "Quick answer", "agent": "Deep research"}


def _quote(text: str, limit: int = 200) -> str:
    """A snippet on one line, shortened, safe inside double quotes."""
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "..."
    return flat.replace('"', "'")


def citation_line(citation: Dict[str, Any]) -> str:
    """`1. handbook.pdf, page 12 -- "snippet..."`"""
    where = citation.get("filename") or "unknown file"
    if citation.get("page_number"):
        where += f", page {citation['page_number']}"
    snippet = _quote(citation.get("snippet") or "")
    return f"{citation.get('number', '?')}. {where}" + (f' -- "{snippet}"' if snippet else "")


def answer_heading(mode: Optional[str], model: Optional[str]) -> str:
    """`## Answer  (Quick answer · model)` -- the parts that are known."""
    details = [part for part in (MODE_LABELS.get(mode or "", mode), model) if part]
    return "## Answer" + (f"  ({' · '.join(details)})" if details else "")


def message_to_markdown(message: Dict[str, Any]) -> str:
    """One message as a Markdown section."""
    if message.get("role") == "user":
        return f"## Question\n\n{(message.get('content') or '').strip()}\n"

    parts: List[str] = [
        answer_heading(message.get("mode"), message.get("model")),
        "",
        (message.get("content") or "").strip(),
        "",
    ]
    citations = message.get("citations") or []
    if citations:
        parts.append("**Sources**")
        parts.append("")
        parts.extend(citation_line(citation) for citation in citations)
        parts.append("")
    return "\n".join(parts)


def conversation_to_markdown(
    title: str,
    messages: Iterable[Dict[str, Any]],
    exported_at: Optional[datetime.datetime] = None,
) -> str:
    """A whole conversation, oldest message first."""
    exported_at = exported_at or datetime.datetime.now(datetime.timezone.utc)
    header = [
        f"# {(title or 'Conversation').strip()}",
        "",
        f"_Exported from DocMinds on {exported_at.strftime('%Y-%m-%d %H:%M')} UTC_",
        "",
    ]
    body = [message_to_markdown(message) for message in messages]
    return "\n".join(header) + "\n".join(body).rstrip() + "\n"


def safe_filename(title: str, extension: str = "md") -> str:
    """A download name built from the title: letters, digits, dashes only."""
    stem = "".join(ch if ch.isalnum() else "-" for ch in (title or "").lower())
    stem = "-".join(part for part in stem.split("-") if part)[:60] or "conversation"
    return f"{stem}.{extension}"
