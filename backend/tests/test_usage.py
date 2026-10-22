# ruff: noqa: F811 -- the fixtures imported from test_upload_hardening are re-used as test arguments
"""
test_usage.py
=============
WHAT THIS FILE TESTS
--------------------
Metering and quotas (services/usage.py) through the real endpoints:

    * every chat answer records a usage event -- model, mode, tokens in and
      out, estimated cost, request id -- in the same transaction as the answer
    * an organization over its monthly token quota gets 429 + Retry-After,
      and nothing is saved or sent to a model
    * uploads that would cross the document or storage quota get 402 and
      store nothing
    * per-organization overrides beat the QUOTA_* settings; 0 means unlimited
    * GET /org/usage summarises the month, admins only

Runs on in-memory SQLite always, and on Postgres too when one is reachable
(CI) -- the same harness as test_upload_hardening.py, whose fixtures it reuses.
"""

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.organization import Organization
from app.models.usage import UsageEvent
from app.services import usage
from app.services.retrieval import RetrievalService
from tests.test_upload_hardening import (  # noqa: F401 -- pytest fixtures
    TEXT,
    env,
    fast_password_hashing,
    queued,
    store_dir,
)

API = settings.API_V1_STR


# ======================================================================
# Pure functions
# ======================================================================


class TestPricing:
    def test_cost_uses_separate_input_and_output_prices(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PRICING", {"m": {"input": 0.15, "output": 0.60}})
        # 3,000 x $0.15/1M + 400 x $0.60/1M = $0.00069 (the docstring example)
        assert usage.estimate_cost_usd("m", 3000, 400) == pytest.approx(0.00069)

    def test_unpriced_models_cost_zero_and_are_logged_once(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "LLM_PRICING", {})
        monkeypatch.setattr(usage, "_unpriced_models", set())
        assert usage.estimate_cost_usd("new-model", 10, 10) == 0
        usage.estimate_cost_usd("new-model", 10, 10)
        assert caplog.text.count("No price for this model") == 1
        assert usage.estimate_cost_usd("mock", 0, 0) == 0

    def test_month_bounds(self):
        start, end = usage.month_bounds(datetime.datetime(2026, 2, 14, 9, tzinfo=datetime.timezone.utc))
        assert start == datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
        assert end == datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)

    def test_override_beats_setting_and_zero_is_unlimited(self, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MONTHLY_TOKENS", 500)
        assert usage.token_quota(None) == 500
        assert usage.token_quota(Organization(name="a", monthly_token_quota=9)) == 9
        assert usage.token_quota(Organization(name="b", monthly_token_quota=0)) == 0

    def test_allowance_counts_files_as_they_are_admitted(self):
        allowance = usage.UploadAllowance(documents=1, bytes_used=0, max_documents=2, max_bytes=0)
        allowance.admit(10)
        with pytest.raises(usage.QuotaExceeded) as refused:
            allowance.admit(10)
        assert refused.value.status_code == 402

    def test_storage_message_is_readable(self):
        allowance = usage.UploadAllowance(documents=0, bytes_used=1024, max_documents=0, max_bytes=2048)
        with pytest.raises(usage.QuotaExceeded) as refused:
            allowance.admit(4096)
        assert "2.0 KB" in refused.value.message and "1.0 KB" in refused.value.message


# ======================================================================
# Through the API
# ======================================================================


async def _org_id(env) -> uuid.UUID:
    me = await env.client.get(f"{API}/users/me", headers=env.headers)
    return uuid.UUID(me.json()["org_id"])


async def _chat_session(env) -> str:
    response = await env.client.post(
        f"{API}/chat/sessions", headers=env.headers, json={"project_id": env.project_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def no_vectors(monkeypatch):
    """SQLite has no pgvector: retrieval returns nothing, the answer is mock."""

    async def search(**kwargs):
        return []

    monkeypatch.setattr(RetrievalService, "search", staticmethod(search))


class TestChatMetering:
    async def test_each_answer_records_a_usage_event(self, env, no_vectors, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
        session_id = await _chat_session(env)

        response = await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages",
            headers={**env.headers, "X-Request-ID": "req-usage-1"},
            json={"message": "How much leave?"},
        )
        assert response.status_code == 200, response.text

        async with env.factory() as db:
            events = (await db.execute(select(UsageEvent))).scalars().all()
        mine = [e for e in events if e.request_id == "req-usage-1"]
        assert len(mine) == 1
        event = mine[0]
        assert (event.event_type, event.mode, event.model) == ("chat", "rag", "mock")
        assert event.org_id == await _org_id(env)
        assert event.total_tokens == 0 and event.estimated_cost_usd == 0

    async def test_over_quota_is_429_and_nothing_is_saved(self, env, no_vectors, monkeypatch):
        org_id = await _org_id(env)
        session_id = await _chat_session(env)
        async with env.factory() as db:
            (await db.get(Organization, org_id)).monthly_token_quota = 1000
            db.add(UsageEvent(org_id=org_id, event_type="chat", mode="rag", model="m",
                              prompt_tokens=900, completion_tokens=100, total_tokens=1000))
            await db.commit()

        response = await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages",
            headers=env.headers,
            json={"message": "One more?"},
        )

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert "monthly AI quota of 1,000 tokens" in response.json()["detail"]
        messages = await env.client.get(
            f"{API}/chat/sessions/{session_id}/messages", headers=env.headers
        )
        assert messages.json() == [], "a refused question must not be stored"

    async def test_last_months_usage_does_not_count(self, env, no_vectors, monkeypatch):
        org_id = await _org_id(env)
        long_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
        async with env.factory() as db:
            (await db.get(Organization, org_id)).monthly_token_quota = 1000
            db.add(UsageEvent(org_id=org_id, event_type="chat", mode="rag", model="m",
                              total_tokens=5000, created_at=long_ago))
            await db.commit()
            await usage.check_token_quota(db, org_id)  # does not raise

    async def test_zero_means_unlimited(self, env):
        org_id = await _org_id(env)
        async with env.factory() as db:
            (await db.get(Organization, org_id)).monthly_token_quota = 0
            db.add(UsageEvent(org_id=org_id, event_type="chat", model="m", total_tokens=10**9))
            await db.commit()
            await usage.check_token_quota(db, org_id)


class TestUploadQuotas:
    async def test_document_quota_is_402_and_stores_nothing(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_DOCUMENTS", 1)
        first = await env.upload(("a.txt", TEXT))
        assert first.status_code == 202, first.text

        refused = await env.upload(("b.txt", TEXT + b" different"))
        assert refused.status_code == 402
        assert "limit of 1 documents" in refused.json()["detail"]
        assert len(await env.documents()) == 1
        assert len(env.stored_files()) == 1

    async def test_a_batch_that_would_cross_the_limit_is_refused_whole(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_DOCUMENTS", 2)
        refused = await env.upload(("a.txt", TEXT), ("b.txt", TEXT + b"1"), ("c.txt", TEXT + b"2"))
        assert refused.status_code == 402
        assert await env.documents() == []
        assert env.stored_files() == [], "files stored before the refusal are removed"

    async def test_storage_quota(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_STORAGE_BYTES", len(TEXT) + 10)
        assert (await env.upload(("a.txt", TEXT))).status_code == 202
        refused = await env.upload(("b.txt", TEXT + b"x"))
        assert refused.status_code == 402
        assert "storage limit" in refused.json()["detail"]

    async def test_duplicates_do_not_count(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_DOCUMENTS", 1)
        assert (await env.upload(("a.txt", TEXT))).status_code == 202
        # The same bytes again: a duplicate (400), not a quota refusal (402).
        assert (await env.upload(("a-copy.txt", TEXT))).status_code == 400

    async def test_per_org_override(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "QUOTA_MAX_DOCUMENTS", 1)
        org_id = await _org_id(env)
        async with env.factory() as db:
            (await db.get(Organization, org_id)).max_documents = 0  # unlimited
            await db.commit()
        for n in range(3):
            assert (await env.upload((f"{n}.txt", TEXT + str(n).encode()))).status_code == 202


class TestUsageSummary:
    async def test_admin_sees_the_month(self, env, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PRICING", {"m1": {"input": 1.0, "output": 2.0}})
        monkeypatch.setattr(settings, "QUOTA_MONTHLY_TOKENS", 10_000)
        org_id = await _org_id(env)
        async with env.factory() as db:
            for mode, model, p, c in [("rag", "m1", 1000, 500), ("agent", "m1", 2000, 0), ("rag", "m2", 10, 10)]:
                usage.record_chat_usage(db, org_id=org_id, user_id=None, mode=mode, model=model,
                                        prompt_tokens=p, completion_tokens=c, total_tokens=0)
            await db.commit()

        response = await env.client.get(f"{API}/org/usage", headers=env.headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["requests"] == 3
        assert body["prompt_tokens"] == 3010 and body["completion_tokens"] == 510
        assert body["tokens"] == {"used": 3520, "quota": 10_000, "unlimited": False, "remaining": 6480}
        # (1000 + 2000) x $1/1M + 500 x $2/1M = $0.004; m2 is unpriced.
        assert body["estimated_cost_usd"] == pytest.approx(0.004)
        assert body["by_model"][0]["model"] == "m1"
        assert {row["mode"]: row["requests"] for row in body["by_mode"]} == {"agent": 1, "rag": 2}
        assert body["documents"]["used"] == 0

    async def test_other_orgs_usage_is_invisible(self, env):
        async with env.factory() as db:
            other = Organization(name="Other")
            db.add(other)
            await db.flush()
            env.created_orgs.append(str(other.id))
            db.add(UsageEvent(org_id=other.id, event_type="chat", model="m", total_tokens=99))
            await db.commit()
        body = (await env.client.get(f"{API}/org/usage", headers=env.headers)).json()
        assert body["tokens"]["used"] == 0
