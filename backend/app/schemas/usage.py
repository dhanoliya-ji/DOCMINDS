"""The response of GET /api/v1/org/usage: one organization's usage, estimated cost and quotas for the current..."""
import datetime
from typing import List, Optional

from pydantic import BaseModel


class QuotaUsage(BaseModel):
    used: int
    quota: int
    unlimited: bool
    remaining: Optional[int] = None


class ModelUsage(BaseModel):
    """One model's share of the month."""

    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class ModeUsage(BaseModel):
    """One chat mode's share of the month ("rag" or "agent")."""

    mode: str
    requests: int
    total_tokens: int


class UsageSummary(BaseModel):
    period_start: datetime.datetime
    period_end: datetime.datetime

    requests: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    tokens: QuotaUsage
    documents: QuotaUsage
    storage_bytes: QuotaUsage

    by_model: List[ModelUsage]
    by_mode: List[ModeUsage]
