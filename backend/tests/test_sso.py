import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.api.v1 import sso
from app.core.config import settings
from app.models import User
from tests import test_auth_flows as _flows

API = _flows.API
env = _flows.env
fast_password_hashing = _flows.fast_password_hashing
console_email = _flows.console_email

ISSUER = "https://idp.example.com"
CLIENT_ID = "docminds-test-client"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeProvider:
    """Answers discovery, JWKS and token requests; the test sets the claims."""

    def __init__(self):
        self.claims: dict = {}
        self.audience = CLIENT_ID
        self.token_requests: list[dict] = []

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": self.audience, "sub": "user-123",
            "iat": now, "exp": now + 300, "nonce": nonce,
            "email_verified": True, **self.claims,
        }
        return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": "key-1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            })
        if path == "/jwks":
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key(), as_dict=True)
            jwk.update({"kid": "key-1", "use": "sig", "alg": "RS256"})
            return httpx.Response(200, json={"keys": [jwk]})
        if path == "/token":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            nonce = form["code"][0]
            return httpx.Response(200, json={"id_token": self.id_token(nonce), "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(settings, "OIDC_ENABLED", True)
    monkeypatch.setattr(settings, "OIDC_DISCOVERY_URL", f"{ISSUER}/.well-known/openid-configuration")
    monkeypatch.setattr(settings, "OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "OIDC_CLIENT_SECRET", "shh")
    monkeypatch.setattr(settings, "OIDC_ALLOW_SIGNUP", True)
    monkeypatch.setattr(settings, "OIDC_REQUIRE_EMAIL_VERIFIED", True)
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://frontend.test")
    monkeypatch.setattr(sso, "_transport", httpx.MockTransport(fake.handler))
    sso.clear_metadata_cache()
    yield fake
    sso.clear_metadata_cache()


def _fragment(response) -> dict:
    location = response.headers["location"]
    assert location.startswith("http://frontend.test/login/sso#"), location
    return {k: v[0] for k, v in parse_qs(urlsplit(location).fragment).items()}


async def sso_sign_in(env, provider, email: str, *, invite: str | None = None,
                      nonce_override: str | None = None, keep_cookie: bool = True) -> dict:
    """Run login -> callback and return the fragment the frontend receives."""
    provider.claims["email"] = email
    params = {"invite": invite} if invite else {}
    start = await env.client.get(f"{API}/auth/sso/login", params=params)
    assert start.status_code == 303, start.text
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    if not keep_cookie:
        env.client.cookies.clear()
    code = nonce_override or query["nonce"][0]
    callback = await env.client.get(
        f"{API}/auth/sso/callback", params={"code": code, "state": query["state"][0]}
    )
    assert callback.status_code == 303, callback.text
    return _fragment(callback)


async def exchange(env, code: str):
    return await env.client.post(f"{API}/auth/sso/exchange", json={"code": code})


class TestOffByDefault:
    async def test_config_says_disabled_and_routes_404(self, env, monkeypatch):
        monkeypatch.setattr(settings, "OIDC_ENABLED", False)
        config = await env.client.get(f"{API}/auth/sso/config")
        assert config.json()["enabled"] is False
        assert (await env.client.get(f"{API}/auth/sso/login")).status_code == 404
        assert (await exchange(env, "x")).status_code == 404

    async def test_enabled_without_credentials_is_still_off(self, env, monkeypatch):
        monkeypatch.setattr(settings, "OIDC_ENABLED", True)
        monkeypatch.setattr(settings, "OIDC_CLIENT_SECRET", "")
        assert (await env.client.get(f"{API}/auth/sso/config")).json()["enabled"] is False


class TestSignIn:
    async def test_a_new_user_gets_their_own_org_and_tokens(self, env, provider):
        email = env.email("sso")
        result = await sso_sign_in(env, provider, email)
        assert "code" in result, result

        tokens = await exchange(env, result["code"])
        assert tokens.status_code == 200, tokens.text
        me = await env.client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"}
        )
        assert me.json()["email"] == email
        assert me.json()["role"] == "Admin"
        env.created_orgs.append(me.json()["org_id"])

        sent = provider.token_requests[0]
        assert sent["client_id"] == [CLIENT_ID] and "code_verifier" in sent

    async def test_the_handoff_code_works_once(self, env, provider):
        result = await sso_sign_in(env, provider, env.email("sso"))
        first = await exchange(env, result["code"])
        assert first.status_code == 200
        async with env.factory() as session:
            user = (await session.execute(select(User).where(User.email == provider.claims["email"]))).scalar_one()
            env.created_orgs.append(str(user.org_id))
        assert (await exchange(env, result["code"])).status_code == 400

    async def test_an_existing_password_account_is_linked(self, env, provider):
        account = await env.account()
        result = await sso_sign_in(env, provider, account["email"].upper())
        tokens = (await exchange(env, result["code"])).json()
        me = await env.client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.json()["id"] == account["user"]["id"]
        assert me.json()["org_id"] == account["user"]["org_id"]

    async def test_an_invite_joins_that_org_with_the_invites_role(self, env, provider):
        admin = await env.account()
        email = env.email("invitee")
        invite = await env.client.post(
            f"{API}/org/invites", headers=admin["headers"], json={"email": email, "role": "Manager"}
        )
        result = await sso_sign_in(env, provider, email, invite=invite.json()["token"])
        tokens = (await exchange(env, result["code"])).json()
        me = await env.client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.json()["org_id"] == admin["user"]["org_id"]
        assert me.json()["role"] == "Manager"
        assert "invite.accept" in await env.audit_actions(admin["user"]["org_id"])

    async def test_an_invite_for_another_email_is_refused(self, env, provider):
        admin = await env.account()
        invite = await env.client.post(
            f"{API}/org/invites", headers=admin["headers"],
            json={"email": env.email("someone"), "role": "Employee"},
        )
        result = await sso_sign_in(env, provider, env.email("other"), invite=invite.json()["token"])
        assert "different email" in result["error"]

    async def test_no_account_and_signup_off_is_refused(self, env, provider, monkeypatch):
        monkeypatch.setattr(settings, "OIDC_ALLOW_SIGNUP", False)
        result = await sso_sign_in(env, provider, env.email("nobody"))
        assert "invite" in result["error"]


class TestGuards:
    async def test_an_unverified_email_is_refused(self, env, provider):
        provider.claims["email_verified"] = False
        result = await sso_sign_in(env, provider, env.email("unverified"))
        assert "verified" in result["error"]

    async def test_the_verified_check_can_be_waived_for_entra(self, env, provider, monkeypatch):
        monkeypatch.setattr(settings, "OIDC_REQUIRE_EMAIL_VERIFIED", False)
        provider.claims["email_verified"] = False
        result = await sso_sign_in(env, provider, env.email("entra"))
        assert "code" in result
        async with env.factory() as session:
            user = (await session.execute(select(User).where(User.email == provider.claims["email"]))).scalar_one()
            env.created_orgs.append(str(user.org_id))

    async def test_a_wrong_nonce_is_refused(self, env, provider):
        result = await sso_sign_in(env, provider, env.email("n"), nonce_override="not-the-nonce")
        assert "error" in result

    async def test_a_token_for_another_client_is_refused(self, env, provider):
        provider.audience = "some-other-app"
        result = await sso_sign_in(env, provider, env.email("aud"))
        assert "error" in result

    async def test_a_missing_browser_cookie_is_refused(self, env, provider):
        result = await sso_sign_in(env, provider, env.email("csrf"), keep_cookie=False)
        assert "different browser" in result["error"]

    async def test_a_forged_state_is_refused(self, env, provider):
        forged = jwt.encode({"type": "oidc_state", "exp": int(time.time()) + 60}, "wrong-key", algorithm="HS256")
        response = await env.client.get(f"{API}/auth/sso/callback", params={"code": "c", "state": forged})
        assert "expired" in _fragment(response)["error"]

    async def test_provider_errors_come_back_as_a_message(self, env, provider):
        response = await env.client.get(f"{API}/auth/sso/callback", params={"error": "access_denied"})
        assert "cancelled" in _fragment(response)["error"]

    async def test_a_bogus_exchange_code_is_400(self, env, provider):
        assert (await exchange(env, uuid.uuid4().hex)).status_code == 400
