import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.webhook import FAILED, PENDING, SUCCEEDED, WebhookDelivery
from app.services import webhooks
from tests import test_auth_flows as _flows

API = _flows.API
env = _flows.env
fast_password_hashing = _flows.fast_password_hashing
console_email = _flows.console_email

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def webhooks_on(monkeypatch):
    """Switch the feature on, make every host 'public', and capture dispatches."""
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", True)
    monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [PUBLIC_IP])
    sent: list[str] = []
    monkeypatch.setattr(webhooks, "dispatch", lambda delivery_id: sent.append(delivery_id))
    return sent


class TestSigning:
    def test_a_receiver_can_verify_the_signature(self):
        body = webhooks.encode_body({"type": "ping", "data": {"x": 1}})
        signature = "sha256=" + webhooks.sign("whsec_abc", 1700000000, body)
        assert webhooks.verify_signature("whsec_abc", 1700000000, body, signature)

    def test_a_changed_body_timestamp_or_secret_fails(self):
        body = webhooks.encode_body({"type": "ping"})
        signature = "sha256=" + webhooks.sign("whsec_abc", 1700000000, body)
        assert not webhooks.verify_signature("whsec_abc", 1700000000, body + b" ", signature)
        assert not webhooks.verify_signature("whsec_abc", 1700000001, body, signature)
        assert not webhooks.verify_signature("whsec_xyz", 1700000000, body, signature)

    def test_secrets_are_random_and_prefixed(self):
        first, second = webhooks.generate_secret(), webhooks.generate_secret()
        assert first.startswith("whsec_") and first != second


class TestSSRF:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",          # loopback
            "10.0.0.5",           # private
            "192.168.1.1",        # private
            "172.16.0.1",         # private
            "169.254.169.254",    # link-local: the cloud metadata service
            "0.0.0.0",            # "this host"
            "::1",                # IPv6 loopback
            "fd00::1",            # IPv6 unique-local (private)
            "::ffff:127.0.0.1",   # IPv4-mapped loopback
            "224.0.0.1",          # multicast
        ],
    )
    def test_non_public_addresses_are_refused(self, monkeypatch, address):
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [address])
        with pytest.raises(webhooks.WebhookURLError):
            webhooks.validate_target_url("https://hooks.example.com/in")

    def test_one_private_address_among_public_ones_is_enough_to_refuse(self, monkeypatch):
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [PUBLIC_IP, "10.0.0.1"])
        with pytest.raises(webhooks.WebhookURLError):
            webhooks.validate_target_url("https://hooks.example.com/in")

    def test_a_public_https_url_is_accepted(self, monkeypatch):
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [PUBLIC_IP])
        assert webhooks.validate_target_url(" https://hooks.example.com/in ") == "https://hooks.example.com/in"

    def test_http_and_other_schemes_are_refused_by_default(self, monkeypatch):
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [PUBLIC_IP])
        for url in ("http://hooks.example.com/", "ftp://hooks.example.com/", "file:///etc/passwd"):
            with pytest.raises(webhooks.WebhookURLError):
                webhooks.validate_target_url(url)

    def test_credentials_in_the_url_are_refused(self, monkeypatch):
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: [PUBLIC_IP])
        with pytest.raises(webhooks.WebhookURLError):
            webhooks.validate_target_url("https://user:pass@hooks.example.com/")

    def test_an_unresolvable_host_is_refused(self, monkeypatch):
        import socket

        def fail(host, port):
            raise socket.gaierror("nope")

        monkeypatch.setattr(webhooks, "_resolve", fail)
        with pytest.raises(webhooks.WebhookURLError):
            webhooks.validate_target_url("https://no-such-host.invalid/")


async def _create(env, admin, **overrides):
    body = {"url": "https://hooks.example.com/in", "event_types": ["member.joined"], **overrides}
    return await env.client.post(f"{API}/webhooks", headers=admin["headers"], json=body)


class TestAdminAPI:
    async def test_every_route_is_404_while_the_feature_is_off(self, env, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)
        admin = await env.account()
        assert (await env.client.get(f"{API}/webhooks", headers=admin["headers"])).status_code == 404
        assert (await env.client.get(f"{API}/webhooks/events")).status_code == 404

    async def test_create_shows_the_secret_once_and_list_never_does(self, env, webhooks_on):
        admin = await env.account()
        created = await _create(env, admin, description="CRM")
        assert created.status_code == 201, created.text
        assert created.json()["secret"].startswith("whsec_")

        listed = await env.client.get(f"{API}/webhooks", headers=admin["headers"])
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert "secret" not in listed.json()[0]

    async def test_the_event_catalogue_is_listed(self, env, webhooks_on):
        admin = await env.account()
        events = await env.client.get(f"{API}/webhooks/events", headers=admin["headers"])
        names = {item["name"] for item in events.json()}
        assert {"document.processed", "document.failed", "member.joined"} <= names

    async def test_unknown_events_and_private_urls_are_422(self, env, webhooks_on, monkeypatch):
        admin = await env.account()
        assert (await _create(env, admin, event_types=["document.exploded"])).status_code == 422
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: ["127.0.0.1"])
        response = await _create(env, admin)
        assert response.status_code == 422
        assert "private" in response.json()["detail"]

    async def test_non_admins_are_refused(self, env, webhooks_on):
        admin = await env.account()
        editor = await env.member(admin, "Manager")
        assert (await _create(env, editor)).status_code == 403

    async def test_another_organizations_webhook_is_404(self, env, webhooks_on):
        alice, mallory = await env.account(), await env.account()
        webhook_id = (await _create(env, alice)).json()["id"]
        for method, path in (
            ("patch", f"/webhooks/{webhook_id}"),
            ("delete", f"/webhooks/{webhook_id}"),
            ("get", f"/webhooks/{webhook_id}/deliveries"),
            ("post", f"/webhooks/{webhook_id}/test"),
        ):
            kwargs = {"json": {"is_active": False}} if method == "patch" else {}
            response = await getattr(env.client, method)(f"{API}{path}", headers=mallory["headers"], **kwargs)
            assert response.status_code == 404, (method, path)

    async def test_update_rotate_and_delete(self, env, webhooks_on):
        admin = await env.account()
        created = (await _create(env, admin)).json()
        webhook_id = created["id"]

        patched = await env.client.patch(
            f"{API}/webhooks/{webhook_id}", headers=admin["headers"],
            json={"is_active": False, "event_types": ["*"]},
        )
        assert patched.status_code == 200
        assert patched.json()["is_active"] is False and patched.json()["event_types"] == ["*"]

        rotated = await env.client.post(f"{API}/webhooks/{webhook_id}/rotate-secret", headers=admin["headers"])
        assert rotated.json()["secret"] != created["secret"]

        deleted = await env.client.delete(f"{API}/webhooks/{webhook_id}", headers=admin["headers"])
        assert deleted.status_code == 204
        assert (await env.client.get(f"{API}/webhooks", headers=admin["headers"])).json() == []

    async def test_the_test_ping_is_queued_after_commit(self, env, webhooks_on):
        admin = await env.account()
        webhook_id = (await _create(env, admin)).json()["id"]
        response = await env.client.post(f"{API}/webhooks/{webhook_id}/test", headers=admin["headers"])
        assert response.status_code == 202
        assert response.json()["event_type"] == "ping"
        assert webhooks_on == [response.json()["id"]]

        log = await env.client.get(f"{API}/webhooks/{webhook_id}/deliveries", headers=admin["headers"])
        assert [d["event_type"] for d in log.json()] == ["ping"]


class TestEmitting:
    async def test_a_member_joining_emits_member_joined(self, env, webhooks_on):
        admin = await env.account()
        await _create(env, admin, event_types=["member.joined"])
        member = await env.member(admin, "Employee")

        async with env.factory() as session:
            rows = (await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.org_id == uuid.UUID(admin["user"]["org_id"]))
            )).scalars().all()
        assert [row.event_type for row in rows] == ["member.joined"]
        assert rows[0].payload["data"]["email"] == member["email"]
        assert rows[0].status == PENDING
        assert webhooks_on == [str(rows[0].id)]

    async def test_a_wildcard_subscription_gets_everything(self, env, webhooks_on):
        admin = await env.account()
        await _create(env, admin, event_types=["*"])
        await env.member(admin, "Employee")
        async with env.factory() as session:
            types = sorted((await session.execute(
                select(WebhookDelivery.event_type).where(
                    WebhookDelivery.org_id == uuid.UUID(admin["user"]["org_id"])
                )
            )).scalars().all())
        assert types == ["invite.created", "member.joined"]

    async def test_a_paused_endpoint_or_another_org_gets_nothing(self, env, webhooks_on):
        alice, bob = await env.account(), await env.account()
        webhook_id = (await _create(env, alice, event_types=["*"])).json()["id"]
        await env.client.patch(f"{API}/webhooks/{webhook_id}", headers=alice["headers"], json={"is_active": False})
        await env.member(alice, "Employee")
        await env.member(bob, "Employee")
        assert webhooks_on == []

    async def test_nothing_is_emitted_while_the_feature_is_off(self, env, webhooks_on, monkeypatch):
        admin = await env.account()
        await _create(env, admin, event_types=["*"])
        monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)
        await env.member(admin, "Employee")
        assert webhooks_on == []


async def _pending_delivery(env, admin) -> str:
    webhook_id = (await _create(env, admin)).json()["id"]
    response = await env.client.post(f"{API}/webhooks/{webhook_id}/test", headers=admin["headers"])
    return response.json()["id"], webhook_id


class TestDelivering:
    async def test_a_successful_delivery_is_signed_and_recorded(self, env, webhooks_on, monkeypatch):
        admin = await env.account()
        created = await _create(env, admin)
        secret, webhook_id = created.json()["secret"], created.json()["id"]
        delivery_id = (await env.client.post(
            f"{API}/webhooks/{webhook_id}/test", headers=admin["headers"]
        )).json()["id"]

        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        monkeypatch.setattr(webhooks, "_transport", httpx.MockTransport(handler))
        outcome = await webhooks.deliver(delivery_id, session_factory=env.factory)
        assert outcome == ("succeeded", 0)

        request = seen[0]
        assert str(request.url) == "https://hooks.example.com/in"
        assert request.headers["X-DocMinds-Event"] == "ping"
        assert request.headers["X-DocMinds-Delivery"] == delivery_id
        timestamp = int(request.headers["X-DocMinds-Timestamp"])
        assert webhooks.verify_signature(secret, timestamp, request.content, request.headers["X-DocMinds-Signature"])
        assert json.loads(request.content)["type"] == "ping"

        async with env.factory() as session:
            row = await session.get(WebhookDelivery, uuid.UUID(delivery_id))
        assert row.status == SUCCEEDED and row.attempts == 1 and row.last_status_code == 200

        assert (await webhooks.deliver(delivery_id, session_factory=env.factory))[0] == "skipped"

    async def test_failures_retry_with_backoff_then_give_up(self, env, webhooks_on, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(settings, "WEBHOOK_RETRY_BACKOFF_SECONDS", 30)
        monkeypatch.setattr(webhooks, "_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
        admin = await env.account()
        delivery_id, _ = await _pending_delivery(env, admin)

        assert await webhooks.deliver(delivery_id, session_factory=env.factory) == ("retry", 30)
        assert await webhooks.deliver(delivery_id, session_factory=env.factory) == ("retry", 60)
        assert await webhooks.deliver(delivery_id, session_factory=env.factory) == ("failed", 0)

        async with env.factory() as session:
            row = await session.get(WebhookDelivery, uuid.UUID(delivery_id))
        assert row.status == FAILED and row.attempts == 3
        assert row.last_status_code == 503 and row.last_error == "HTTP 503"

    async def test_redirects_are_not_followed(self, env, webhooks_on, monkeypatch):
        monkeypatch.setattr(
            webhooks, "_transport",
            httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1/"})),
        )
        admin = await env.account()
        delivery_id, _ = await _pending_delivery(env, admin)
        outcome, _wait = await webhooks.deliver(delivery_id, session_factory=env.factory)
        assert outcome == "retry"

    async def test_a_url_that_now_resolves_privately_is_blocked_at_delivery(self, env, webhooks_on, monkeypatch):
        admin = await env.account()
        delivery_id, _ = await _pending_delivery(env, admin)
        monkeypatch.setattr(webhooks, "_resolve", lambda host, port: ["127.0.0.1"])
        called = []
        monkeypatch.setattr(webhooks, "_transport", httpx.MockTransport(lambda r: called.append(r) or httpx.Response(200)))
        assert await webhooks.deliver(delivery_id, session_factory=env.factory) == ("failed", 0)
        assert called == []

    async def test_redeliver_resets_a_failed_delivery(self, env, webhooks_on, monkeypatch):
        monkeypatch.setattr(settings, "WEBHOOK_MAX_ATTEMPTS", 1)
        monkeypatch.setattr(webhooks, "_transport", httpx.MockTransport(lambda r: httpx.Response(500)))
        admin = await env.account()
        delivery_id, webhook_id = await _pending_delivery(env, admin)
        assert (await webhooks.deliver(delivery_id, session_factory=env.factory))[0] == "failed"

        response = await env.client.post(
            f"{API}/webhooks/{webhook_id}/deliveries/{delivery_id}/redeliver", headers=admin["headers"]
        )
        assert response.status_code == 202
        assert response.json()["status"] == "pending" and response.json()["attempts"] == 0


def test_retry_delay_doubles_and_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_RETRY_BACKOFF_SECONDS", 30)
    assert [webhooks.retry_delay_seconds(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert webhooks.retry_delay_seconds(20) == 3600


def test_every_mapped_audit_action_names_a_known_event():
    assert set(webhooks.AUDIT_EVENT_MAP.values()) <= set(webhooks.EVENT_TYPES)
