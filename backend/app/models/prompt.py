"""
prompt.py
=========
WHAT THIS FILE DOES
-------------------
Defines `PromptTemplate` -- one saved prompt in the prompt library: a title
("Summarise the risks") and the text that is inserted into the chat box.

TWO SCOPES
----------
    personal   visible to the person who saved it, and nobody else
    org        visible to everyone in the organization -- the team's shared
               library. Creating or changing one needs the editor role, the
               same people who curate the documents it is asked about.

The handful of BUILT-IN templates (summarise risks, extract dates, compare
clauses) are not rows: they live in code (services/prompt_library.py), so
every organization has them from its first minute and an upgrade can improve
them without a data migration.
"""

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

PROMPT_SCOPES = ("personal", "org")


class PromptTemplate(Base):
    """A saved prompt. Table name: "prompt_templates"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # The tenant boundary. Every query filters on it.
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Who saved it. CASCADE: a personal template is meaningless without its
    # owner, and an org template whose author left is deleted with them too
    # -- simpler than an ownerless row nobody may edit. (Deactivating a member
    # keeps the row; only removing them deletes it.)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # "personal" | "org"
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")

    title: Mapped[str] = mapped_column(String(120), nullable=False)

    # The text inserted into the chat input. Capped at the chat message
    # limit (4000) by the schema, so an inserted template can always be sent.
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
