"""
account.py  (schemas)
=====================
WHAT THIS FILE DOES
-------------------
Request and response shapes for everything an organization's members and
Admins manage about ACCOUNTS -- as opposed to documents or chat:

    password reset / email verification     (api/v1/auth.py)
    members, invites, API keys, audit log   (api/v1/org.py)

THE RULE THAT SHAPES EVERY "OUT" SCHEMA HERE
--------------------------------------------
Secrets appear in a response AT MOST ONCE, at the moment they are created:

    InviteCreated.token      the invite token, so the Admin can copy the link
    APIKeyCreated.key        the API key itself

After that, `InviteOut` and `APIKeyOut` -- what the list endpoints return --
have no field that could hold one. The server could not fill it anyway: only
hashes are stored.
"""

import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# The roles a request may name. The generic aliases are accepted too and
# normalised by the endpoint (see core/roles.py).
RoleName = Literal["Admin", "Manager", "Employee", "admin", "editor", "viewer"]


# ======================================================================
# Password reset and email verification
# ======================================================================


class PasswordResetRequest(BaseModel):
    """Body for POST /auth/password-reset/request."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Body for POST /auth/password-reset/confirm."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str


class EmailVerifyRequest(BaseModel):
    """Body for POST /auth/verify-email."""

    token: str = Field(min_length=1, max_length=256)


class Message(BaseModel):
    """A plain acknowledgement, for endpoints whose result is just "done"."""

    detail: str


class InvitePreview(BaseModel):
    """
    What GET /auth/invites/{token} tells a not-yet-signed-up visitor, so the
    signup page can say "Join Acme as Editor" and prefill the email.
    """

    email: str
    role: str
    org_name: str
    expires_at: datetime.datetime


# ======================================================================
# Members
# ======================================================================


class MemberOut(BaseModel):
    """One member of the caller's organization."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime.datetime


class MemberUpdate(BaseModel):
    """Body for PATCH /org/members/{id}. Send only what should change."""

    role: Optional[RoleName] = None
    is_active: Optional[bool] = None


# ======================================================================
# Invites
# ======================================================================


class InviteCreate(BaseModel):
    """Body for POST /org/invites."""

    email: EmailStr
    # Viewer by default: the least privilege is the safe thing to forget to
    # change.
    role: RoleName = "Employee"


class InviteOut(BaseModel):
    """An invite as the invites list shows it. No token -- see the module docstring."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime.datetime
    accepted_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    invited_by: Optional[uuid.UUID] = None
    created_at: datetime.datetime
    # "pending" | "accepted" | "revoked" | "expired", worked out by the endpoint
    # so every client does not have to re-derive it from three timestamps.
    status: str = "pending"


class InviteCreated(InviteOut):
    """
    The response to creating an invite. The ONLY time the token is returned.

    The token is also emailed; returning it as well lets an Admin copy the
    link and send it some other way when email is not configured.
    """

    token: str
    invite_url: str


# ======================================================================
# API keys
# ======================================================================


class APIKeyCreate(BaseModel):
    """Body for POST /org/api-keys."""

    name: str = Field(min_length=1, max_length=255)
    # Least privilege by default. Capped at the creator's own role.
    role: RoleName = "Employee"
    # Optional lifetime in days; omitted means the key never expires.
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class APIKeyOut(BaseModel):
    """An API key as the list shows it: the prefix only, never the key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    role: str
    user_id: uuid.UUID
    is_active: bool
    expires_at: Optional[datetime.datetime] = None
    last_used_at: Optional[datetime.datetime] = None
    revoked_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class APIKeyCreated(APIKeyOut):
    """The response to creating a key. The ONLY time the full key is shown."""

    key: str


# ======================================================================
# Audit log
# ======================================================================


class AuditLogOut(BaseModel):
    """One audit entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    details: Dict[str, Any]
    ip_address: Optional[str] = None
    user_id: Optional[uuid.UUID] = None
    created_at: datetime.datetime


class AuditLogPage(BaseModel):
    """
    One page of audit entries, newest first.

    `total` is the number of entries matching the filters across ALL pages, so
    a UI can show "page 2 of 14" without fetching everything.
    """

    items: List[AuditLogOut]
    total: int
    limit: int
    offset: int
