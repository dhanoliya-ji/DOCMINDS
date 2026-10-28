"""The numbers behind Settings -> Admin dashboard, for ONE organization over the last N days, each as a..."""
import datetime
import uuid
from typing import Any, Dict, List

from sqlalchemy import case, distinct, func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.document import Document
from app.models.project import Project
from app.models.usage import UsageEvent

TOP_QUESTIONS = 10


def _day(value: Any) -> str:
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)[:10]


def day_range(since: datetime.datetime, now: datetime.datetime) -> List[str]:
    days = []
    current = since.date()
    while current <= now.date():
        days.append(current.isoformat())
        current += datetime.timedelta(days=1)
    return days


def _fill(days: List[str], rows: Dict[str, Dict[str, Any]], zero: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"date": day, **rows.get(day, zero)} for day in days]


async def usage_daily(db: AsyncSession, org_id: uuid.UUID, since, days: List[str]):
    day = func.date(UsageEvent.created_at)
    rows = await db.execute(
        select(
            day,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.estimated_cost_usd), 0),
        )
        .where(UsageEvent.org_id == org_id, UsageEvent.created_at >= since)
        .group_by(day)
    )
    found = {
        _day(d): {"requests": int(n), "tokens": int(t), "cost_usd": round(float(c), 6)}
        for d, n, t, c in rows.all()
    }
    return _fill(days, found, {"requests": 0, "tokens": 0, "cost_usd": 0.0})


async def documents_daily(db: AsyncSession, org_id: uuid.UUID, since, days: List[str]):
    day = func.date(Document.updated_at)
    rows = await db.execute(
        select(
            day,
            func.sum(case((Document.status == "completed", 1), else_=0)),
            func.sum(case((Document.status == "failed", 1), else_=0)),
        )
        .join(Project, Document.project_id == Project.id)
        .where(Project.org_id == org_id, Document.updated_at >= since)
        .group_by(day)
    )
    found = {_day(d): {"processed": int(p or 0), "failed": int(f or 0)} for d, p, f in rows.all()}
    return _fill(days, found, {"processed": 0, "failed": 0})


async def users_daily(db: AsyncSession, org_id: uuid.UUID, since, days: List[str]):
    activity = union(
        select(func.date(UsageEvent.created_at).label("day"), UsageEvent.user_id.label("user_id")).where(
            UsageEvent.org_id == org_id, UsageEvent.created_at >= since, UsageEvent.user_id.is_not(None)
        ),
        select(func.date(AuditLog.created_at).label("day"), AuditLog.user_id.label("user_id")).where(
            AuditLog.org_id == org_id, AuditLog.created_at >= since, AuditLog.user_id.is_not(None)
        ),
    ).subquery()
    rows = await db.execute(
        select(activity.c.day, func.count(distinct(activity.c.user_id))).group_by(activity.c.day)
    )
    found = {_day(d): {"active_users": int(n)} for d, n in rows.all()}
    total = (
        await db.execute(select(func.count(distinct(activity.c.user_id))))
    ).scalar_one()
    return _fill(days, found, {"active_users": 0}), int(total or 0)


async def feedback_daily(db: AsyncSession, org_id: uuid.UUID, since, days: List[str]):
    day = func.date(Feedback.created_at)
    rows = await db.execute(
        select(
            day,
            func.sum(case((Feedback.rating > 0, 1), else_=0)),
            func.sum(case((Feedback.rating < 0, 1), else_=0)),
        )
        .join(ChatMessage, Feedback.message_id == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .join(Project, ChatSession.project_id == Project.id)
        .where(Project.org_id == org_id, Feedback.created_at >= since)
        .group_by(day)
    )
    found = {_day(d): {"up": int(u or 0), "down": int(n or 0)} for d, u, n in rows.all()}
    return _fill(days, found, {"up": 0, "down": 0})


async def top_questions(db: AsyncSession, org_id: uuid.UUID, since, limit: int = TOP_QUESTIONS):
    normalised = func.lower(func.trim(ChatMessage.content))
    rows = await db.execute(
        select(func.min(ChatMessage.content), func.count(ChatMessage.id))
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .join(Project, ChatSession.project_id == Project.id)
        .where(Project.org_id == org_id, ChatMessage.role == "user", ChatMessage.created_at >= since)
        .group_by(normalised)
        .order_by(func.count(ChatMessage.id).desc(), normalised)
        .limit(limit)
    )
    return [{"question": (q or "").strip()[:500], "count": int(n)} for q, n in rows.all()]


async def overview(db: AsyncSession, org_id: uuid.UUID, days: int, now=None) -> Dict[str, Any]:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start_day = (now - datetime.timedelta(days=days - 1)).date()
    since = datetime.datetime(start_day.year, start_day.month, start_day.day, tzinfo=datetime.timezone.utc)
    series = day_range(since, now)

    usage = await usage_daily(db, org_id, since, series)
    documents = await documents_daily(db, org_id, since, series)
    active, active_total = await users_daily(db, org_id, since, series)
    feedback = await feedback_daily(db, org_id, since, series)
    up = sum(d["up"] for d in feedback)
    down = sum(d["down"] for d in feedback)
    return {
        "days": days,
        "since": since,
        "totals": {
            "requests": sum(d["requests"] for d in usage),
            "tokens": sum(d["tokens"] for d in usage),
            "cost_usd": round(sum(d["cost_usd"] for d in usage), 6),
            "documents_processed": sum(d["processed"] for d in documents),
            "documents_failed": sum(d["failed"] for d in documents),
            "active_users": active_total,
            "feedback_up": up,
            "feedback_down": down,
            "satisfaction": round(up / (up + down), 4) if up + down else None,
        },
        "usage": usage,
        "documents": documents,
        "active_users": active,
        "feedback": feedback,
        "top_questions": await top_questions(db, org_id, since),
    }
