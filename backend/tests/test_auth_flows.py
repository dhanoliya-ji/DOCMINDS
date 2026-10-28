import datetime
import hashlib
import re
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core import security
from app.core.config import settings
from app.main import app
from app.models import Base
from app.models import AuditLog
from app.models import Organization
from app.models import User
from app.services import email as email_service
from tests.conftest import database_available

API = settings.API_V1_STR
PASSWORD = "a-perfectly-fine-password"


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element, compiler, **kw):
    """Render the audit table's JSONB column as SQLite's JSON."""
    return "JSON"


def _backends():
    available, reason = database_available()
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(not available, reason=f"Tier 2: {reason or 'no database'}"),
        ),
    ]


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """SHA-256 in place of bcrypt -- see the module docstring."""
    monkeypatch.setattr(
        security, "get_password_hash", lambda p: "test$" + hashlib.sha256(p.encode()).hexdigest()
    )
    monkeypatch.setattr(
        security,
        "verify_password",
        lambda p, h: h == "test$" + hashlib.sha256(p.encode()).hexdigest(),
    )


@pytest.fixture(autouse=True)
def console_email(monkeypatch):
    """Emails go to the in-memory OUTBOX, emptied for every test."""
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    email_service.OUTBOX.clear()
    yield email_service.OUTBOX
    email_service.OUTBOX.clear()


@pytest_asyncio.fixture(params=_backends())
async def env(request):
    """A client wired to the app, plus a session factory for direct checks."""
    backend = request.param
    created_orgs: list[str] = []

    if backend == "sqlite":
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[deps.get_db] = override_get_db
    else:
        from app.db.session import SessionLocal as factory  # noqa: N813

        engine = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield Env(client, factory, created_orgs)

    app.dependency_overrides.pop(deps.get_db, None)
    if engine is not None:
        await engine.dispose()
    else:
        async with factory() as session:
            for org_id in created_orgs:
                await session.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))
            await session.commit()


class Env:
    """Small helpers shared by every test, so each one reads as the flow it checks."""

    def __init__(self, client: AsyncClient, factory, created_orgs: list[str]):
        self.client = client
        self.factory = factory
        self.created_orgs = created_orgs

    @staticmethod
    def email(prefix: str = "user") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"

    async def signup(self, email: str | None = None, password: str = PASSWORD, **extra):
        email = email or self.email()
        response = await self.client.post(
            f"{API}/auth/signup", json={"email": email, "password": password, **extra}
        )
        if response.status_code == 201:
            org_id = response.json()["org_id"]
            if org_id not in self.created_orgs:
                self.created_orgs.append(org_id)
        return response

    async def login(self, email: str, password: str = PASSWORD):
        return await self.client.post(
            f"{API}/auth/login", data={"username": email, "password": password}
        )

    async def account(self, **extra) -> dict:
        """Sign up (a new org, so an Admin) and sign in."""
        email = self.email()
        signup = await self.signup(email, **extra)
        assert signup.status_code == 201, signup.text
        tokens = (await self.login(email)).json()
        return {
            "email": email,
            "user": signup.json(),
            "tokens": tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    async def member(self, admin: dict, role: str) -> dict:
        """Invite someone into `admin`'s org with `role`, sign them up and in."""
        email = self.email(role.lower())
        invite = await self.client.post(
            f"{API}/org/invites", headers=admin["headers"], json={"email": email, "role": role}
        )
        assert invite.status_code == 201, invite.text
        signup = await self.signup(email, invite_token=invite.json()["token"])
        assert signup.status_code == 201, signup.text
        tokens = (await self.login(email)).json()
        return {
            "email": email,
            "user": signup.json(),
            "tokens": tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    async def audit_actions(self, org_id: str) -> list[str]:
        async with self.factory() as session:
            rows = await session.execute(
                select(AuditLog.action).where(AuditLog.org_id == uuid.UUID(org_id))
            )
            return [row[0] for row in rows]


def token_from(outbox, kind: str) -> str:
    """Pull the token out of the newest email whose link matches `kind`."""
    for message in reversed(outbox):
        match = re.search(rf"/{kind}\?(?:token|invite)=([\w-]+)", message.body)
        if match:
            return match.group(1)
    raise AssertionError(f"no {kind} email in the outbox")


class TestSignup:
    async def test_without_an_invite_it_creates_a_new_org_and_an_admin(self, env):
        response = await env.signup(org_name="Acme")
        assert response.status_code == 201, response.text
        assert response.json()["role"] == "Admin"

    async def test_the_org_name_is_optional(self, env):
        assert (await env.signup()).status_code == 201

    async def test_a_client_sent_role_is_ignored(self, env):
        """Asking to be an Employee of your own new org still makes you its Admin,"""
        response = await env.signup(role="Employee", org_name="Mine")
        assert response.json()["role"] == "Admin"

    async def test_joining_by_org_id_alone_is_refused(self, env):
        """The old hole: an org id is not a secret, so it cannot be a key."""
        admin = await env.account()
        response = await env.signup(org_id=admin["user"]["org_id"], role="Admin")

        assert response.status_code == 400
        assert "invite" in response.json()["detail"].lower()

    @pytest.mark.parametrize(
        "password,fragment",
        [
            ("short", "at least 10"),
            ("12345678901234", "numbers only"),
        ],
    )
    async def test_the_password_policy_is_enforced(self, env, password, fragment):
        response = await env.signup(password=password)
        assert response.status_code == 422
        assert fragment in response.json()["detail"]

    async def test_the_password_cannot_be_the_email(self, env):
        email = env.email()
        response = await env.signup(email, password=email)
        assert response.status_code == 422
        assert "email" in response.json()["detail"]

    async def test_a_duplicate_email_is_refused_in_any_case(self, env):
        email = env.email()
        await env.signup(email)
        assert (await env.signup(email.upper())).status_code == 400

    async def test_signup_is_audited(self, env):
        account = await env.account()
        assert "user.signup" in await env.audit_actions(account["user"]["org_id"])


class TestEmailVerification:
    async def test_a_new_account_is_unverified_and_gets_an_email(self, env, console_email):
        account = await env.account()
        assert account["user"]["is_verified"] is False
        assert any(account["email"] == m.to for m in console_email)

    async def test_the_link_verifies_once(self, env, console_email):
        account = await env.account()
        token = token_from(console_email, "verify-email")

        first = await env.client.post(f"{API}/auth/verify-email", json={"token": token})
        again = await env.client.post(f"{API}/auth/verify-email", json={"token": token})
        me = await env.client.get(f"{API}/users/me", headers=account["headers"])

        assert first.status_code == 200
        assert again.status_code == 400
        assert me.json()["is_verified"] is True

    async def test_resend_replaces_the_old_link(self, env, console_email):
        account = await env.account()
        old = token_from(console_email, "verify-email")
        resend = await env.client.post(
            f"{API}/auth/verify-email/resend", headers=account["headers"]
        )
        new = token_from(console_email, "verify-email")

        assert resend.status_code == 202
        assert old != new
        assert (await env.client.post(f"{API}/auth/verify-email", json={"token": old})).status_code == 400
        assert (await env.client.post(f"{API}/auth/verify-email", json={"token": new})).status_code == 200

    async def test_login_can_require_verification(self, env, monkeypatch):
        monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True)
        email = env.email()
        await env.signup(email)
        assert (await env.login(email)).status_code == 403


class TestSessions:
    async def test_access_tokens_carry_a_jti(self, env):
        import jwt  # PyJWT

        account = await env.account()
        claims = jwt.decode(
            account["tokens"]["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert claims["jti"] and claims["iat"]

    async def test_refresh_rotates_the_refresh_token(self, env):
        account = await env.account()
        old = account["tokens"]["refresh_token"]

        response = await env.client.post(f"{API}/auth/refresh", json={"refresh_token": old})

        assert response.status_code == 200
        assert response.json()["refresh_token"] != old

    async def test_reusing_a_rotated_token_ends_the_whole_session(self, env):
        """The theft signal: an already-rotated token comes back. Both the old"""
        account = await env.account()
        old = account["tokens"]["refresh_token"]
        new = (await env.client.post(f"{API}/auth/refresh", json={"refresh_token": old})).json()[
            "refresh_token"
        ]

        reuse = await env.client.post(f"{API}/auth/refresh", json={"refresh_token": old})
        after = await env.client.post(f"{API}/auth/refresh", json={"refresh_token": new})

        assert reuse.status_code == 401
        assert after.status_code == 401

    async def test_an_unrecorded_refresh_token_is_refused(self, env):
        """Correctly signed but never issued by a login -- e.g. from before"""
        account = await env.account()
        forged = security.create_refresh_token(subject=account["user"]["id"])
        response = await env.client.post(f"{API}/auth/refresh", json={"refresh_token": forged})
        assert response.status_code == 401

    async def test_logout_kills_the_access_token_and_the_refresh_token(self, env):
        account = await env.account()

        response = await env.client.post(
            f"{API}/auth/logout",
            headers=account["headers"],
            json={"refresh_token": account["tokens"]["refresh_token"]},
        )
        me = await env.client.get(f"{API}/users/me", headers=account["headers"])
        refresh = await env.client.post(
            f"{API}/auth/refresh", json={"refresh_token": account["tokens"]["refresh_token"]}
        )

        assert response.status_code == 204
        assert me.status_code == 401
        assert refresh.status_code == 401
        assert "user.logout" in await env.audit_actions(account["user"]["org_id"])

    async def test_logout_does_not_touch_other_sessions(self, env):
        account = await env.account()
        second = (await env.login(account["email"])).json()

        await env.client.post(f"{API}/auth/logout", headers=account["headers"])
        me = await env.client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {second['access_token']}"}
        )
        assert me.status_code == 200

    async def test_logout_with_nothing_is_still_204(self, env):
        assert (await env.client.post(f"{API}/auth/logout")).status_code == 204

    async def test_a_failed_login_is_audited_but_not_revealed(self, env):
        account = await env.account()
        response = await env.login(account["email"], "definitely-wrong-password")

        assert response.status_code == 401
        assert "user.login_failed" in await env.audit_actions(account["user"]["org_id"])


class TestPasswordReset:
    async def test_the_response_does_not_reveal_whether_the_email_exists(self, env, console_email):
        account = await env.account()
        console_email.clear()

        known = await env.client.post(
            f"{API}/auth/password-reset/request", json={"email": account["email"]}
        )
        unknown = await env.client.post(
            f"{API}/auth/password-reset/request", json={"email": env.email("nobody")}
        )

        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()
        assert [m.to for m in console_email] == [account["email"]]

    async def test_a_reset_changes_the_password_and_ends_every_session(self, env, console_email):
        account = await env.account()
        await env.client.post(f"{API}/auth/password-reset/request", json={"email": account["email"]})
        token = token_from(console_email, "reset-password")

        confirm = await env.client.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": token, "new_password": "a-brand-new-password"},
        )

        assert confirm.status_code == 200
        assert (await env.login(account["email"])).status_code == 401
        assert (await env.login(account["email"], "a-brand-new-password")).status_code == 200

        old_me = await env.client.get(f"{API}/users/me", headers=account["headers"])
        assert old_me.status_code == 401
        old_refresh = await env.client.post(
            f"{API}/auth/refresh", json={"refresh_token": account["tokens"]["refresh_token"]}
        )
        assert old_refresh.status_code == 401

        assert "password.reset" in await env.audit_actions(account["user"]["org_id"])

    async def test_the_link_works_once(self, env, console_email):
        account = await env.account()
        await env.client.post(f"{API}/auth/password-reset/request", json={"email": account["email"]})
        token = token_from(console_email, "reset-password")
        body = {"token": token, "new_password": "a-brand-new-password"}

        assert (await env.client.post(f"{API}/auth/password-reset/confirm", json=body)).status_code == 200
        assert (await env.client.post(f"{API}/auth/password-reset/confirm", json=body)).status_code == 400

    async def test_a_weak_new_password_does_not_burn_the_link(self, env, console_email):
        account = await env.account()
        await env.client.post(f"{API}/auth/password-reset/request", json={"email": account["email"]})
        token = token_from(console_email, "reset-password")

        weak = await env.client.post(
            f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": "short"}
        )
        good = await env.client.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": token, "new_password": "a-brand-new-password"},
        )
        assert weak.status_code == 422
        assert good.status_code == 200

    async def test_a_made_up_token_is_refused(self, env):
        response = await env.client.post(
            f"{API}/auth/password-reset/confirm",
            json={"token": "not-a-real-token", "new_password": "a-brand-new-password"},
        )
        assert response.status_code == 400


class TestInvites:
    async def test_an_invite_puts_the_user_in_the_org_with_the_invited_role(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")

        assert viewer["user"]["org_id"] == admin["user"]["org_id"]
        assert viewer["user"]["role"] == "Employee"
        assert viewer["user"]["is_verified"] is True

    async def test_the_role_comes_from_the_invite_not_the_request(self, env):
        admin = await env.account()
        email = env.email()
        invite = (
            await env.client.post(
                f"{API}/org/invites", headers=admin["headers"], json={"email": email, "role": "viewer"}
            )
        ).json()

        response = await env.signup(email, invite_token=invite["token"], role="Admin")
        assert response.json()["role"] == "Employee"

    async def test_the_preview_describes_the_invite(self, env, console_email):
        admin = await env.account(org_name="Preview Org")
        email = env.email()
        await env.client.post(
            f"{API}/org/invites", headers=admin["headers"], json={"email": email, "role": "editor"}
        )
        token = token_from(console_email, "login")

        preview = await env.client.get(f"{API}/auth/invites/{token}")
        assert preview.status_code == 200
        assert preview.json()["org_name"] == "Preview Org"
        assert preview.json()["role"] == "Manager"
        assert preview.json()["email"] == email

    async def test_an_invite_is_for_one_email_only(self, env):
        admin = await env.account()
        invite = (
            await env.client.post(
                f"{API}/org/invites", headers=admin["headers"], json={"email": env.email()}
            )
        ).json()

        response = await env.signup(env.email("someone-else"), invite_token=invite["token"])
        assert response.status_code == 400

    async def test_an_invite_works_once(self, env):
        admin = await env.account()
        email = env.email()
        invite = (
            await env.client.post(f"{API}/org/invites", headers=admin["headers"], json={"email": email})
        ).json()

        assert (await env.signup(email, invite_token=invite["token"])).status_code == 201
        assert (await env.signup(env.email(), invite_token=invite["token"])).status_code == 400

    async def test_a_revoked_invite_cannot_be_used(self, env):
        admin = await env.account()
        email = env.email()
        invite = (
            await env.client.post(f"{API}/org/invites", headers=admin["headers"], json={"email": email})
        ).json()

        revoke = await env.client.delete(f"{API}/org/invites/{invite['id']}", headers=admin["headers"])
        listed = await env.client.get(f"{API}/org/invites", headers=admin["headers"])

        assert revoke.status_code == 204
        assert listed.json()[0]["status"] == "revoked"
        assert "token" not in listed.json()[0]
        assert (await env.signup(email, invite_token=invite["token"])).status_code == 400

    async def test_an_expired_invite_cannot_be_used(self, env):
        from app.models import Invite

        admin = await env.account()
        email = env.email()
        invite = (
            await env.client.post(f"{API}/org/invites", headers=admin["headers"], json={"email": email})
        ).json()
        async with env.factory() as session:
            row = await session.get(Invite, uuid.UUID(invite["id"]))
            row.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
            await session.commit()

        assert (await env.signup(email, invite_token=invite["token"])).status_code == 400

    async def test_invites_are_audited(self, env):
        admin = await env.account()
        await env.member(admin, "viewer")
        actions = await env.audit_actions(admin["user"]["org_id"])
        assert "invite.create" in actions and "invite.accept" in actions


class TestRoles:
    async def test_a_viewer_can_read_but_not_create_projects(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")

        assert (await env.client.get(f"{API}/projects/", headers=viewer["headers"])).status_code == 200
        create = await env.client.post(
            f"{API}/projects/", headers=viewer["headers"], json={"name": "Nope"}
        )
        assert create.status_code == 403

    async def test_an_editor_can_create_projects(self, env):
        admin = await env.account()
        editor = await env.member(admin, "editor")
        create = await env.client.post(
            f"{API}/projects/", headers=editor["headers"], json={"name": "Yes"}
        )
        assert create.status_code == 201

    async def test_a_viewer_cannot_upload(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")
        response = await env.client.post(
            f"{API}/documents/upload",
            headers=viewer["headers"],
            data={"project_id": str(uuid.uuid4())},
            files={"files": ("a.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("role", ["viewer", "editor"])
    async def test_only_admins_reach_the_admin_endpoints(self, env, role):
        admin = await env.account()
        member = await env.member(admin, role)
        for path in ("/org/members", "/org/invites", "/org/api-keys", "/org/audit-logs"):
            response = await env.client.get(f"{API}{path}", headers=member["headers"])
            assert response.status_code == 403, path


class TestMembers:
    async def test_admins_list_their_own_org_only(self, env):
        admin = await env.account()
        await env.member(admin, "viewer")
        other = await env.account()

        mine = await env.client.get(f"{API}/org/members", headers=admin["headers"])
        theirs = await env.client.get(f"{API}/org/members", headers=other["headers"])

        assert len(mine.json()) == 2
        assert [m["email"] for m in theirs.json()] == [other["email"]]

    async def test_a_role_change_takes_effect(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")

        change = await env.client.patch(
            f"{API}/org/members/{viewer['user']['id']}", headers=admin["headers"], json={"role": "editor"}
        )
        create = await env.client.post(
            f"{API}/projects/", headers=viewer["headers"], json={"name": "Now allowed"}
        )

        assert change.status_code == 200 and change.json()["role"] == "Manager"
        assert create.status_code == 201
        assert "member.role_change" in await env.audit_actions(admin["user"]["org_id"])

    async def test_deactivation_is_immediate(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")

        await env.client.patch(
            f"{API}/org/members/{viewer['user']['id']}", headers=admin["headers"], json={"is_active": False}
        )
        me = await env.client.get(f"{API}/users/me", headers=viewer["headers"])
        refresh = await env.client.post(
            f"{API}/auth/refresh", json={"refresh_token": viewer["tokens"]["refresh_token"]}
        )

        assert me.status_code == 400
        assert refresh.status_code in (400, 401)

    async def test_you_cannot_demote_yourself(self, env):
        admin = await env.account()
        response = await env.client.patch(
            f"{API}/org/members/{admin['user']['id']}", headers=admin["headers"], json={"role": "viewer"}
        )
        assert response.status_code == 400

    async def test_the_last_admin_cannot_be_removed(self, env):
        """Two admins: one may remove the other, but the one left standing can"""
        first = await env.account()
        second = await env.member(first, "admin")

        removed = await env.client.delete(
            f"{API}/org/members/{first['user']['id']}", headers=second["headers"]
        )
        assert removed.status_code == 204

        self_remove = await env.client.delete(
            f"{API}/org/members/{second['user']['id']}", headers=second["headers"]
        )
        assert self_remove.status_code == 400

    async def test_removal_keeps_the_audit_trail(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")
        viewer_id = uuid.UUID(viewer["user"]["id"])

        response = await env.client.delete(
            f"{API}/org/members/{viewer['user']['id']}", headers=admin["headers"]
        )
        assert response.status_code == 204

        async with env.factory() as session:
            assert await session.get(User, viewer_id) is None
            accept = (
                await session.execute(select(AuditLog).where(AuditLog.action == "invite.accept",
                                                             AuditLog.org_id == uuid.UUID(admin["user"]["org_id"])))
            ).scalar_one()
            assert accept.user_id is None
        assert "member.remove" in await env.audit_actions(admin["user"]["org_id"])

    async def test_another_orgs_member_is_a_404(self, env):
        admin = await env.account()
        other = await env.account()
        response = await env.client.patch(
            f"{API}/org/members/{other['user']['id']}", headers=admin["headers"], json={"role": "viewer"}
        )
        assert response.status_code == 404


class TestAPIKeys:
    async def test_the_key_is_shown_once_and_authenticates(self, env):
        admin = await env.account()
        created = await env.client.post(
            f"{API}/org/api-keys", headers=admin["headers"], json={"name": "CI", "role": "admin"}
        )
        key = created.json()["key"]
        listed = await env.client.get(f"{API}/org/api-keys", headers=admin["headers"])
        me = await env.client.get(f"{API}/users/me", headers={"X-API-Key": key})

        assert created.status_code == 201
        assert key.startswith("dm_") and created.json()["key_prefix"] == key[:12]
        assert "key" not in listed.json()[0]
        assert me.status_code == 200 and me.json()["email"] == admin["email"]

    async def test_a_viewer_key_is_a_viewer_even_for_an_admin(self, env):
        admin = await env.account()
        key = (
            await env.client.post(f"{API}/org/api-keys", headers=admin["headers"], json={"name": "ro"})
        ).json()["key"]

        read = await env.client.get(f"{API}/projects/", headers={"X-API-Key": key})
        write = await env.client.post(
            f"{API}/projects/", headers={"X-API-Key": key}, json={"name": "Nope"}
        )
        admin_only = await env.client.get(f"{API}/org/members", headers={"X-API-Key": key})

        assert read.status_code == 200
        assert write.status_code == 403
        assert admin_only.status_code == 403

    async def test_a_revoked_key_stops_working(self, env):
        admin = await env.account()
        created = (
            await env.client.post(f"{API}/org/api-keys", headers=admin["headers"], json={"name": "x"})
        ).json()

        revoke = await env.client.delete(f"{API}/org/api-keys/{created['id']}", headers=admin["headers"])
        me = await env.client.get(f"{API}/users/me", headers={"X-API-Key": created["key"]})

        assert revoke.status_code == 204
        assert me.status_code == 401
        actions = await env.audit_actions(admin["user"]["org_id"])
        assert "api_key.create" in actions and "api_key.revoke" in actions

    async def test_a_made_up_key_is_401(self, env):
        response = await env.client.get(f"{API}/users/me", headers={"X-API-Key": "dm_nonsense"})
        assert response.status_code == 401

    async def test_a_key_is_scoped_to_its_org(self, env):
        admin = await env.account()
        other = await env.account()
        await env.client.post(f"{API}/projects/", headers=other["headers"], json={"name": "Secret"})
        key = (
            await env.client.post(f"{API}/org/api-keys", headers=admin["headers"], json={"name": "k"})
        ).json()["key"]

        visible = await env.client.get(f"{API}/projects/", headers={"X-API-Key": key})
        assert "Secret" not in [p["name"] for p in visible.json()]


class TestAuditLog:
    async def test_it_pages_and_filters(self, env):
        admin = await env.account()
        for _ in range(3):
            await env.client.post(
                f"{API}/org/invites", headers=admin["headers"], json={"email": env.email()}
            )

        everything = (await env.client.get(f"{API}/org/audit-logs", headers=admin["headers"])).json()
        page = (
            await env.client.get(f"{API}/org/audit-logs?limit=2&offset=0", headers=admin["headers"])
        ).json()
        invites = (
            await env.client.get(f"{API}/org/audit-logs?action=invite.create", headers=admin["headers"])
        ).json()
        prefix = (
            await env.client.get(f"{API}/org/audit-logs?action=invite.", headers=admin["headers"])
        ).json()

        assert everything["total"] >= 5  # signup + login + 3 invites
        assert len(page["items"]) == 2 and page["total"] == everything["total"]
        assert invites["total"] == 3
        assert {item["action"] for item in prefix["items"]} == {"invite.create"}

    async def test_another_org_sees_none_of_it(self, env):
        admin = await env.account()
        other = await env.account()
        theirs = (await env.client.get(f"{API}/org/audit-logs", headers=other["headers"])).json()
        assert all(item["user_id"] != admin["user"]["id"] for item in theirs["items"])


class TestTaskEndpointsAreLockedDown:
    async def test_a_non_admin_is_refused(self, env):
        admin = await env.account()
        viewer = await env.member(admin, "viewer")
        response = await env.client.post(
            f"{API}/tasks/trigger-add?x=1&y=2", headers=viewer["headers"]
        )
        assert response.status_code == 403

    async def test_they_do_not_exist_in_production(self, env, monkeypatch):
        admin = await env.account()
        monkeypatch.setattr(settings, "ENV", "production")
        response = await env.client.get(f"{API}/tasks/status/abc", headers=admin["headers"])
        assert response.status_code == 404

    async def test_unauthenticated_is_401(self, env):
        assert (await env.client.get(f"{API}/tasks/status/abc")).status_code == 401
