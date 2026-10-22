"""
user.py  (schemas)
==================
WHAT THIS FILE DOES
-------------------
Describes the SHAPE of user data going into and out of the API.

THE MOST IMPORTANT THING ON THIS PAGE
-------------------------------------
Look at `UserOut` below and notice what is NOT there: `hashed_password`.

Pydantic returns ONLY the fields a schema declares. Even though the `User`
object handed to it carries the password hash, that field is silently dropped
because the schema does not mention it.

That is a genuine security control, not a formatting nicety. Any endpoint
returning `response_model=UserOut` is structurally incapable of leaking the
hash -- there is no way to forget, because omission is the default.

WHY SEPARATE "IN" AND "OUT" SCHEMAS?
------------------------------------
The fields a client may SEND are not the fields we are willing to RETURN.

    UserCreate  takes a plain `password`  (in, never stored as given)
    UserOut     returns `id`, `is_active` (out, never accepted from a client)

If one schema did both, a client could POST `{"is_active": true, "role":
"Admin"}` and promote themselves. Splitting them makes that impossible.
"""

import datetime
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Fields shared by several user schemas, defined once."""

    # `EmailStr` validates the address format automatically. A malformed email
    # is rejected with a clear 422 before any of our code runs.
    email: EmailStr

    # `Literal` restricts the value to exactly these three strings. Anything
    # else is rejected, so an invalid role can never reach the database.
    role: Literal["Admin", "Manager", "Employee"] = "Employee"


class UserCreate(UserBase):
    """
    Body for POST /auth/signup.

    There are exactly two ways to sign up, and the CLIENT CANNOT CHOOSE ITS
    OWN ROLE in either:

        no invite_token   a brand-new organization is created (named
                          `org_name`, or after the email if omitted) and the
                          user becomes its Admin.

        invite_token      the user joins the organization that invited them,
                          with the role the inviting Admin chose. The email
                          must match the one the invite was sent to.

    The inherited `role` field is still ACCEPTED, so older clients do not get
    a 422, but it is IGNORED -- see api/v1/auth.py. Letting a request pick its
    own role is precisely the hole invites exist to close.
    """

    # The PLAIN password. It arrives here, is checked against the password
    # policy (core/security.py), immediately bcrypt-hashed, and never stored
    # or logged as given.
    password: str

    # The name for a NEW organization. Optional: without it the organization
    # is named after the email address, and can be renamed later.
    org_name: Optional[str] = Field(default=None, max_length=255)

    # The token from an invite email. Present = join; absent = create.
    invite_token: Optional[str] = Field(default=None, max_length=256)

    # DEPRECATED. Joining an organization by its id is no longer possible --
    # anyone who learned an id could join that company. Kept in the schema
    # only so a client still sending it gets a clear error from the endpoint
    # instead of silently landing in a brand-new organization.
    org_id: Optional[uuid.UUID] = None


class UserUpdate(BaseModel):
    """
    Body for editing a user. Every field is optional so a caller can send just
    the one they want to change (a PATCH-style partial update) rather than
    having to resend the whole object.

    Note it does NOT inherit from UserBase: inheriting would make `email`
    required, which defeats the purpose of a partial update.
    """

    email: Optional[EmailStr] = None
    role: Optional[Literal["Admin", "Manager", "Employee"]] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class UserOut(UserBase):
    """
    What the API returns about a user.

    Contains no password field of any kind -- see the module docstring.
    """

    # `from_attributes=True` lets Pydantic build this straight from a
    # SQLAlchemy row by reading its attributes. Without it, every endpoint
    # would have to copy each field across by hand.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    is_active: bool
    is_verified: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
