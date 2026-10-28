"""Request and response shapes for everything an organization's members and Admins manage about ACCOUNTS --..."""
import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

RoleName = Literal["Admin", "Manager", "Employee", "admin", "editor", "viewer"]


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
    """What GET /auth/invites/{token} tells a not-yet-signed-up visitor, so the"""

    email: str
    role: str
    org_name: str
    expires_at: datetime.datetime


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


class InviteCreate(BaseModel):
    """Body for POST /org/invites."""

    email: EmailStr
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
    status: str = "pending"


class InviteCreated(InviteOut):
    """The response to creating an invite. The ONLY time the token is returned."""

    token: str
    invite_url: str


class APIKeyCreate(BaseModel):
    """Body for POST /org/api-keys."""

    name: str = Field(min_length=1, max_length=255)
    role: RoleName = "Employee"
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
    """One page of audit entries, newest first."""

    items: List[AuditLogOut]
    total: int
    limit: int
    offset: int
