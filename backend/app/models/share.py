"""
share.py
========
WHAT THIS FILE DOES
-------------------
Defines `ShareLink` -- a read-only PUBLIC link to one piece of content:

    kind="message"   one assistant answer (and the question it answers)
    kind="session"   a whole conversation
    kind="project"   a project's name and its list of documents

Anyone holding the link can open /share/<token> without signing in. That is
the whole point -- sending an answer to a colleague outside the workspace, or
pasting it in a ticket -- and it is why everything about this table is
designed around the token never being recoverable from the database.

THE TOKEN IS NEVER STORED
-------------------------
The link carries a random 256-bit token. The database keeps only an HMAC of
it (services/sharing.py), keyed with the server's SECRET_KEY:

    * A leaked database backup holds no working links: the hash cannot be
      turned back into a token, and without SECRET_KEY it cannot even be
      checked against a guess.
    * The token is shown ONCE, when the link is created, exactly like an API
      key. A lost link is revoked and replaced, never "looked up".
    * Rotating SECRET_KEY invalidates every share link at once -- a useful
      emergency switch, and documented as such.

WHY THREE NULLABLE FOREIGN KEYS, NOT ONE "target_id"
----------------------------------------------------
One generic `target_id` column could point at anything, so the database
could not enforce that the target still exists. With a real foreign key per
kind -- exactly one of them set, matching `kind` -- ON DELETE CASCADE removes
the link the moment its answer, conversation or project is deleted. A
deleted conversation can never be resurrected through an old link.

`org_id` is copied onto the row as well: every management query filters on
it (the tenant boundary), and the public endpoint re-checks that the target
still belongs to it.
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base

# The three things a link can point at.
SHARE_KINDS = ("message", "session", "project")


class ShareLink(Base):
    """A read-only public link. Table name: "share_links"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # The tenant boundary: links are listed and revoked within one org.
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Who made it. SET NULL rather than CASCADE: removing a member should not
    # silently break every link they handed out -- an admin revokes those
    # deliberately.
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # "message" | "session" | "project" -- which of the three keys is set.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )

    # HMAC-SHA256 of the token, hex. Unique, and the only lookup key the
    # public endpoint has.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # The first characters of the token, so a list of links can tell them
    # apart ("dms_3fK9...") without revealing enough to open one.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # NULL = never expires. Checked on every view.
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when revoked. The row is kept (not deleted) so the audit trail and
    # the "revoked" state in the list still make sense afterwards.
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # How often it has been opened, and when last -- so an owner can see
    # whether a link is actually in use before revoking it.
    view_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_viewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def target_id(self) -> Optional[uuid.UUID]:
        """Whichever of the three foreign keys `kind` says is set."""
        return {
            "message": self.message_id,
            "session": self.session_id,
            "project": self.project_id,
        }.get(self.kind)
