"""
test_rate_limit.py
==================
WHAT THIS FILE TESTS
--------------------
core/rate_limit.py (the sliding-window limiter), the RATE_LIMIT_* settings,
core/client_ip.py (which address a request is counted against), and the
429 + Retry-After an endpoint returns once a caller is over the limit.

TIER 1: no Redis, no database. A fake clock makes the window arithmetic
exact, and a fake Redis client exercises the shared-counter path.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api import deps
from app.core import rate_limit
from app.core.client_ip import client_ip_from_scope
from app.core.config import Settings, parse_rate_limit, settings
from app.core.rate_limit import (
    RateLimiter,
    refusal_message,
    seconds_until_allowed,
    weighted_count,
)
from app.main import app

API = settings.API_V1_STR


class Clock:
    def __init__(self, now: float = 1_000_030.0):  # 10s into a minute
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rate_limits_on(monkeypatch):
    """Switch limiting back on (conftest switches it off for every test)."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()


# ======================================================================
# Parsing "20/minute"
# ======================================================================


class TestParsing:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("20/minute", (20, 60)),
            ("10/hour", (10, 3600)),
            ("5/15minutes", (5, 900)),
            (" 3 / second ", (3, 1)),
            ("100/day", (100, 86400)),
            ("7/m", (7, 60)),
        ],
    )
    def test_valid_limits(self, text, expected):
        assert parse_rate_limit(text) == expected

    @pytest.mark.parametrize("text", ["", "off", "OFF", "none", "disabled"])
    def test_off(self, text):
        assert parse_rate_limit(text) is None

    @pytest.mark.parametrize("text", ["20/minuet", "twenty/minute", "20", "0/minute", "20/0minutes"])
    def test_garbage_is_an_error_not_unlimited(self, text):
        with pytest.raises(ValueError):
            parse_rate_limit(text)

    def test_a_typo_stops_the_app_starting(self):
        with pytest.raises(ValidationError, match="RATE_LIMIT_LOGIN"):
            Settings(RATE_LIMIT_LOGIN="20/minuet")

    def test_negative_quota_is_refused(self):
        with pytest.raises(ValidationError, match="QUOTA_MONTHLY_TOKENS"):
            Settings(QUOTA_MONTHLY_TOKENS=-1)

    def test_unknown_limit_name_fails_at_import_time(self):
        with pytest.raises(ValueError):
            deps.rate_limit_by_ip("logn")


# ======================================================================
# The sliding-window arithmetic
# ======================================================================


class TestArithmetic:
    def test_the_docstring_example(self):
        # previous 20, current 4, 15s into a 60s window -> 20 x 45/60 + 4 = 19
        assert weighted_count(20, 4, 15, 60) == pytest.approx(19)

    def test_previous_window_fades_out(self):
        assert weighted_count(20, 0, 60, 60) == 0
        assert weighted_count(20, 0, 0, 60) == 20

    def test_no_wait_when_there_is_room(self):
        assert seconds_until_allowed(0, 3, 10, 60, 5) == 0

    def test_full_current_window_waits_into_the_next(self):
        # 5 of 5 used 10s in: the rest of this window (50s) plus the time for
        # this window's share to fade below 4 in the next: 60 x (1 - 4/5) = 12.
        assert seconds_until_allowed(0, 5, 10, 60, 5) == pytest.approx(62)

    def test_refusal_message_names_the_limit_and_the_wait(self):
        assert refusal_message("login", 42) == (
            "Too many sign-in attempts from this address. Please try again in 42 seconds."
        )
        assert "3 minutes" in refusal_message("chat", 150)
        assert "2 hours" in refusal_message("signup", 7200)


# ======================================================================
# The limiter, in memory
# ======================================================================


class TestMemoryLimiter:
    async def test_allows_up_to_the_limit_then_refuses(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
        limiter = RateLimiter(clock=Clock())

        decisions = [await limiter.hit("login", "ip:1.2.3.4", 3, 60) for _ in range(4)]

        assert [d.allowed for d in decisions] == [True, True, True, False]
        assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
        refused = decisions[-1]
        assert refused.retry_after >= 1
        assert refused.remaining == 0

    async def test_callers_are_counted_separately(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
        limiter = RateLimiter(clock=Clock())
        for _ in range(2):
            await limiter.hit("login", "ip:a", 2, 60)
        assert not (await limiter.hit("login", "ip:a", 2, 60)).allowed
        assert (await limiter.hit("login", "ip:b", 2, 60)).allowed

    async def test_refused_requests_are_not_counted(self, monkeypatch):
        """Hammering while refused must not push the reopening further out."""
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
        clock = Clock()
        limiter = RateLimiter(clock=clock)
        for _ in range(2):
            await limiter.hit("login", "ip:a", 2, 60)
        first = await limiter.hit("login", "ip:a", 2, 60)
        for _ in range(50):
            await limiter.hit("login", "ip:a", 2, 60)
        again = await limiter.hit("login", "ip:a", 2, 60)
        assert not first.allowed and not again.allowed
        assert again.retry_after == first.retry_after

    async def test_waiting_retry_after_is_enough(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
        clock = Clock()
        limiter = RateLimiter(clock=clock)
        for _ in range(5):
            await limiter.hit("chat", "user:1", 5, 60)
        refused = await limiter.hit("chat", "user:1", 5, 60)
        assert not refused.allowed

        clock.now += refused.retry_after
        assert (await limiter.hit("chat", "user:1", 5, 60)).allowed

    async def test_no_boundary_burst(self, monkeypatch):
        """The fixed-window flaw: a full window at :59 and another at :00."""
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
        clock = Clock(60 * 16667 - 1)  # one second before a window boundary
        limiter = RateLimiter(clock=clock)
        for _ in range(10):
            assert (await limiter.hit("chat", "u", 10, 60)).allowed
        clock.now += 2  # the next window has just begun
        assert not (await limiter.hit("chat", "u", 10, 60)).allowed

    async def test_check_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
        assert await rate_limit.check("login", "ip:x") is None

    async def test_one_limit_can_be_switched_off(self, rate_limits_on, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH", "off")
        assert await rate_limit.check("search", "user:x") is None
        assert await rate_limit.check("chat", "user:x") is not None


# ======================================================================
# The limiter, with Redis -- and without it
# ======================================================================


class FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    def get(self, key):
        self.ops.append(("get", key))

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "incr":
                self.store.data[op[1]] = self.store.data.get(op[1], 0) + 1
                results.append(self.store.data[op[1]])
            elif op[0] == "expire":
                self.store.ttls[op[1]] = op[2]
                results.append(True)
            else:
                results.append(self.store.data.get(op[1]))
        return results


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    async def decr(self, key):
        self.data[key] -= 1


class BrokenRedis:
    def pipeline(self, transaction=True):
        raise ConnectionError("redis is down")


class TestRedisPath:
    async def test_counts_in_redis_when_reachable(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "auto")
        fake = FakeRedis()
        limiter = RateLimiter(clock=Clock(), redis_factory=lambda: fake)

        for _ in range(2):
            assert (await limiter.hit("login", "ip:a", 2, 60)).allowed
        assert not (await limiter.hit("login", "ip:a", 2, 60)).allowed

        (key,) = fake.data
        assert key.startswith("docminds:ratelimit:login:60:ip:a:")
        assert fake.data[key] == 2, "the refused request was undone"
        assert fake.ttls[key] == 120, "keys expire after two windows"

    async def test_falls_back_to_memory_when_redis_fails(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "auto")
        limiter = RateLimiter(clock=Clock(), redis_factory=BrokenRedis)

        decisions = [await limiter.hit("login", "ip:a", 2, 60) for _ in range(3)]

        assert [d.allowed for d in decisions] == [True, True, False]
        assert "cannot reach Redis" in caplog.text
        # Logged once on the transition, not on every request.
        assert caplog.text.count("cannot reach Redis") == 1

    async def test_memory_setting_never_touches_redis(self, monkeypatch):
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")

        def explode():
            raise AssertionError("Redis must not be used")

        limiter = RateLimiter(clock=Clock(), redis_factory=explode)
        assert (await limiter.hit("login", "ip:a", 2, 60)).allowed


# ======================================================================
# Which address is "the client"?
# ======================================================================


def scope(peer="10.0.0.1", headers=()):
    return {
        "type": "http",
        "client": (peer, 1234),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }


class TestClientIp:
    def test_default_is_the_tcp_peer_and_ignores_forwarded_for(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 0)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
        s = scope(headers=[("X-Forwarded-For", "6.6.6.6")])
        assert client_ip_from_scope(s) == "10.0.0.1"

    def test_one_trusted_proxy_takes_the_rightmost_entry(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
        # The client claimed 6.6.6.6; our proxy appended the real 203.0.113.7.
        s = scope(headers=[("X-Forwarded-For", "6.6.6.6, 203.0.113.7")])
        assert client_ip_from_scope(s) == "203.0.113.7"

    def test_too_few_hops_falls_back_to_the_peer(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 2)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
        s = scope(headers=[("X-Forwarded-For", "203.0.113.7")])
        assert client_ip_from_scope(s) == "10.0.0.1"

    def test_garbage_is_ignored(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
        s = scope(headers=[("X-Forwarded-For", "not-an-ip\r\nevil")])
        assert client_ip_from_scope(s) == "10.0.0.1"

    def test_a_cdn_header_wins(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "CF-Connecting-IP")
        s = scope(headers=[("CF-Connecting-IP", "198.51.100.9"), ("X-Forwarded-For", "1.1.1.1")])
        assert client_ip_from_scope(s) == "198.51.100.9"

    def test_ports_are_stripped(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
        monkeypatch.setattr(settings, "CLIENT_IP_HEADER", "")
        assert client_ip_from_scope(scope(headers=[("X-Forwarded-For", "203.0.113.7:5000")])) == "203.0.113.7"
        assert client_ip_from_scope(scope(headers=[("X-Forwarded-For", "[2001:db8::1]:443")])) == "2001:db8::1"


# ======================================================================
# The HTTP behaviour: 429 + Retry-After
# ======================================================================


@pytest.fixture
async def client():
    async def no_db():
        yield None

    app.dependency_overrides[deps.get_db] = no_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.pop(deps.get_db, None)


class TestEndpoints:
    async def test_login_is_limited_per_ip_with_retry_after(self, rate_limits_on, monkeypatch, client):
        monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "2/minute")
        # No form body at all: the first two get the ordinary 422, which
        # proves the limiter runs independently of the endpoint's own work.
        first = await client.post(f"{API}/auth/login")
        second = await client.post(f"{API}/auth/login")
        third = await client.post(f"{API}/auth/login")

        assert first.status_code == 422 and second.status_code == 422
        assert third.status_code == 429
        assert int(third.headers["Retry-After"]) >= 1
        assert third.headers["X-RateLimit-Limit"] == "2"
        assert third.headers["X-RateLimit-Remaining"] == "0"
        assert "sign-in attempts" in third.json()["detail"]

    async def test_another_address_is_not_affected(self, rate_limits_on, monkeypatch, client):
        monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "1/minute")
        monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
        a = {"X-Forwarded-For": "203.0.113.1"}
        b = {"X-Forwarded-For": "203.0.113.2"}
        await client.post(f"{API}/auth/login", headers=a)
        assert (await client.post(f"{API}/auth/login", headers=a)).status_code == 429
        assert (await client.post(f"{API}/auth/login", headers=b)).status_code == 422

    async def test_signup_and_password_reset_are_limited(self, rate_limits_on, monkeypatch, client):
        monkeypatch.setattr(settings, "RATE_LIMIT_SIGNUP", "1/hour")
        monkeypatch.setattr(settings, "RATE_LIMIT_PASSWORD_RESET", "1/hour")
        await client.post(f"{API}/auth/signup", json={})
        assert (await client.post(f"{API}/auth/signup", json={})).status_code == 429
        await client.post(f"{API}/auth/password-reset/request", json={})
        # Request and confirm share one budget.
        assert (await client.post(f"{API}/auth/password-reset/confirm", json={})).status_code == 429

    async def test_chat_search_upload_are_limited_per_user(self, rate_limits_on, monkeypatch, client):
        import uuid
        from types import SimpleNamespace

        alice = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), is_active=True, role="admin")
        app.dependency_overrides[deps.get_current_active_user] = lambda: alice
        try:
            for name in ("RATE_LIMIT_CHAT", "RATE_LIMIT_SEARCH", "RATE_LIMIT_UPLOAD"):
                monkeypatch.setattr(settings, name, "1/minute")
            targets = [
                f"{API}/chat/sessions/{uuid.uuid4()}/messages",
                f"{API}/search/",
                f"{API}/documents/upload",
            ]
            for url in targets:
                await client.post(url)  # the one allowed request (a 422)
                refused = await client.post(url)
                assert refused.status_code == 429, url
                assert "Retry-After" in refused.headers
        finally:
            app.dependency_overrides.pop(deps.get_current_active_user, None)

    async def test_unauthenticated_requests_never_reach_the_counter(self, rate_limits_on, monkeypatch, client):
        monkeypatch.setattr(settings, "RATE_LIMIT_SEARCH", "1/minute")
        for _ in range(3):
            assert (await client.post(f"{API}/search/", json={"query": "x"})).status_code == 401


class TestVerifyResendLimit:
    async def test_verify_email_resend_is_limited_per_user(self, rate_limits_on, monkeypatch, client):
        import uuid
        from types import SimpleNamespace

        # Already verified, so the endpoint returns without touching the
        # database or sending email -- only the limiter is under test.
        bob = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), is_active=True,
                              role="member", is_verified=True)
        app.dependency_overrides[deps.get_current_active_user] = lambda: bob
        try:
            monkeypatch.setattr(settings, "RATE_LIMIT_VERIFY_RESEND", "1/hour")
            assert (await client.post(f"{API}/auth/verify-email/resend")).status_code == 202
            refused = await client.post(f"{API}/auth/verify-email/resend")
            assert refused.status_code == 429
            assert "verification emails" in refused.json()["detail"]
        finally:
            app.dependency_overrides.pop(deps.get_current_active_user, None)
