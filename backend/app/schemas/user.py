"""Describes the SHAPE of user data going into and out of the API."""
import datetime
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Fields shared by several user schemas, defined once."""

    email: EmailStr

    role: Literal["Admin", "Manager", "Employee"] = "Employee"


class UserCreate(UserBase):
    """Body for POST /auth/signup."""

    password: str

    org_name: Optional[str] = Field(default=None, max_length=255)

    invite_token: Optional[str] = Field(default=None, max_length=256)

    org_id: Optional[uuid.UUID] = None


class UserUpdate(BaseModel):
    """Body for editing a user. Every field is optional so a caller can send just"""

    email: Optional[EmailStr] = None
    role: Optional[Literal["Admin", "Manager", "Employee"]] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class UserOut(UserBase):
    """What the API returns about a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    is_active: bool
    is_verified: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
