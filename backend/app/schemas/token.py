"""Describes the shape of authentication tokens -- both what we hand back after a successful login, and what..."""
from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    """What /auth/login and /auth/refresh return."""

    access_token: str
    refresh_token: str

    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """The claims we expect to find INSIDE a decoded token."""

    sub: Optional[str] = None

    role: Optional[str] = None
    org_id: Optional[str] = None

    type: Optional[str] = None

    jti: Optional[str] = None

    iat: Optional[float] = None
    exp: Optional[float] = None


class TokenRefreshRequest(BaseModel):
    """Body for POST /auth/refresh -- exchange a refresh token for a new pair."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Body for POST /auth/logout."""

    refresh_token: Optional[str] = None
