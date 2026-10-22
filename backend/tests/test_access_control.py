"""
test_access_control.py
======================
WHAT THIS FILE TESTS
--------------------
The small pure pieces the account system is built from, one at a time:

    core/roles.py        the ranking: admin > editor > viewer
    core/security.py     password policy, opaque tokens, API key format, jti
    core/config.py       the production SECRET_KEY guard
    models/invite.py     an invite's derived status
    services/email.py    the console sender, and that SMTP failure never raises

The full HTTP flows that combine them live in test_auth_flows.py.

TIER 1: no database, no network.
"""

import datetime
import uuid

import pytest
import jwt  # PyJWT
from pydantic import ValidationError

from app.core import roles, security
from app.core.config import Settings, _DEFAULT_SECRET_KEY, settings


# ======================================================================
# Roles
# ======================================================================


class TestRoles:
    @pytest.mark.parametrize(
        "alias,stored",
        [("viewer", "Employee"), ("editor", "Manager"), ("admin", "Admin"),
         ("Employee", "Employee"), ("manager", "Manager"), ("ADMIN", "Admin")],
    )
    def test_aliases_map_onto_the_stored_names(self, alias, stored):
        assert roles.normalize_role(alias) == stored

    def test_an_unknown_role_is_an_error_not_a_default(self):
        """A typo in `require_role("edtior")` must fail loudly at import time,
        never quietly admit or refuse everyone."""
        with pytest.raises(ValueError):
            roles.normalize_role("superuser")

    def test_the_ranking(self):
        assert roles.rank("Admin") > roles.rank("Manager") > roles.rank("Employee") > 0

    @pytest.mark.parametrize(
        "role,minimum,expected",
        [
            ("Admin", "viewer", True),
            ("Admin", "editor", True),
            ("Manager", "editor", True),
            ("Manager", "admin", False),
            ("Employee", "editor", False),
            ("Employee", "viewer", True),
        ],
    )
    def test_at_least(self, role, minimum, expected):
        assert roles.at_least(role, minimum) is expected

    @pytest.mark.parametrize("junk", [None, "", "root", "Administrator"])
    def test_an_unknown_stored_role_fails_closed(self, junk):
        assert roles.at_least(junk, "viewer") is False

    def test_lower_of_picks_the_weaker(self):
        assert roles.lower_of("Admin", "Employee") == "Employee"
        assert roles.lower_of("Employee", "Admin") == "Employee"
        assert roles.lower_of("Manager", None) == "Manager"


class TestRequireRole:
    def test_it_rejects_a_typo_at_definition_time(self):
        from app.api import deps

        with pytest.raises(ValueError):
            deps.require_role("edtior")

    def test_an_api_key_role_caps_the_user(self):
        """An Admin's viewer-scoped key acts as a viewer."""
        from app.api import deps
        from app.models.user import User

        user = User(role="Admin")
        assert deps.effective_role(user) == "Admin"
        user.api_key_role = "Employee"
        assert deps.effective_role(user) == "Employee"

    def test_api_key_role_is_not_a_column(self):
        from app.models.user import User

        assert "api_key_role" not in User.__table__.columns


# ======================================================================
# Password policy
# ======================================================================


class TestPasswordPolicy:
    def test_a_good_password_has_no_problems(self):
        assert security.password_problems("correct horse battery", "ada@example.com") == []

    def test_too_short(self):
        problems = security.password_problems("short", None)
        assert problems == [f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters long."]

    def test_all_numeric(self):
        assert "numbers only" in " ".join(security.password_problems("12345678901234"))

    @pytest.mark.parametrize("password", ["ada.lovelace@example.com", "ADA.LOVELACE@EXAMPLE.COM", "ada.lovelace"])
    def test_the_email_or_its_name_part(self, password):
        problems = security.password_problems(password, "ada.lovelace@example.com")
        assert any("email" in p for p in problems)

    def test_every_problem_is_reported_at_once(self):
        assert len(security.password_problems("12345", None)) == 2

    def test_the_minimum_follows_the_setting(self, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD_MIN_LENGTH", 20)
        assert security.password_problems("a" * 19)
        assert not security.password_problems("a" * 20)


# ======================================================================
# Opaque tokens and API keys
# ======================================================================


class TestOpaqueTokens:
    def test_tokens_are_random_and_long(self):
        a, b = security.generate_token(), security.generate_token()
        assert a != b and len(a) >= 40

    def test_the_hash_is_sha256_hex_and_not_the_token(self):
        token = security.generate_token()
        hashed = security.hash_token(token)
        assert len(hashed) == 64 and token not in hashed
        assert hashed == security.hash_token(token)

    def test_an_api_key_has_the_prefix_and_fits_its_columns(self):
        plaintext, prefix, hashed = security.generate_api_key()
        assert plaintext.startswith("dm_")
        assert prefix == plaintext[:12] and len(prefix) <= 16  # api_keys.key_prefix is String(16)
        assert hashed == security.hash_token(plaintext)


class TestTokenIds:
    def _decode(self, token):
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    def test_every_access_token_has_a_unique_jti(self):
        a = self._decode(security.create_access_token("u"))
        b = self._decode(security.create_access_token("u"))
        assert a["jti"] != b["jti"]

    def test_refresh_tokens_are_unique_even_in_the_same_second(self):
        """The server stores refresh-token HASHES under a unique index; two
        identical tokens would make the second login fail."""
        assert security.create_refresh_token("u") != security.create_refresh_token("u")

    def test_iat_is_fractional(self):
        """Whole seconds would let a token minted in the same second as a
        password reset survive it."""
        iat = self._decode(security.create_access_token("u"))["iat"]
        assert isinstance(iat, float)


# ======================================================================
# The production SECRET_KEY guard
# ======================================================================


class TestSecretKeyGuard:
    def test_development_accepts_the_default(self):
        assert Settings(ENV="development", SECRET_KEY=_DEFAULT_SECRET_KEY)

    def test_production_refuses_the_committed_default(self):
        with pytest.raises(ValidationError, match="development default"):
            Settings(ENV="production", SECRET_KEY=_DEFAULT_SECRET_KEY)

    def test_production_refuses_a_short_key(self):
        with pytest.raises(ValidationError, match="at least 32"):
            Settings(ENV="production", SECRET_KEY="too-short")

    def test_production_accepts_a_strong_key(self):
        assert Settings(ENV="production", SECRET_KEY="x" * 64).ENV == "production"


# ======================================================================
# Invite status
# ======================================================================


class TestInviteStatus:
    def _invite(self, **overrides):
        from app.models.invite import Invite

        fields = {
            "org_id": uuid.uuid4(),
            "email": "a@b.com",
            "role": "Employee",
            "token_hash": "x" * 64,
            "expires_at": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1),
        }
        fields.update(overrides)
        return Invite(**fields)

    def test_pending(self):
        assert self._invite().status == "pending"

    def test_expired(self):
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        assert self._invite(expires_at=past).status == "expired"

    def test_a_naive_expiry_is_read_as_utc(self):
        past = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
        assert self._invite(expires_at=past).status == "expired"

    def test_accepted_wins_over_expired(self):
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        assert self._invite(expires_at=past, accepted_at=past).status == "accepted"

    def test_revoked(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        assert self._invite(revoked_at=now).status == "revoked"


# ======================================================================
# Email
# ======================================================================


class TestEmail:
    async def test_console_is_the_default_and_records_the_message(self, monkeypatch):
        from app.services import email

        monkeypatch.setattr(settings, "SMTP_HOST", "")
        email.OUTBOX.clear()

        assert settings.email_backend == "console"
        assert await email.send_password_reset_email("a@b.com", "tok123") is True
        assert "reset-password?token=tok123" in email.OUTBOX[-1].body

    async def test_an_smtp_failure_returns_false_instead_of_raising(self, monkeypatch):
        """A mail outage must never become a 500 -- or, on password reset, a
        response that differs between real and unknown addresses."""
        from app.services import email

        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.invalid")

        def boom(_message):
            raise OSError("connection refused")

        monkeypatch.setattr(email, "_send_smtp", boom)
        assert settings.email_backend == "smtp"
        assert await email.send_invite_email("a@b.com", "Acme", "Employee", "t") is False

    def test_links_point_at_the_frontend(self, monkeypatch):
        from app.services import email

        monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com/")
        assert email.invite_link("abc") == "https://app.example.com/login?invite=abc"

    def test_frontend_url_falls_back_to_the_first_origin(self, monkeypatch):
        monkeypatch.setattr(settings, "FRONTEND_URL", "")
        monkeypatch.setattr(settings, "FRONTEND_ORIGIN", " https://a.example.com , https://b.example.com")
        assert settings.frontend_url == "https://a.example.com"
