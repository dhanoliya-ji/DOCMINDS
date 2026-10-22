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
import hashlib
import secrets
import uuid
from typing import Any, List, Optional, Tuple, Union

from jose import jwt
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
        # "iat" -- issued-at. Lets get_current_user reject tokens issued BEFORE
        # the user's last password change (see `password_changed_at`). A
        # FRACTIONAL timestamp (RFC 7519 allows it): with whole seconds, a
        # token stolen in the same second as a password reset would survive it.
        "iat": datetime.datetime.now(datetime.timezone.utc).timestamp(),
        # "jti" -- JWT ID, a random unique name for THIS token. Logout puts it
        # on the denylist (models/revoked_token.py), which is the only way to
        # kill a stateless token before it expires on its own.
        "jti": uuid.uuid4().hex,
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
        # A random id makes every refresh token unique, even two issued to the
        # same user in the same second. That matters because the server stores
        # a HASH of each one (models/refresh_token.py) under a unique index.
        "jti": uuid.uuid4().hex,
    }

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ----------------------------------------------------------------------
# OPAQUE TOKENS: invites, password reset, email verification, API keys
# ----------------------------------------------------------------------
# These are NOT JWTs. They are long random strings whose only meaning is "the
# row in the database with this hash". That makes them revocable and single
# use for free: delete or mark the row, and the string is worthless.
#
# THE SERVER ONLY EVER STORES A HASH OF THEM -- the same idea as passwords.
# Somebody who reads the database (a backup, a leaked dump) finds hashes they
# cannot turn back into working links.
#
# SHA-256, not bcrypt, and that is correct here: bcrypt is slow to protect
# SHORT human-chosen passwords from guessing. These strings carry 256 bits of
# randomness, so guessing is hopeless anyway, and a fast hash keeps the lookup
# cheap -- it runs on every API-key request.


def generate_token() -> str:
    """A fresh, URL-safe random token (43 characters, 256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest -- what the database stores instead of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Every API key starts with this, so a key pasted into the wrong place (a chat
# message, a public repo) is recognisable at a glance -- and by secret scanners.
API_KEY_PREFIX = "dm_"


def generate_api_key() -> Tuple[str, str, str]:
    """
    Create a new API key.

    Returns (plaintext, display_prefix, hash). The plaintext is shown to the
    user exactly ONCE, by the endpoint that creates it, and never again: only
    the prefix (for recognising the key in a list) and the hash are stored.
    """
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    # 12 characters: "dm_" plus 9 random ones. Enough to tell keys apart in a
    # list, nowhere near enough to guess the other ~34.
    return plaintext, plaintext[:12], hash_token(plaintext)


# ----------------------------------------------------------------------
# PASSWORD POLICY
# ----------------------------------------------------------------------


def password_problems(password: str, email: Optional[str] = None) -> List[str]:
    """
    Everything wrong with a proposed password, as human-readable sentences.

    An EMPTY list means the password is acceptable. Returning every problem at
    once (rather than raising on the first) lets the signup form tell the user
    everything they need to fix in one round trip.

    THE RULES, AND WHY THESE AND NOT OTHERS
    ---------------------------------------
    Modern guidance (NIST SP 800-63B) is to require LENGTH and to block the
    obviously guessable, rather than demanding a capital, a digit and a symbol.
    Composition rules mostly produce "Password1!", which every cracking list
    tries early; length is what actually costs an attacker time.

        * at least PASSWORD_MIN_LENGTH characters (10 by default)
        * not ONLY digits -- "1234567890" is long and still the first thing
          anyone tries, and a phone number or date of birth is guessable
        * not the account's own email address, which is the very first
          thing an attacker who knows the email would try
    """
    problems: List[str] = []
    minimum = settings.PASSWORD_MIN_LENGTH

    if len(password) < minimum:
        problems.append(f"Password must be at least {minimum} characters long.")
    if password.isdigit():
        problems.append("Password cannot be made of numbers only.")
    if email:
        lowered = password.strip().lower()
        if lowered == email.strip().lower() or lowered == email.split("@")[0].lower():
            problems.append("Password cannot be the same as your email address.")

    return problems
