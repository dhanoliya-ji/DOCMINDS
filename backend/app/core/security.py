"""Two separate security jobs live here."""
import datetime
import hashlib
import secrets
import uuid
from typing import Any, List, Optional, Tuple, Union

import jwt  # PyJWT
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a typed password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Turn a plain password into a hash suitable for storing."""
    return pwd_context.hash(password)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[datetime.timedelta] = None,
    additional_claims: Optional[dict] = None,
) -> str:
    """Build a short-lived access token."""
    if expires_delta:
        expire = datetime.datetime.now(datetime.timezone.utc) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "iat": datetime.datetime.now(datetime.timezone.utc).timestamp(),
        "jti": uuid.uuid4().hex,
        "sub": str(subject),
        "type": "access",
    }

    if additional_claims:
        to_encode.update(additional_claims)

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: Optional[datetime.timedelta] = None,
) -> str:
    """Build a long-lived refresh token."""
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
        "jti": uuid.uuid4().hex,
    }

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def generate_token() -> str:
    """A fresh, URL-safe random token (43 characters, 256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest -- what the database stores instead of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


API_KEY_PREFIX = "dm_"


def generate_api_key() -> Tuple[str, str, str]:
    """Create a new API key."""
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return plaintext, plaintext[:12], hash_token(plaintext)


def password_problems(password: str, email: Optional[str] = None) -> List[str]:
    """Everything wrong with a proposed password, as human-readable sentences."""
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
