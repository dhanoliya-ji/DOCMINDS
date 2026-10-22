"""
usage.py  (service)
===================
WHAT THIS FILE DOES
-------------------
Metering and quotas: what each organization has used this month, what that is
estimated to cost, and whether it may use more.

    record_chat_usage(...)     stage a usage event for one chat answer
    check_token_quota(...)     refuse a chat when the month's tokens are gone
    upload_allowance(...)      refuse an upload that would cross the document
                               or storage quota
    usage_summary(...)         the month so far, for GET /org/usage
    estimate_cost_usd(...)     tokens -> dollars, from the LLM_PRICING table

WHY QUOTAS AT ALL
-----------------
Every organization shares the same model provider account and the same
server. Without a ceiling, one tenant running a script against chat can spend
the whole provider budget -- and exhaust the provider's rate limit, which
fails everyone else's answers too. Rate limits (core/rate_limit.py) cap the
SPEED of one user; quotas cap the monthly TOTAL of a whole organization.

TWO DIFFERENT "NO"s, ON PURPOSE
-------------------------------
    429 Too Many Requests   monthly token quota used up. It RESETS on the 1st
                            (UTC), so the reply carries a Retry-After header
                            counting down to it, like any other rate limit.
    402 Payment Required    document or storage quota full. Waiting will not
                            help -- nothing frees up by itself -- so this says
                            "you need a bigger plan", which is what 402 means.

THE LIMITS COME FROM TWO PLACES
-------------------------------
The QUOTA_* settings are the defaults for everyone. An organization row may
override any of them (models/organization.py): NULL there means "use the
setting", 0 means unlimited, anything else is that organization's own limit.

SOFT AT THE EDGE, HARD AFTER IT
-------------------------------
The token check runs BEFORE the model is called, against what has been used
so far. A request made with 100 tokens left is allowed, and may finish a few
hundred over -- we cannot know an answer's length before it is written. The
request after that is refused. The same is true of two uploads racing each
other. Being a few hundred tokens or one document over is the price of never
cutting an answer off half-way.
"""

import calendar
import datetime
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import Project
from app.models.usage import UsageEvent

logger = logging.getLogger(__name__)


class QuotaExceeded(Exception):
    """
    Raised when an organization is out of something. The API layer turns it
    into an HTTP error with exactly this status, message and headers.
    """

    def __init__(self, status_code: int, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.headers = {"Retry-After": str(retry_after)} if retry_after is not None else None


# ======================================================================
# PERIODS, LIMITS AND MONEY
# ======================================================================


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def month_bounds(now: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, datetime.datetime]:
    """
    The calendar month (UTC) containing `now`: [start, end).

    Calendar months rather than "the last 30 days" because that is what a
    person means by "this month's usage", and because a fixed reset date is
    something a 429 can promise: "try again on the 1st".
    """
    now = now or utcnow()
    start = datetime.datetime(now.year, now.month, 1, tzinfo=datetime.timezone.utc)
    days = calendar.monthrange(now.year, now.month)[1]
    return start, start + datetime.timedelta(days=days)


def _limit(override: Optional[int], default: int) -> int:
    """The organization's own limit if it has one, else the setting. 0 = unlimited."""
    return default if override is None else override


def token_quota(org: Optional[Organization]) -> int:
    return _limit(getattr(org, "monthly_token_quota", None), settings.QUOTA_MONTHLY_TOKENS)


def document_quota(org: Optional[Organization]) -> int:
    return _limit(getattr(org, "max_documents", None), settings.QUOTA_MAX_DOCUMENTS)


def storage_quota(org: Optional[Organization]) -> int:
    return _limit(getattr(org, "max_storage_bytes", None), settings.QUOTA_MAX_STORAGE_BYTES)


# Models already reported as missing from LLM_PRICING, so the warning is
# logged once per process rather than on every answer.
_unpriced_models: set = set()


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Dollars for one call: tokens x price per million, input and output
    priced separately.

        gpt-oss-120b, 3,000 in + 400 out
        = 3000 x $0.15/1M + 400 x $0.60/1M = $0.00045 + $0.00024 = $0.00069

    A model missing from LLM_PRICING -- including "mock" and "error" -- is
    costed at zero. For a real model that is logged once, so a forgotten
    price shows up in the logs rather than silently as free usage.
    """
    price = settings.LLM_PRICING.get(model)
    if price is None:
        if model not in ("mock", "error") and model not in _unpriced_models:
            _unpriced_models.add(model)
            logger.warning(
                "No price for this model in LLM_PRICING; its usage is costed at $0",
                extra={"model": model},
            )
        return 0.0
    cost = (
        prompt_tokens * float(price.get("input", 0.0))
        + completion_tokens * float(price.get("output", 0.0))
    ) / 1_000_000
    return round(cost, 8)


# ======================================================================
# RECORDING
# ======================================================================


def record_chat_usage(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    mode: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    request_id: Optional[str] = None,
) -> UsageEvent:
    """
    Stage one usage event in the CURRENT transaction (no commit).

    The chat endpoint commits it together with the question and the answer,
    so usage can never be recorded for an answer that was rolled back -- or
    an answer saved without its usage.
    """
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    # Providers report a total; if one did not, the parts add up to it.
    total_tokens = int(total_tokens or 0) or prompt_tokens + completion_tokens
    event = UsageEvent(
        org_id=org_id,
        user_id=user_id,
        event_type="chat",
        mode=mode,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
        request_id=request_id,
    )
    db.add(event)
    return event


# ======================================================================
# THE TOKEN QUOTA
# ======================================================================


async def tokens_used(db: AsyncSession, org_id: uuid.UUID, since: datetime.datetime) -> int:
    """Total tokens the organization has used since `since`."""
    result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.org_id == org_id, UsageEvent.created_at >= since
        )
    )
    return int(result.scalar_one() or 0)


async def check_token_quota(
    db: AsyncSession, org_id: uuid.UUID, now: Optional[datetime.datetime] = None
) -> None:
    """
    Raise QuotaExceeded (429) if the organization has used its monthly
    tokens. Call it BEFORE saving the question or calling the model.
    """
    quota = token_quota(await db.get(Organization, org_id))
    if quota == 0:
        return  # unlimited

    start, end = month_bounds(now)
    used = await tokens_used(db, org_id, start)
    if used < quota:
        return

    now = now or utcnow()
    logger.warning(
        "Monthly token quota exhausted", extra={"tokens_used": used, "token_quota": quota}
    )
    raise QuotaExceeded(
        429,
        (
            f"Your organization has used its monthly AI quota of {quota:,} tokens. "
            f"It resets on {end:%B} 1 (UTC). Search and documents still work "
            "in the meantime."
        ),
        # Seconds until midnight UTC on the 1st. Long, but true -- and a
        # client that honours Retry-After will not hammer us until then.
        retry_after=max(1, int((end - now).total_seconds())),
    )


# ======================================================================
# THE DOCUMENT AND STORAGE QUOTAS
# ======================================================================


async def stored_documents(db: AsyncSession, org_id: uuid.UUID) -> Tuple[int, int]:
    """
    (document count, total bytes) across every project in the organization.

    Every row counts whatever its status -- a failed document still has its
    file in storage until someone removes it.
    """
    result = await db.execute(
        select(func.count(Document.id), func.coalesce(func.sum(Document.file_size), 0))
        .join(Project, Document.project_id == Project.id)
        .where(Project.org_id == org_id)
    )
    count, size = result.one()
    return int(count or 0), int(size or 0)


def format_bytes(size: int) -> str:
    """5368709120 -> "5.0 GB" (binary units, as storage is usually shown)."""
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - the loop always returns


@dataclass
class UploadAllowance:
    """
    The room an organization has left, consumed file by file as an upload is
    accepted. The upload endpoint calls `admit` for each new (non-duplicate)
    file BEFORE storing it, so a refused upload stores nothing at all.
    """

    documents: int
    bytes_used: int
    max_documents: int
    max_bytes: int

    def admit(self, size: int) -> None:
        if self.max_documents and self.documents + 1 > self.max_documents:
            raise QuotaExceeded(
                402,
                (
                    f"Your organization has reached its limit of {self.max_documents:,} "
                    "documents. Ask the service operator to raise it."
                ),
            )
        if self.max_bytes and self.bytes_used + size > self.max_bytes:
            raise QuotaExceeded(
                402,
                (
                    "This upload would take your organization over its storage limit of "
                    f"{format_bytes(self.max_bytes)} ({format_bytes(self.bytes_used)} "
                    "already used). Ask the service operator to raise it."
                ),
            )
        self.documents += 1
        self.bytes_used += size


async def upload_allowance(db: AsyncSession, org_id: uuid.UUID) -> UploadAllowance:
    """What the organization holds now, and its limits."""
    org = await db.get(Organization, org_id)
    count, size = await stored_documents(db, org_id)
    return UploadAllowance(
        documents=count,
        bytes_used=size,
        max_documents=document_quota(org),
        max_bytes=storage_quota(org),
    )


# ======================================================================
# THE SUMMARY (GET /org/usage)
# ======================================================================


def _quota_view(used: int, quota: int) -> Dict[str, Any]:
    """One quota as the API reports it. 0 means unlimited, said explicitly."""
    return {
        "used": used,
        "quota": quota,
        "unlimited": quota == 0,
        "remaining": None if quota == 0 else max(0, quota - used),
    }


async def usage_summary(
    db: AsyncSession, org_id: uuid.UUID, now: Optional[datetime.datetime] = None
) -> Dict[str, Any]:
    """This calendar month's usage, cost and quotas for one organization."""
    start, end = month_bounds(now)
    org = await db.get(Organization, org_id)
    in_period = (
        UsageEvent.org_id == org_id,
        UsageEvent.created_at >= start,
        UsageEvent.created_at < end,
    )

    model_rows = await db.execute(
        select(
            UsageEvent.model,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.estimated_cost_usd), 0),
        )
        .where(*in_period)
        .group_by(UsageEvent.model)
        .order_by(func.sum(UsageEvent.total_tokens).desc(), UsageEvent.model)
    )
    by_model: List[Dict[str, Any]] = [
        {
            "model": model,
            "requests": int(requests),
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
            "estimated_cost_usd": round(float(cost), 6),
        }
        for model, requests, prompt, completion, total, cost in model_rows.all()
    ]

    mode_rows = await db.execute(
        select(
            UsageEvent.mode,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
        )
        .where(*in_period)
        .group_by(UsageEvent.mode)
        .order_by(UsageEvent.mode)
    )
    by_mode = [
        {"mode": mode or "unknown", "requests": int(requests), "total_tokens": int(total)}
        for mode, requests, total in mode_rows.all()
    ]

    prompt_total = sum(row["prompt_tokens"] for row in by_model)
    completion_total = sum(row["completion_tokens"] for row in by_model)
    token_total = sum(row["total_tokens"] for row in by_model)
    documents, storage = await stored_documents(db, org_id)

    return {
        "period_start": start,
        "period_end": end,
        "requests": sum(row["requests"] for row in by_model),
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in by_model), 6),
        "tokens": _quota_view(token_total, token_quota(org)),
        "documents": _quota_view(documents, document_quota(org)),
        "storage_bytes": _quota_view(storage, storage_quota(org)),
        "by_model": by_model,
        "by_mode": by_mode,
    }
