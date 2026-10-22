"""
usage.py  (database model)
==========================
WHAT THIS FILE DOES
-------------------
Defines the USAGE_EVENTS table: one row per metered action. Today that is one
row per chat answer, recording

    which organization and user asked
    which model answered, in which mode (rag or agent)
    tokens IN (the prompt: excerpts, history, question)
    tokens OUT (the answer the model wrote)
    the estimated cost of those tokens, in US dollars

WHY TOKENS IN AND OUT SEPARATELY
--------------------------------
Providers price them differently -- output usually costs four or five times
as much as input -- so "1,000 tokens" alone cannot be turned into money. The
split also says where the cost comes from: a huge input count means too many
or too long excerpts are being sent (RETRIEVAL_TOP_K, chunk size); a huge
output count means long answers.

WHY A ROW PER EVENT RATHER THAN A RUNNING TOTAL
-----------------------------------------------
A counter column ("tokens_this_month") is cheaper to read, but it can only
ever answer the one question it was built for, and a bug that double-counts
destroys the evidence of itself. Individual rows can be summed by month, by
model, by mode or by user after the fact, and audited one by one. The
composite index on (org_id, created_at) keeps "this organization, this month"
a short index range scan -- the quota check runs it on every chat request.

THE SAME DELETE RULES AS THE AUDIT LOG
--------------------------------------
    organization deleted -> its usage goes too (CASCADE): there is nobody
                            left to bill.
    user deleted         -> the rows STAY, with user_id set to NULL. The
                            organization still consumed those tokens, and its
                            month's total must not drop because a member left.
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class UsageEvent(Base):
    """One metered action. Table name: "usage_events"."""

    __table_args__ = (
        # Serves every query this table gets: one organization's events in a
        # date range (the quota check, the usage summary).
        Index("ix_usage_events_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, not CASCADE -- see the module docstring. Indexed because
    # deleting a user makes Postgres find every row pointing at them.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # What was metered. "chat" today; the column leaves room for more
    # ("embedding", "ocr") without a migration.
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "rag" or "agent" for chat events.
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # The model that actually answered -- the fallback model's name if the
    # main one failed, "mock" in demo mode, "error" if no model answered.
    model: Mapped[str] = mapped_column(String(255), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    # Money is stored as an exact decimal, never a float: 0.1 + 0.2 is not
    # 0.3 in floating point, and those errors add up over a million rows.
    # Eight decimal places, because one small request costs a few millionths
    # of a dollar. `asdecimal=False` hands the value to Python as a float,
    # which is what the JSON response wants anyway.
    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(14, 8, asdecimal=False), default=0, server_default="0", nullable=False
    )

    # The id of the HTTP request that caused this (core/middleware.py), so a
    # surprising row can be traced to its log lines.
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
