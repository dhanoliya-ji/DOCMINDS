# ruff: noqa: F811 -- the fixtures imported from test_upload_hardening are re-used as test arguments
"""
test_billing.py
===============
WHAT THIS FILE TESTS
--------------------
Stripe billing (services/billing.py, api/v1/billing.py) and the admin
dashboard aggregates (services/analytics.py, api/v1/analytics.py):

    * Stripe-Signature verification: good, wrong secret, stale, malformed
    * plans map onto the existing quotas; free clears the overrides
    * the webhook: checkout completed -> pro and its limits, enforced by
      services/usage.py; a redelivered event changes nothing twice;
      subscription deleted -> free; unsigned -> 400
    * Checkout and the Customer Portal against a faked Stripe, admins only
    * everything 404s while switched off
    * the dashboard: daily series with zeros filled, top questions grouped,
      tenant isolation, admins only

Runs on in-memory SQLite always, and on Postgres too when one is reachable.
"""

import datetime
import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.audit import AuditLog
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.organization import Organization
from app.models.usage import UsageEvent
from app.models.user import User
from app.services import analytics, billing, usage
from tests.test_upload_hardening import (  # noqa: F401 -- pytest fixtures
    env,
    fast_password_hashing,
    store_dir,
)

API = settings.API_V1_STR
SECRET = "whsec_test_secret"


async def _me(env) -> dict:
    return (await env.client.get(f"{API}/users/me", headers=env.headers)).json()


async def _set_role(env, role: str) -> None:
    me = await _me(env)
    async with env.factory() as db:
        user = await db.get(User, uuid.UUID(me["id"]))
        user.role = role
        await db.commit()


# ======================================================================
# Pure functions
# ======================================================================


class TestSignature:
    def test_valid(self):
        body = b'{"id": "evt_1"}'
        billing.verify_signature(body, billing.sign(body, SECRET, int(time.time())), SECRET, tolerance=300)

    def test_wrong_secret_or_body(self):
        body = b'{"id": "evt_1"}'
        header = billing.sign(body, "whsec_other", int(time.time()))
        with pytest.raises(billing.SignatureError):
            billing.verify_signature(body, header, SECRET, tolerance=300)
        header = billing.sign(body, SECRET, int(time.time()))
        with pytest.raises(billing.SignatureError):
            billing.verify_signature(body + b" ", header, SECRET, tolerance=300)

    def test_stale_and_malformed(self):
        body = b"{}"
        with pytest.raises(billing.SignatureError, match="too old"):
            billing.verify_signature(body, billing.sign(body, SECRET, int(time.time()) - 3600), SECRET, tolerance=300)
        for header in ("", "t=abc,v1=x", "v1=deadbeef"):
            with pytest.raises(billing.SignatureError):
                billing.verify_signature(body, header, SECRET, tolerance=300)
        with pytest.raises(billing.SignatureError):
            billing.verify_signature(body, billing.sign(body, "", 1), "", tolerance=300)


class TestPlans:
    def test_free_is_the_quota_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_DOCUMENTS", 7)
        assert billing.plan_limits("free")["max_documents"] == 7

    def test_apply_paid_then_free(self):
        org = Organization(name="Acme")
        billing.apply_plan(org, "pro")
        assert org.plan == "pro" and org.monthly_token_quota == settings.BILLING_PLAN_LIMITS["pro"]["monthly_tokens"]
        assert usage.token_quota(org) == org.monthly_token_quota
        billing.apply_plan(org, "free")
        assert (org.monthly_token_quota, org.max_documents, org.max_storage_bytes) == (None, None, None)

    def test_price_mapping(self, monkeypatch):
        monkeypatch.setattr(settings, "STRIPE_PRICE_TEAM", "price_team")
        assert billing.plan_for_price("price_team") == "team"
        assert billing.plan_for_price("price_unknown") is None
        assert billing.plan_for_price(None) is None


# ======================================================================
# Through the API
# ======================================================================


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setattr(settings, "STRIPE_PRICE_TEAM", "price_team")
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://front.test")


@pytest.fixture
def fake_stripe(monkeypatch):
    calls: list = []

    def handler(request: httpx.Request):
        calls.append(request)
        path = request.url.path
        if path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_123"})
        if path == "/v1/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})
        if path == "/v1/billing_portal/sessions":
            return httpx.Response(200, json={"url": "https://billing.stripe.com/p/1"})
        return httpx.Response(400, json={"error": {"message": "nope"}})

    monkeypatch.setattr(billing, "_transport", httpx.MockTransport(handler))
    return calls


async def _post_event(env, event: dict, secret: str = SECRET):
    body = json.dumps(event).encode()
    return await env.client.post(
        f"{API}/billing/webhook", content=body,
        headers={"Stripe-Signature": billing.sign(body, secret, int(time.time())), "Content-Type": "application/json"},
    )


async def _org(env) -> Organization:
    async with env.factory() as db:
        return await db.get(Organization, uuid.UUID((await _me(env))["org_id"]))


class TestBillingApi:
    async def test_404_while_off(self, env):
        assert (await env.client.get(f"{API}/billing", headers=env.headers)).status_code == 404
        assert (await env.client.post(f"{API}/billing/webhook", content=b"{}")).status_code == 404

    async def test_status_for_every_member(self, env, billing_on):
        await _set_role(env, "Employee")
        response = await env.client.get(f"{API}/billing", headers=env.headers)
        assert response.status_code == 200
        body = response.json()
        assert body["plan"] == "free" and body["has_customer"] is False
        assert [p["id"] for p in body["plans"]] == ["free", "pro", "team"]
        assert body["plans"][1]["purchasable"] is True and body["plans"][0]["purchasable"] is False

    async def test_checkout_and_portal(self, env, billing_on, fake_stripe):
        response = await env.client.post(f"{API}/billing/checkout", headers=env.headers, json={"plan": "pro"})
        assert response.status_code == 200, response.text
        assert response.json()["url"].startswith("https://checkout.stripe.com/")
        sent = dict(httpx.QueryParams(fake_stripe[-1].content.decode()))
        org = await _org(env)
        assert sent["line_items[0][price]"] == "price_pro" and sent["metadata[org_id]"] == str(org.id)
        assert sent["success_url"] == "http://front.test/settings/billing?checkout=success"
        assert org.stripe_customer_id == "cus_123"
        # The plan does NOT change until Stripe says so.
        assert org.plan == "free"

        portal = await env.client.post(f"{API}/billing/portal", headers=env.headers)
        assert portal.json()["url"] == "https://billing.stripe.com/p/1"

    async def test_portal_without_customer_is_409(self, env, billing_on, fake_stripe):
        assert (await env.client.post(f"{API}/billing/portal", headers=env.headers)).status_code == 409

    async def test_only_admins_buy(self, env, billing_on, fake_stripe):
        await _set_role(env, "Manager")
        response = await env.client.post(f"{API}/billing/checkout", headers=env.headers, json={"plan": "pro"})
        assert response.status_code == 403 and fake_stripe == []

    async def test_webhook_lifecycle(self, env, billing_on):
        org_id = str((await _org(env)).id)
        completed = {
            "id": "evt_1", "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": org_id, "customer": "cus_9", "subscription": "sub_9",
                                "payment_status": "paid", "metadata": {"org_id": org_id, "plan": "pro"}}},
        }
        first = await _post_event(env, completed)
        assert first.json()["outcome"] == "applied"
        org = await _org(env)
        assert (org.plan, org.stripe_customer_id, org.stripe_subscription_id) == ("pro", "cus_9", "sub_9")
        assert org.max_documents == settings.BILLING_PLAN_LIMITS["pro"]["max_documents"]

        assert (await _post_event(env, completed)).json()["outcome"] == "duplicate"

        upgraded = {
            "id": "evt_2", "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_9", "customer": "cus_9", "status": "active",
                                "current_period_end": 1893456000,
                                "items": {"data": [{"price": {"id": "price_team"}}]}}},
        }
        await _post_event(env, upgraded)
        org = await _org(env)
        assert org.plan == "team" and org.max_documents == 0  # 0 = unlimited
        assert org.plan_renews_at is not None

        deleted = {"id": "evt_3", "type": "customer.subscription.deleted",
                   "data": {"object": {"id": "sub_9", "customer": "cus_9", "status": "canceled"}}}
        await _post_event(env, deleted)
        org = await _org(env)
        assert (org.plan, org.max_documents, org.subscription_status) == ("free", None, "canceled")

        async with env.factory() as db:
            changes = (await db.execute(select(AuditLog).where(AuditLog.action == "billing.plan_change"))).scalars().all()
        assert [(c.details["from"], c.details["to"]) for c in changes] == [
            ("free", "pro"), ("pro", "team"), ("team", "free")]

    async def test_unsigned_or_wrongly_signed_is_refused(self, env, billing_on):
        org_id = str((await _org(env)).id)
        event = {"id": "evt_x", "type": "checkout.session.completed",
                 "data": {"object": {"metadata": {"org_id": org_id, "plan": "team"}}}}
        assert (await env.client.post(f"{API}/billing/webhook", content=json.dumps(event))).status_code == 400
        assert (await _post_event(env, event, secret="whsec_wrong")).status_code == 400
        assert (await _org(env)).plan == "free"


# ======================================================================
# Admin dashboard
# ======================================================================


class TestAnalytics:
    def test_day_range_includes_both_ends(self):
        since = datetime.datetime(2026, 2, 27, tzinfo=datetime.timezone.utc)
        now = datetime.datetime(2026, 3, 2, 12, tzinfo=datetime.timezone.utc)
        assert analytics.day_range(since, now) == ["2026-02-27", "2026-02-28", "2026-03-01", "2026-03-02"]

    async def test_404_while_off(self, env):
        assert (await env.client.get(f"{API}/analytics/overview", headers=env.headers)).status_code == 404

    async def test_overview(self, env, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_DASHBOARD_ENABLED", True)
        me = await _me(env)
        org_id, user_id = uuid.UUID(me["org_id"]), uuid.UUID(me["id"])
        async with env.factory() as db:
            db.add(UsageEvent(org_id=org_id, user_id=user_id, event_type="chat", model="m",
                              total_tokens=100, estimated_cost_usd=0.5))
            db.add(UsageEvent(org_id=org_id, user_id=user_id, event_type="chat", model="m",
                              total_tokens=50, estimated_cost_usd=0.25))
            chat = ChatSession(user_id=user_id, project_id=uuid.UUID(env.project_id), title="t")
            db.add(chat)
            await db.flush()
            for text in ("What is the leave policy?", " what is the leave policy? ", "Who approves travel?"):
                db.add(ChatMessage(session_id=chat.id, role="user", content=text))
            answer = ChatMessage(session_id=chat.id, role="assistant", content="20 days")
            db.add(answer)
            await db.flush()
            db.add(Feedback(message_id=answer.id, rating=1))
            await db.commit()

        response = await env.client.get(f"{API}/analytics/overview", headers=env.headers, params={"days": 7})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["usage"]) == 7 and body["usage"][0]["requests"] == 0
        assert body["usage"][-1] == {"date": body["usage"][-1]["date"], "requests": 2, "tokens": 150, "cost_usd": 0.75}
        assert body["totals"]["active_users"] == 1 and body["totals"]["feedback_up"] == 1
        assert body["top_questions"][0]["count"] == 2
        assert body["top_questions"][0]["question"].lower().startswith("what is the leave policy")

        series = await env.client.get(f"{API}/analytics/usage", headers=env.headers, params={"days": 3})
        assert len(series.json()) == 3

    async def test_admins_only(self, env, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_DASHBOARD_ENABLED", True)
        await _set_role(env, "Manager")
        assert (await env.client.get(f"{API}/analytics/overview", headers=env.headers)).status_code == 403
