"""
Tier-1 tests for login/refresh hardening (no database needed).

    * refresh claims the token with a CONDITIONAL update, so of two requests
      racing with the same token only one wins; the loser is treated as reuse
    * login runs bcrypt even for an unknown email (no timing oracle)
    * login matches the email case-insensitively, like signup and reset
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import auth
from app.core import security
from app.core.config import settings


class FakeResult:
    def __init__(self, rowcount=1, user=None):
        self.rowcount = rowcount
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class FakeDB:
    """Records statements; answers the conditional UPDATE with `rowcount`."""

    def __init__(self, rowcount=1, user=None):
        self.rowcount = rowcount
        self.user = user
        self.statements = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.rowcount, self.user)

    async def get(self, model, pk):
        return self.user

    async def commit(self):
        self.commits += 1


def _refresh_token():
    return security.create_refresh_token(subject=str(uuid.uuid4()))


@pytest.fixture
def stored_token(monkeypatch):
    row = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), family_id=uuid.uuid4(),
                          revoked_at=None, expires_at=None)
    revoked = []

    async def find(db, token):
        return row

    async def revoke_family(db, family_id):
        revoked.append(family_id)

    async def issue(db, user, family_id=None):
        return {"access_token": "a", "refresh_token": "r", "token_type": "bearer"}

    monkeypatch.setattr(auth.sessions, "find_refresh_token", find)
    monkeypatch.setattr(auth.sessions, "is_expired", lambda _: False)
    monkeypatch.setattr(auth.sessions, "revoke_family", revoke_family)
    monkeypatch.setattr(auth.sessions, "issue_token_pair", issue)
    return row, revoked


class TestRefreshRace:
    async def test_winner_of_the_conditional_update_gets_a_pair(self, stored_token):
        row, revoked = stored_token
        db = FakeDB(rowcount=1, user=SimpleNamespace(is_active=True))
        tokens = await auth.refresh_token(auth.TokenRefreshRequest(refresh_token=_refresh_token()), db)
        assert tokens["access_token"] == "a"
        assert revoked == []
        # The claim is a conditional UPDATE on revoked_at IS NULL.
        sql = str(db.statements[-1])
        assert "UPDATE refresh_tokens" in sql and "revoked_at IS NULL" in sql

    async def test_loser_of_the_race_is_treated_as_reuse(self, stored_token):
        # The Python-side check saw revoked_at=None, but another request
        # claimed the row first: rowcount 0.
        row, revoked = stored_token
        db = FakeDB(rowcount=0, user=SimpleNamespace(is_active=True))
        with pytest.raises(HTTPException) as exc:
            await auth.refresh_token(auth.TokenRefreshRequest(refresh_token=_refresh_token()), db)
        assert exc.value.status_code == 401
        assert revoked == [row.family_id]
        assert db.commits == 1


class TestLogin:
    def _form(self, username, password="pw"):
        return SimpleNamespace(username=username, password=password)

    async def test_unknown_email_still_runs_bcrypt(self, monkeypatch):
        seen = []
        monkeypatch.setattr(security, "verify_password", lambda p, h: seen.append(h) or False)
        monkeypatch.setattr(auth, "_DUMMY_HASH", "$2b$dummy")
        with pytest.raises(HTTPException) as exc:
            await auth.login(SimpleNamespace(), FakeDB(user=None), self._form("nobody@x.com"))
        assert exc.value.status_code == 401
        assert seen == ["$2b$dummy"]

    async def test_email_lookup_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(security, "verify_password", lambda p, h: False)
        monkeypatch.setattr(auth, "_DUMMY_HASH", "x")
        db = FakeDB(user=None)
        with pytest.raises(HTTPException):
            await auth.login(SimpleNamespace(), db, self._form("  Alice@X.com "))
        stmt = db.statements[0]
        assert "lower(users.email)" in str(stmt)
        assert "alice@x.com" in stmt.compile().params.values()

    def test_dummy_hash_is_a_real_bcrypt_hash(self, monkeypatch):
        monkeypatch.setattr(auth, "_DUMMY_HASH", None)
        try:
            h = auth._dummy_hash()
        except ValueError:  # passlib + bcrypt>=4.1 locally; CI pins a working pair
            pytest.skip("local bcrypt backend incompatible with passlib")
        assert h == auth._dummy_hash()
        assert not security.verify_password("anything", h)


def test_settings_default():
    assert settings.RATE_LIMIT_VERIFY_RESEND == "5/hour"
