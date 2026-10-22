"""
token.py  (schemas)
===================
WHAT THIS FILE DOES
-------------------
Describes the shape of authentication tokens -- both what we hand back after a
successful login, and what we expect to find inside a token when decoding one.

REMINDER OF HOW THE TWO TOKENS WORK
-----------------------------------
    ACCESS token   30 minutes. Sent with every API request. Short-lived so a
                   stolen one is only briefly useful.
    REFRESH token  7 days. Used ONLY to obtain a new access token, so it
                   travels rarely and is far less exposed.

Together they give short-lived credentials without forcing the user to log in
again twice an hour. See core/security.py for how they are created.
"""

from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    """
    What /auth/login and /auth/refresh return.

    The frontend stores `access_token` and attaches it to every subsequent
    request as `Authorization: Bearer <token>`.
    """

    access_token: str
    refresh_token: str

    # "bearer" is the OAuth2 standard type, meaning "whoever bears this token
    # is granted access". It is included because the specification requires it,
    # and because OAuth2 client libraries read it to build the header.
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """
    The claims we expect to find INSIDE a decoded token.

    Used in api/deps.py: after `jwt.decode` verifies the signature, the raw
    dictionary is parsed through this model so the rest of the code works with
    typed fields instead of unchecked dictionary lookups.

    EVERY FIELD IS OPTIONAL, AND THAT IS DELIBERATE.
    A forged or malformed token might be missing any of them. If they were
    required, Pydantic would raise a validation error that could escape as an
    unhandled 500. Optional fields mean the code can check them explicitly and
    return a clean 401 instead -- an attacker learns nothing from a crash.
    """

    # "sub" (subject) -- the standard JWT claim naming who the token is about.
    # Holds the user's id, as a string, because JSON has no UUID type.
    sub: Optional[str] = None

    # Custom claims we add at login, so permissions can be checked straight
    # from the verified token without a database query on every request.
    role: Optional[str] = None
    org_id: Optional[str] = None

    # "access" or "refresh".
    #
    # THIS FIELD IS A REAL SECURITY CONTROL. Both token kinds carry a valid
    # signature, so without this marker a refresh token would be accepted as an
    # API credential -- handing an attacker seven days of access instead of
    # thirty minutes. `get_current_user` in deps.py rejects anything that is
    # not "access".
    type: Optional[str] = None

    # "jti" -- the token's unique id. Logout puts it on the denylist.
    jti: Optional[str] = None

    # "iat" / "exp" -- issued-at and expiry, as Unix seconds. `iat` is checked
    # against the user's last password change; `exp` tells logout how long the
    # denylist entry must last.
    iat: Optional[float] = None
    exp: Optional[float] = None


class TokenRefreshRequest(BaseModel):
    """Body for POST /auth/refresh -- exchange a refresh token for a new pair."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """
    Body for POST /auth/logout.

    The refresh token is optional only so that a client which never stored
    one can still sign out its access token. Sending it is what actually ends
    the SESSION -- without it, the refresh token could mint new access tokens
    for another week.
    """

    refresh_token: Optional[str] = None
