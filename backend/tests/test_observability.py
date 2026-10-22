"""
test_observability.py
=====================
WHAT THIS FILE TESTS
--------------------
    core/observability.py   JSON log lines carrying the request context;
                            Sentry staying off (and harmless) without a DSN
    core/middleware.py      the request id, the access-log line, and the
                            security headers on every response
    core/health.py          /health/ready: per-component status, 503 when a
                            HARD dependency is down, Celery only in celery mode

TIER 1: no database, Redis or Celery. Every dependency check is replaced by a
fake that succeeds, fails or hangs on demand.
"""

import asyncio
import json
import logging
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import health, observability
from app.core.config import settings
from app.main import app

API = settings.API_V1_STR


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _record(message="hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ======================================================================
# Structured logging
# ======================================================================


class TestJsonFormatter:
    def test_one_json_object_with_context_and_extras(self):
        token = observability.new_context(request_id="req-1")
        try:
            observability.bind(user_id="u-1", org_id="o-1")
            line = observability.JsonFormatter().format(_record(document_id="d-1"))
        finally:
            observability.reset_context(token)

        payload = json.loads(line)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["request_id"] == "req-1"
        assert payload["user_id"] == "u-1" and payload["org_id"] == "o-1"
        assert payload["document_id"] == "d-1"
        assert payload["time"].endswith("+00:00")

    def test_unserialisable_values_never_crash_the_logger(self):
        import uuid

        line = observability.JsonFormatter().format(_record(thing=uuid.uuid4(), obj=object()))
        assert json.loads(line)["thing"]

    def test_exceptions_are_on_the_same_line(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "app.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )
        payload = json.loads(observability.JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_context_is_per_request(self):
        token = observability.new_context(request_id="a")
        observability.reset_context(token)
        assert observability.current_request_id() is None

    def test_text_format_for_terminals(self):
        token = observability.new_context(request_id="r9")
        try:
            line = observability.TextFormatter().format(_record())
        finally:
            observability.reset_context(token)
        assert "app.test: hello" in line and "request_id=r9" in line

    def test_configure_logging_is_idempotent(self):
        root = logging.getLogger()
        observability.configure_logging()
        observability.configure_logging()
        ours = [h for h in root.handlers if getattr(h, "_docminds_handler", False)]
        assert len(ours) == 1


class TestSentry:
    def test_off_without_a_dsn(self, monkeypatch):
        monkeypatch.setattr(settings, "SENTRY_DSN", "")
        monkeypatch.setattr(observability, "_sentry_enabled", False)
        assert observability.init_sentry("api") is False

    def test_missing_package_is_a_warning_not_a_crash(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@example.invalid/1")
        monkeypatch.setattr(observability, "_sentry_enabled", False)
        # None in sys.modules makes `import sentry_sdk` raise ImportError.
        monkeypatch.setitem(sys.modules, "sentry_sdk", None)
        assert observability.init_sentry("api") is False
        assert "sentry-sdk is not installed" in caplog.text

    def test_initialised_with_safe_defaults(self, monkeypatch):
        calls = {}

        class FakeSdk:
            @staticmethod
            def init(**kwargs):
                calls.update(kwargs)

            @staticmethod
            def set_tag(key, value):
                calls.setdefault("tags", {})[key] = value

        monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@example.invalid/1")
        monkeypatch.setattr(observability, "_sentry_enabled", False)
        monkeypatch.setitem(sys.modules, "sentry_sdk", FakeSdk)
        assert observability.init_sentry("worker") is True
        assert calls["send_default_pii"] is False
        assert calls["tags"] == {"component": "worker"}
        monkeypatch.setattr(observability, "_sentry_enabled", False)


# ======================================================================
# Middleware: request id, access log, security headers
# ======================================================================


class TestRequestContext:
    async def test_a_request_id_is_generated_and_returned(self, client):
        response = await client.get(f"{API}/health")
        assert len(response.headers["X-Request-ID"]) == 32

    async def test_a_sane_incoming_id_is_kept(self, client):
        response = await client.get(f"{API}/health", headers={"X-Request-ID": "lb-abc.123"})
        assert response.headers["X-Request-ID"] == "lb-abc.123"

    async def test_a_hostile_incoming_id_is_replaced(self, client):
        response = await client.get(f"{API}/health", headers={"X-Request-ID": "x" * 500})
        assert response.headers["X-Request-ID"] != "x" * 500

    async def test_one_access_log_line_with_the_route_template(self, client, caplog):
        caplog.set_level(logging.INFO, logger="app.access")
        await client.get(f"{API}/documents/00000000-0000-0000-0000-000000000000/preview")
        lines = [r for r in caplog.records if r.name == "app.access"]
        assert len(lines) == 1
        line = lines[0]
        # The TEMPLATE, never the literal URL (which can hold secrets).
        assert line.path == f"{API}/documents/{{document_id}}/preview"
        assert line.status == 401
        assert line.method == "GET"
        assert line.duration_ms >= 0


class TestSecurityHeaders:
    async def test_every_response_carries_them(self, client):
        response = await client.get("/")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "camera=()" in response.headers["Permissions-Policy"]
        assert "Strict-Transport-Security" not in response.headers

    async def test_errors_carry_them_too(self, client):
        response = await client.get(f"{API}/no-such-endpoint")
        assert response.status_code == 404
        assert response.headers["X-Frame-Options"] == "DENY"

    async def test_hsts_in_production_only(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENV", "production")
        response = await client.get("/")
        assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")


class TestCorsExposedHeaders:
    """
    A cross-origin browser can only READ response headers the API lists in
    Access-Control-Expose-Headers. The frontend needs Retry-After to tell a
    rate-limited user how long to wait (lib/api.ts, getErrorMessage).
    """

    async def test_retry_after_and_request_id_are_readable_cross_origin(self, client):
        origin = settings.cors_origins[0]
        response = await client.get(f"{API}/health", headers={"Origin": origin})
        exposed = response.headers.get("Access-Control-Expose-Headers", "").lower()
        assert "retry-after" in exposed
        assert "x-request-id" in exposed


# ======================================================================
# Readiness
# ======================================================================


def fake_checks(monkeypatch, **outcomes):
    """outcomes: name -> "ok" | "fail" | "hang"."""

    def make(outcome):
        async def check():
            if outcome == "fail":
                raise ConnectionError("password=secret host=db.internal")
            if outcome == "hang":
                await asyncio.sleep(10)

        return check

    for name, outcome in outcomes.items():
        monkeypatch.setitem(health.CHECKS, name, make(outcome))


class TestReadiness:
    async def test_inline_mode_all_ok(self, client, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        fake_checks(monkeypatch, database="ok", redis="ok")
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert set(body["components"]) == {"database", "redis"}, "no worker in inline mode"

    async def test_redis_down_is_not_fatal_in_inline_mode(self, client, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        fake_checks(monkeypatch, database="ok", redis="fail")
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 200
        redis = response.json()["components"]["redis"]
        assert redis == {**redis, "status": "down", "required": False}

    async def test_database_down_is_503_and_leaks_nothing(self, client, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        fake_checks(monkeypatch, database="fail", redis="ok")
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 503
        assert response.json()["components"]["database"]["error"] == "ConnectionError"
        assert "secret" not in response.text

    async def test_celery_mode_needs_redis_and_a_worker(self, client, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "celery")
        fake_checks(monkeypatch, database="ok", redis="ok", celery_worker="fail")
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 503
        assert response.json()["components"]["celery_worker"]["required"] is True

    async def test_a_hanging_check_times_out(self, client, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        monkeypatch.setattr(settings, "READINESS_TIMEOUT_SECONDS", 0.05)
        fake_checks(monkeypatch, database="hang", redis="hang")
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 503
        assert response.json()["components"]["database"]["error"] == "timeout"

    async def test_liveness_touches_nothing(self, client, monkeypatch):
        fake_checks(monkeypatch, database="fail", redis="fail")
        assert (await client.get(f"{API}/health")).status_code == 200
