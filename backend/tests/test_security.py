"""
test_security.py
================
WHAT THIS FILE TESTS
--------------------
`core/security.py` -- password hashing and JWT creation.

WHY THIS FILE MATTERS
---------------------
Security code fails silently. A password comparison that always returns True,
or a token whose `type` is never checked, breaks nothing visible: the app keeps
working, logins keep succeeding, and the hole stays open until someone finds
it. There is no crash to notice and no wrong answer on screen.

So the properties below are asserted directly rather than assumed from reading.

TIER 1: no database, no network.
"""

import uuid
from datetime import timedelta

import pytest
from jose import JWTError, jwt

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)


def decode(token: str) -> dict:
    """Decode a token the same way the API's dependency does."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


# ======================================================================
# Passwords
# ======================================================================


class TestPasswordHashing:
    def test_the_hash_is_not_the_password(self):
        """The single most important property. Sounds trivial; must be pinned."""
        password = "correct horse battery staple"
        hashed = get_password_hash(password)

        assert hashed != password
        assert password not in hashed

    def test_the_right_password_verifies(self):
        hashed = get_password_hash("s3cret-pa55word")
        assert verify_password("s3cret-pa55word", hashed) is True

    @pytest.mark.parametrize(
        "wrong",
        [
            "s3cret-pa55wor",       # one character short
            "s3cret-pa55worD",      # different case at the end
            "S3cret-pa55word",      # different case at the start
            "",                     # empty
            "completely different",
        ],
    )
    def test_a_wrong_password_does_not_verify(self, wrong):
        hashed = get_password_hash("s3cret-pa55word")
        assert verify_password(wrong, hashed) is False

    def test_the_same_password_hashes_differently_every_time(self):
        """
        Bcrypt salts each hash. Two users who choose the same password must
        store different values -- otherwise equal hashes in a leaked database
        would reveal which accounts share a password, and one cracked hash
        would open all of them at once.
        """
        first = get_password_hash("identical")
        second = get_password_hash("identical")

        assert first != second
        # Both still verify, because the salt travels inside the hash.
        assert verify_password("identical", first)
        assert verify_password("identical", second)

    def test_bcrypt_is_the_scheme_in_use(self):
        """`$2b$` is bcrypt's identifier. A change here is a deliberate act."""
        assert get_password_hash("anything").startswith("$2")

    def test_unicode_passwords_work(self):
        password = "пароль-密码-🔑"
        assert verify_password(password, get_password_hash(password))


# ======================================================================
# Tokens
# ======================================================================


class TestAccessToken:
    def test_the_subject_survives_the_round_trip(self):
        user_id = uuid.uuid4()
        payload = decode(create_access_token(subject=user_id))

        # Stored as a string, because JSON has no UUID type.
        assert payload["sub"] == str(user_id)

    def test_it_is_marked_as_an_access_token(self):
        assert decode(create_access_token(subject="user-1"))["type"] == "access"

    def test_additional_claims_are_embedded(self):
        """
        `org_id` and `role` ride along so the API can authorise without a
        second database lookup on every request.
        """
        org_id = str(uuid.uuid4())
        token = create_access_token(
            subject="user-1", additional_claims={"org_id": org_id, "role": "Admin"}
        )

        payload = decode(token)
        assert payload["org_id"] == org_id
        assert payload["role"] == "Admin"

    def test_it_carries_an_expiry(self):
        payload = decode(create_access_token(subject="user-1"))
        assert "exp" in payload

    def test_an_expired_token_is_rejected(self):
        """A token past its expiry must not decode, whatever else is right."""
        token = create_access_token(subject="user-1", expires_delta=timedelta(seconds=-10))

        with pytest.raises(jwt.ExpiredSignatureError):
            decode(token)

    def test_a_token_signed_with_another_key_is_rejected(self):
        """
        The signature is the whole security model: the server trusts a token
        because it could only have been signed by something holding the secret.
        """
        forged = jwt.encode(
            {"sub": "attacker", "type": "access"},
            "not-the-real-secret-key",
            algorithm=settings.ALGORITHM,
        )

        with pytest.raises(JWTError):
            decode(forged)

    def test_a_tampered_token_is_rejected(self):
        token = create_access_token(subject="user-1")
        head, body, signature = token.split(".")

        # Change one character of the payload, leave the signature alone.
        tampered = f"{head}.{body[:-2]}XY.{signature}"

        with pytest.raises(JWTError):
            decode(tampered)


class TestRefreshToken:
    def test_it_is_marked_as_a_refresh_token(self):
        assert decode(create_refresh_token(subject="user-1"))["type"] == "refresh"

    def test_the_two_types_are_distinguishable(self):
        """
        Without the `type` claim a 7-day refresh token would be accepted
        wherever a 30-minute access token is -- silently turning a short-lived
        credential into a long-lived one. `/auth/refresh` checks this claim,
        and it can only check it if the two differ.
        """
        access = decode(create_access_token(subject="user-1"))
        refresh = decode(create_refresh_token(subject="user-1"))

        assert access["type"] != refresh["type"]

    def test_it_carries_no_role_and_no_org(self):
        """
        Deliberate. A refresh token only proves identity; permissions are
        re-read from the database when it is exchanged. That is what makes a
        revoked role take effect within 30 minutes rather than lingering in a
        token for a week.
        """
        payload = decode(
            create_refresh_token(
                subject="user-1", additional_claims={"role": "Admin", "org_id": "x"}
            )
            if _accepts_claims()
            else create_refresh_token(subject="user-1")
        )

        assert "role" not in payload
        assert "org_id" not in payload

    def test_it_lives_longer_than_an_access_token(self):
        access = decode(create_access_token(subject="user-1"))
        refresh = decode(create_refresh_token(subject="user-1"))

        assert refresh["exp"] > access["exp"]


def _accepts_claims() -> bool:
    """
    Does `create_refresh_token` even take additional claims?

    The test above should hold either way -- whether the function refuses the
    argument or accepts and ignores it -- so this keeps the assertion about
    behaviour rather than about the signature.
    """
    import inspect

    return "additional_claims" in inspect.signature(create_refresh_token).parameters
