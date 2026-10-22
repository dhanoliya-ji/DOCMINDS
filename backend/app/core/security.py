"""
security.py
===========
WHAT THIS FILE DOES
-------------------
Two separate security jobs live here:

    1. PASSWORD HASHING  -- storing passwords so a database leak is survivable.
    2. TOKEN CREATION    -- issuing the JWTs that prove who a user is.

--------------------------------------------------------------------------
PART 1: WHY PASSWORDS ARE HASHED, NOT ENCRYPTED
--------------------------------------------------------------------------
We never store the password itself. We store a HASH -- a one-way fingerprint:

    "hunter2"  ->  "$2b$12$KIXQJ8f0Zx9Yq2rL8mNc.uO7hT3vB1sD4gF6hJ9kL2mN5pQ8rS1tU"

One-way means the hash cannot be turned back into the password. To check a
login we hash what the user typed and compare the two fingerprints.

So if the database is stolen, the attacker gets fingerprints, not passwords.

WHY BCRYPT SPECIFICALLY?
Because it is DELIBERATELY SLOW. A fast hash like SHA-256 can be computed
billions of times per second on a GPU, so an attacker simply tries every common
password. Bcrypt takes ~100ms per attempt by design, turning a few hours of
brute forcing into several years.

Bcrypt also salts automatically: a random value is mixed into every hash, so
two users with the same password get completely different fingerprints. That
defeats "rainbow tables" -- precomputed lists of hash-to-password mappings.

--------------------------------------------------------------------------
PART 2: WHAT A JWT IS
--------------------------------------------------------------------------
A JSON Web Token is a signed string with three dot-separated parts:

    eyJhbGciOiJIUzI1NiJ9 . eyJzdWIiOiIxMjMiLCJyb2xlIjoi... . dBjftJeZ4CVP...
    └─ header ─────────┘   └─ payload (the claims) ─────┘   └─ signature ─┘

The payload is only base64-encoded, NOT encrypted -- anyone can read it. What
the signature guarantees is that nobody has CHANGED it, because producing a
valid signature requires SECRET_KEY.

    => Never put anything confidential in a token.
    => Do put identity and role in it, so the API can authorise a request
       without a database lookup on every call.

WHY TWO KINDS OF TOKEN?
    ACCESS token   short-lived (30 min). Sent with every request. If it is
                   stolen, the damage window is small.
    REFRESH token  long-lived (7 days). Used ONLY to obtain a new access token.
                   Sent rarely, so it is far less exposed.

That pairing is what lets us have both short-lived credentials and a user who
is not forced to log in twice an hour.
"""

import datetime
from typing import Any, Optional, Union

import jwt  # PyJWT
from passlib.context import CryptContext

from app.core.config import settings

# ----------------------------------------------------------------------
# THE PASSWORD HASHER
# ----------------------------------------------------------------------
# `schemes=["bcrypt"]` sets the algorithm used for NEW hashes.
#
# `deprecated="auto"` is forward planning: if a stronger algorithm is added to
# the list later, passlib will still VERIFY old bcrypt hashes correctly and can
# transparently upgrade them on next login. Without it, changing algorithms
# would lock every existing user out.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a typed password against a stored hash.

    passlib reads the salt and cost factor out of the stored hash, re-hashes the
    supplied password the same way, and compares. The comparison is
    constant-time, which prevents a timing attack -- if it returned early on the
    first differing character, an attacker could measure response times to work
    out the hash one character at a time.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Turn a plain password into a hash suitable for storing.

    Called once at signup. A fresh random salt is generated internally, so the
    same password hashed twice produces two different results -- both of which
    verify correctly.
    """
    return pwd_context.hash(password)


# ----------------------------------------------------------------------
# TOKEN CREATION
# ----------------------------------------------------------------------


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[datetime.timedelta] = None,
    additional_claims: Optional[dict] = None,
) -> str:
    """
    Build a short-lived access token.

    Arguments:
        subject           -- who the token is about; here, the user's id.
        expires_delta     -- optional custom lifetime.
        additional_claims -- extra facts to embed, such as org_id and role.

    Embedding `org_id` and `role` is a deliberate optimisation: the API can
    check permissions straight from the verified token instead of querying the
    database on every single request.
    """
    if expires_delta:
        expire = datetime.datetime.now(datetime.timezone.utc) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    # These short key names are the JWT standard's registered claims:
    #   "exp" -- expiry time. Verification fails automatically once passed.
    #   "sub" -- subject, the entity the token describes.
    # "type" is our own addition, explained below.
    to_encode = {
        "exp": expire,
        # Cast to str because the id is a UUID object, and JSON has no UUID type.
        "sub": str(subject),
        # WHY THIS MATTERS: without a type marker, a refresh token would also be
        # accepted as an access token -- handing an attacker a credential valid
        # for seven days instead of thirty minutes. deps.py checks this field.
        "type": "access",
    }

    if additional_claims:
        to_encode.update(additional_claims)

    # Signing with SECRET_KEY is what makes the token tamper-proof. Editing the
    # payload invalidates the signature, and forging a new one is impossible
    # without the key.
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: Optional[datetime.timedelta] = None,
) -> str:
    """
    Build a long-lived refresh token.

    Note what is NOT here: no role, no org_id. A refresh token's only purpose is
    to identify the user well enough to mint a fresh access token. Keeping it
    minimal means stale permissions cannot be carried forward -- the new access
    token is built from the user's CURRENT role, read from the database at that
    moment.
    """
    if expires_delta:
        expire = datetime.datetime.now(datetime.timezone.utc) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
    }

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
