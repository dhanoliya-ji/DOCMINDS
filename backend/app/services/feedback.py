"""Summarises the thumbs up / thumbs down people gave to answers, for the admin endpoint GET /org/feedback..."""
import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.project import Project

RECENT_NEGATIVE_LIMIT = 20


def _scoped(statement, org_id: uuid.UUID, since: Optional[datetime.datetime]):
    """The joins and the tenant filter every query here shares."""
    statement = (
        statement.join(ChatMessage, Feedback.message_id == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .join(Project, ChatSession.project_id == Project.id)
        .where(Project.org_id == org_id)
    )
    if since is not None:
        statement = statement.where(Feedback.created_at >= since)
    return statement


def satisfaction(up: int, down: int) -> Optional[float]:
    """up / (up + down), rounded; None when nobody has voted."""
    votes = up + down
    return round(up / votes, 4) if votes else None


async def summarise(
    db: AsyncSession,
    org_id: uuid.UUID,
    since: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    up_count = func.sum(case((Feedback.rating > 0, 1), else_=0))
    down_count = func.sum(case((Feedback.rating < 0, 1), else_=0))

    total, up, down = (
        await db.execute(_scoped(select(func.count(Feedback.id), up_count, down_count), org_id, since))
    ).one()
    up, down = int(up or 0), int(down or 0)

    async def breakdown(column) -> List[Dict[str, Any]]:
        key = func.coalesce(column, "unknown")
        rows = (
            await db.execute(
                _scoped(select(key, up_count, down_count), org_id, since)
                .group_by(key)
                .order_by(key)
            )
        ).all()
        return [{"key": k, "up": int(u or 0), "down": int(d or 0)} for k, u, d in rows]

    negative_rows = (
        await db.execute(
            _scoped(select(Feedback, ChatMessage), org_id, since)
            .where(Feedback.rating < 0)
            .order_by(Feedback.created_at.desc())
            .limit(RECENT_NEGATIVE_LIMIT)
        )
    ).all()

    recent_negative = []
    for feedback, message in negative_rows:
        question = (
            await db.execute(
                select(ChatMessage.content)
                .where(
                    ChatMessage.session_id == message.session_id,
                    ChatMessage.role == "user",
                    ChatMessage.created_at <= message.created_at,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        recent_negative.append(
            {
                "message_id": message.id,
                "session_id": message.session_id,
                "rating": feedback.rating,
                "comment": feedback.comment,
                "question": question,
                "answer": message.content,
                "mode": message.mode,
                "model": message.model,
                "created_at": feedback.created_at,
            }
        )

    return {
        "total": int(total or 0),
        "up": up,
        "down": down,
        "satisfaction": satisfaction(up, down),
        "by_mode": await breakdown(ChatMessage.mode),
        "by_model": await breakdown(ChatMessage.model),
        "recent_negative": recent_negative,
    }
