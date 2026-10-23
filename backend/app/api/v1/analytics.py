"""
analytics.py
============
WHAT THIS FILE DOES
-------------------
The admin dashboard's aggregates (services/analytics.py), for org admins:

    GET /analytics/overview?days=30        everything below, in one request
    GET /analytics/usage?days=30           answers, tokens, cost per day
    GET /analytics/documents?days=30       processed / failed per day
    GET /analytics/active-users?days=30    distinct active members per day
    GET /analytics/feedback?days=30        thumbs up / down per day
    GET /analytics/top-questions?days=30   the most-asked questions

`days` is 1-365 and always includes today (UTC). Every route answers 404
while ADMIN_DASHBOARD_ENABLED is off. Read-only: nothing here is audited.
"""

import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.schemas.integrations import (
    AnalyticsOverview,
    DailyDocuments,
    DailyFeedback,
    DailyUsage,
    DailyUsers,
    TopQuestion,
)
from app.services import analytics


def _require_enabled() -> None:
    if not settings.ADMIN_DASHBOARD_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(dependencies=[Depends(_require_enabled), Depends(deps.require_admin)])

Days = Query(30, ge=1, le=365, description="Look back this many days, today included.")


def _window(days: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = (now - datetime.timedelta(days=days - 1)).date()
    since = datetime.datetime(start.year, start.month, start.day, tzinfo=datetime.timezone.utc)
    return since, analytics.day_range(since, now)


@router.get("/overview", response_model=AnalyticsOverview)
async def get_overview(days: int = Days, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.get_current_active_user)):
    return await analytics.overview(db, current_user.org_id, days)


@router.get("/usage", response_model=List[DailyUsage])
async def get_usage(days: int = Days, db: AsyncSession = Depends(deps.get_db),
                    current_user: User = Depends(deps.get_current_active_user)):
    since, series = _window(days)
    return await analytics.usage_daily(db, current_user.org_id, since, series)


@router.get("/documents", response_model=List[DailyDocuments])
async def get_documents(days: int = Days, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.get_current_active_user)):
    since, series = _window(days)
    return await analytics.documents_daily(db, current_user.org_id, since, series)


@router.get("/active-users", response_model=List[DailyUsers])
async def get_active_users(days: int = Days, db: AsyncSession = Depends(deps.get_db),
                           current_user: User = Depends(deps.get_current_active_user)):
    since, series = _window(days)
    daily, _total = await analytics.users_daily(db, current_user.org_id, since, series)
    return daily


@router.get("/feedback", response_model=List[DailyFeedback])
async def get_feedback(days: int = Days, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.get_current_active_user)):
    since, series = _window(days)
    return await analytics.feedback_daily(db, current_user.org_id, since, series)


@router.get("/top-questions", response_model=List[TopQuestion])
async def get_top_questions(days: int = Days, limit: int = Query(10, ge=1, le=50),
                            db: AsyncSession = Depends(deps.get_db),
                            current_user: User = Depends(deps.get_current_active_user)):
    since, _series = _window(days)
    return await analytics.top_questions(db, current_user.org_id, since, limit)
