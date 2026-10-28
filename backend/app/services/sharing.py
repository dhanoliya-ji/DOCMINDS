"""Everything about public share links that is not HTTP: minting a token, hashing it, deciding whether a link..."""
import datetime
import hashlib
import hmac
import secrets
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document
from app.models.project import Project
from app.models.share import ShareLink

TOKEN_PREFIX = "dms_"
DISPLAY_PREFIX_LENGTH = 10

MAX_SHARED_MESSAGES = 200
MAX_SHARED_DOCUMENTS = 200


def new_token() -> str:
    """A fresh link token: 256 bits of randomness, URL-safe."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """The only form of the token the database ever holds."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """SQLite hands timestamps back without a timezone; Postgres with UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


def status_of(link: ShareLink, now: Optional[datetime.datetime] = None) -> str:
    """"active", "revoked" or "expired"."""
    if link.revoked_at is not None:
        return "revoked"
    expires = _aware(link.expires_at)
    if expires is not None and expires <= (now or _now()):
        return "expired"
    return "active"


async def resolve(db: AsyncSession, token: str) -> Optional[ShareLink]:
    """The live link for `token`, or None."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        return None
    link = (
        await db.execute(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    ).scalar_one_or_none()
    if link is None or status_of(link) != "active":
        return None
    return link


def _public_citations(citations: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """A citation without its ids: what a visitor needs to read it, no more."""
    return [
        {
            "number": c.get("number"),
            "filename": c.get("filename"),
            "page_number": c.get("page_number"),
            "score": c.get("score"),
            "snippet": c.get("snippet"),
        }
        for c in citations or []
    ]


def _public_message(message: ChatMessage) -> Dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "citations": _public_citations(message.citations) if message.role == "assistant" else [],
        "mode": message.mode if message.role == "assistant" else None,
        "confidence": message.confidence if message.role == "assistant" else None,
        "created_at": message.created_at,
    }


async def _transcript(db: AsyncSession, session_id: uuid.UUID) -> List[ChatMessage]:
    rows = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.role.desc())
        .limit(MAX_SHARED_MESSAGES)
    )
    return list(rows.scalars().all())


async def _session_in_org(
    db: AsyncSession, session_id: uuid.UUID, org_id: uuid.UUID
) -> Optional[ChatSession]:
    """The session, but only while its project is still in the link's org."""
    return (
        await db.execute(
            select(ChatSession)
            .join(Project, Project.id == ChatSession.project_id)
            .where(ChatSession.id == session_id, Project.org_id == org_id)
        )
    ).scalar_one_or_none()


async def public_view(db: AsyncSession, link: ShareLink) -> Optional[Dict[str, Any]]:
    """What a visitor to /share/<token> sees, or None if the target is gone."""
    base = {"kind": link.kind, "created_at": link.created_at, "expires_at": link.expires_at}

    if link.kind == "message":
        message = await db.get(ChatMessage, link.message_id)
        if message is None or message.role != "assistant":
            return None
        session = await _session_in_org(db, message.session_id, link.org_id)
        if session is None:
            return None
        messages = await _transcript(db, session.id)
        question: Optional[ChatMessage] = None
        for candidate in messages:
            if candidate.id == message.id:
                break
            if candidate.role == "user":
                question = candidate
        shown = ([question] if question is not None else []) + [message]
        return {**base, "title": session.title, "messages": [_public_message(m) for m in shown]}

    if link.kind == "session":
        session = await _session_in_org(db, link.session_id, link.org_id)
        if session is None:
            return None
        messages = await _transcript(db, session.id)
        return {**base, "title": session.title, "messages": [_public_message(m) for m in messages]}

    if link.kind == "project":
        project = await db.get(Project, link.project_id)
        if project is None or project.org_id != link.org_id:
            return None
        documents = (
            await db.execute(
                select(Document)
                .where(Document.project_id == project.id, Document.status == "completed")
                .order_by(Document.filename)
                .limit(MAX_SHARED_DOCUMENTS)
            )
        ).scalars().all()
        return {
            **base,
            "title": project.name,
            "documents": [
                {
                    "filename": d.filename,
                    "file_type": d.file_type,
                    "page_count": (d.meta_data or {}).get("page_count"),
                    "word_count": (d.meta_data or {}).get("word_count"),
                    "created_at": d.created_at,
                }
                for d in documents
            ],
        }
    return None


async def record_view(db: AsyncSession, link: ShareLink) -> None:
    """Count one visit. The caller commits."""
    link.view_count = int(link.view_count or 0) + 1
    link.last_viewed_at = _now()
