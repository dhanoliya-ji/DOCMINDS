"""
usage.py  (schemas)
===================
The response of GET /api/v1/org/usage: one organization's usage, estimated
cost and quotas for the current calendar month (UTC). Built by
services/usage.py:usage_summary.

Each quota is reported the same way, so a dashboard can draw all three bars
with one component:

    {"used": 412000, "quota": 1000000, "unlimited": false, "remaining": 588000}

A quota of 0 means unlimited; `unlimited` says so explicitly and `remaining`
is null, so no client has to know that convention.
"""

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
    # The period these numbers cover: [period_start, period_end).
    period_start: datetime.datetime
    period_end: datetime.datetime

    # Metered chat answers this month.
    requests: int
    # Tokens IN (prompts) and OUT (answers). Their sum is `tokens.used`.
    prompt_tokens: int
    completion_tokens: int
    # From LLM_PRICING. An estimate: the provider's invoice is the real number.
    estimated_cost_usd: float

    tokens: QuotaUsage
    documents: QuotaUsage
    storage_bytes: QuotaUsage

    by_model: List[ModelUsage]
    by_mode: List[ModeUsage]
