"""
user.py
=======
WHAT THIS FILE DOES
-------------------
Defines the USER table -- someone who can log in.

Every user belongs to exactly one Organization. That single `org_id` column is
what the whole tenant isolation rests on: it is the value compared against in
every search query in services/retrieval.py.

THE THREE ROLES
---------------
    Admin     (admin)   full control, including members, API keys, audit log.
    Manager   (editor)  can create projects and upload/delete documents.
    Employee  (viewer)  can read, search and ask questions.

The roles are RANKED (core/roles.py), and endpoints name the lowest role they
accept, for example:

        Depends(deps.require_role("editor"))     # Manager or Admin

A NOTE ON THE PASSWORD COLUMN
-----------------------------
The column is `hashed_password`, and the naming is deliberate. The plain
password is never stored, never logged and never returned by the API. Only a
bcrypt hash -- a one-way fingerprint -- is kept. See core/security.py.

The API can never leak it either: the `UserOut` schema in schemas/user.py does
not list the field, and Pydantic returns only the fields it declares.
"""

import datetime
import uuid
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.api_key import APIKey
    from app.models.audit import AuditLog
    from app.models.chat import ChatSession
    from app.models.organization import Organization


class User(Base):
    """A person who can sign in. Table name: "users"."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # `unique=True` is enforced by the DATABASE, not just by the signup check.
    # That distinction matters: two signup requests arriving at the same instant
    # could both pass an application-level "does this email exist?" test, and
    # only the database constraint reliably stops the duplicate.
    #
    # `index=True` because every login looks a user up by email, and without an
    # index that means scanning the entire table.
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    # The bcrypt hash. 255 characters is ample -- bcrypt hashes are 60.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Admin | Manager | Employee. Stored as a string rather than a database
    # ENUM because adding a new role to an ENUM requires a migration, whereas
    # this only needs a code change. The allowed values are enforced by the
    # Pydantic schema on the way in.
    role: Mapped[str] = mapped_column(
        String(50), default="Employee", nullable=False
    )

    # >>> THE TENANT BOUNDARY <<<
    # This column is copied into the user's JWT at login and then compared
    # against `projects.org_id` in every retrieval query. It is the single most
    # security-critical field in the schema.
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Lets an account be disabled without deleting it -- which preserves their
    # audit history and authorship. Checked by `get_current_active_user`, so
    # deactivation takes effect immediately even while their token is still
    # within its lifetime.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Set when the user clicks the link in their verification email (or
    # immediately, when they joined through an emailed invite -- clicking the
    # invite link already proved they own the address). Only REQUIRED for
    # sign-in when settings.REQUIRE_EMAIL_VERIFICATION is on.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # When the password was last changed (by reset). Access tokens issued
    # BEFORE this moment are refused by get_current_user, so resetting a
    # password signs out every session that used the old one -- including a
    # thief's, which is usually why someone resets a password.
    password_changed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # NOT A COLUMN. Set by get_current_user when the request authenticated
    # with an API key rather than a password-derived token, and holds that
    # key's role. Role checks use the LOWER of this and `role`, so a key
    # created as "viewer" cannot be used to upload, even by an Admin's key.
    # A plain class attribute without `Mapped[...]` is ignored by SQLAlchemy.
    api_key_role = None

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # ---- Relationships -------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="users"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )
    # NOT "delete-orphan", unlike the relationships around it. The audit
    # table's foreign key is ON DELETE SET NULL precisely so that removing a
    # user keeps the record of what they did (see models/audit.py). An ORM
    # cascade here would quietly delete those rows first and defeat it;
    # `passive_deletes=True` leaves the job to the database.
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="user", cascade="save-update, merge", passive_deletes=True
    )
    chat_sessions: Mapped[List["ChatSession"]] = relationship(
        "ChatSession", back_populates="user", cascade="all, delete-orphan"
    )
