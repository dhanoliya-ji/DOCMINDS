"""Encrypts and decrypts the OAuth tokens a connection holds, before they touch the database."""
import base64
import json
from functools import lru_cache
from typing import Any, Dict, List

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

_PURPOSE = b"docminds connector tokens v1"


class TokenDecryptError(Exception):
    """The stored tokens could not be decrypted: the key changed, or they were altered."""


def _derive(material: str) -> bytes:
    """Any string -> a valid Fernet key (32 bytes, url-safe base64)."""
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_PURPOSE).derive(
        material.encode("utf-8")
    )
    return base64.urlsafe_b64encode(raw)


def _as_fernet_key(value: str) -> bytes:
    """Use a real Fernet key as given; stretch anything else."""
    try:
        if len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32:
            return value.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        pass
    return _derive(value)


@lru_cache(maxsize=4)
def _fernet_for(configured: str, secret_key: str) -> MultiFernet:
    keys: List[bytes] = [
        _as_fernet_key(part.strip()) for part in configured.split(",") if part.strip()
    ]
    if not keys:
        keys = [_derive(secret_key)]
    return MultiFernet([Fernet(key) for key in keys])


def _fernet() -> MultiFernet:
    return _fernet_for(settings.CONNECTOR_ENCRYPTION_KEY, settings.SECRET_KEY)


def encrypt_tokens(tokens: Dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(tokens, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decrypt_tokens(ciphertext: str) -> Dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError) as exc:
        raise TokenDecryptError(
            "The stored connection could not be decrypted (was the encryption key "
            "changed?). Disconnect and connect it again."
        ) from exc
