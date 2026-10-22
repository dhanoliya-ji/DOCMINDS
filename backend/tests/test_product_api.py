"""
test_product_api.py
===================
WHAT THIS FILE TESTS
--------------------
The batch-2 product endpoints over real HTTP requests:

    projects    get one (with counts), rename, delete -- editor+, audited,
                tenant-isolated, and a delete that takes the documents, their
                chunks, the conversations AND the stored files with it
    documents   get one, rename (extension kept), delete (chunks and file
                gone), re-index (back to pending, queued, 409 while busy,
                410 when quarantined), the text around a cited chunk
    chat        rename / list-by-project / export a conversation; the
                streaming endpoint's events in both modes, and that the
                answer it saves carries mode, model and the agent's steps;
                the document filter reaching retrieval
    feedback    the admin summary, and that it is admin-only

HOW IT RUNS
-----------
Like test_upload_hardening.py: every test runs on in-memory SQLite always,
and on Postgres too when one is reachable (CI). Retrieval is faked -- its
pgvector query is test_retrieval_db.py's job -- and the model is the mock
provider, so answers are deterministic.
"""

import datetime
import hashlib
import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.api import deps
from app.api.v1 import chat as chat_module
from app.core import security
from app.core.config import settings
from app.main import app
from app.models.audit import AuditLog
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.organization import Organization
from app.models.user import User
from app.services import storage as storage_module
from app.services.retrieval import RetrievalService
from app.tasks import ingestion
from tests.conftest import database_available
from tests.sqlite_harness import make_sqlite_factory

API = settings.API_V1_STR
TEXT = ("Refunds are available within thirty days of purchase. " * 40).encode()


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
    monkeypatch.setattr(
        security, "get_password_hash", lambda p: "test$" + hashlib.sha256(p.encode()).hexdigest()
    )
    monkeypatch.setattr(
        security,
        "verify_password",
        lambda p, h: h == "test$" + hashlib.sha256(p.encode()).hexdigest(),
    )


@pytest.fixture(autouse=True)
def no_rate_limits(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


@pytest.fixture
def store_dir(monkeypatch, tmp_path):
    root = tmp_path / "store"
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(root))
    storage_module.reset_storage_cache()
    yield root
    storage_module.reset_storage_cache()


@pytest.fixture
def queued(monkeypatch):
    """Celery mode with `.delay` replaced by a spy -- no Redis needed."""
    calls: list[str] = []
    monkeypatch.setattr(settings, "INGESTION_MODE", "celery")
    monkeypatch.setattr(ingestion.process_document_task, "delay", lambda document_id: calls.append(document_id))
    return calls


@pytest.fixture
def searches(monkeypatch):
    """RetrievalService.search replaced by a fake that records its calls."""
    calls: list[dict] = []
    results: list[dict] = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return list(results)

    monkeypatch.setattr(RetrievalService, "search", staticmethod(fake_search))
    return {"calls": calls, "results": results}


@pytest_asyncio.fixture(params=_backends())
async def env(request, monkeypatch, store_dir, queued, use_mock_providers):
    created_orgs: list[str] = []
    engine = None
    if request.param == "sqlite":
        engine, factory = await make_sqlite_factory()

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[deps.get_db] = override_get_db
    else:
        from app.db.session import SessionLocal as factory  # noqa: N813

    monkeypatch.setattr(ingestion, "SessionLocal", factory)
    # The streaming endpoint opens its own session, through this name.
    monkeypatch.setattr(chat_module, "SessionLocal", factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        harness = Env(client, factory, created_orgs, queued)
        harness.headers = await harness.sign_up()
        harness.project_id = await harness.make_project("Handbooks")
        yield harness

    app.dependency_overrides.pop(deps.get_db, None)
    if engine is not None:
        await engine.dispose()
    else:
        async with factory() as session:
            for org_id in created_orgs:
                await session.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))
            await session.commit()


class Env:
    def __init__(self, client, factory, created_orgs, queued):
        self.client = client
        self.factory = factory
        self.created_orgs = created_orgs
        self.queued = queued
        self.headers: dict = {}
        self.project_id = ""

    async def sign_up(self) -> dict:
        email = f"user-{uuid.uuid4().hex[:12]}@example.com"
        password = "a-perfectly-fine-password"
        signup = await self.client.post(f"{API}/auth/signup", json={"email": email, "password": password})
        assert signup.status_code == 201, signup.text
        self.created_orgs.append(signup.json()["org_id"])
        login = await self.client.post(f"{API}/auth/login", data={"username": email, "password": password})
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    async def make_project(self, name: str, headers=None) -> str:
        response = await self.client.post(
            f"{API}/projects/", headers=headers or self.headers, json={"name": name}
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def upload(self, name="policy.txt", data=TEXT) -> dict:
        response = await self.client.post(
            f"{API}/documents/upload",
            headers=self.headers,
            data={"project_id": self.project_id},
            files=[("files", (name, data, "text/plain"))],
        )
        assert response.status_code == 202, response.text
        return response.json()[0]

    async def set_document(self, document_id: str, **values) -> None:
        async with self.factory() as session:
            document = await session.get(Document, uuid.UUID(document_id))
            for key, value in values.items():
                setattr(document, key, value)
            await session.commit()

    async def add_chunks(self, document_id: str, count: int) -> list[str]:
        ids = []
        async with self.factory() as session:
            for index in range(count):
                chunk = Chunk(document_id=uuid.UUID(document_id), content=f"Chunk number {index}.",
                              chunk_index=index, page_number=index + 1, meta_data={})
                session.add(chunk)
                await session.flush()
                ids.append(str(chunk.id))
            await session.commit()
        return ids

    async def demote(self, headers: dict, role: str = "Employee") -> None:
        """Change the signed-in user's role directly -- standing in for an admin."""
        me = await self.client.get(f"{API}/users/me", headers=headers)
        async with self.factory() as session:
            user = await session.get(User, uuid.UUID(me.json()["id"]))
            user.role = role
            await session.commit()

    async def count(self, model, *where) -> int:
        async with self.factory() as session:
            return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    async def audit_actions(self) -> list[str]:
        async with self.factory() as session:
            rows = await session.execute(select(AuditLog.action).order_by(AuditLog.created_at))
            return [row[0] for row in rows]

    async def new_session(self) -> str:
        response = await self.client.post(
            f"{API}/chat/sessions", headers=self.headers, json={"project_id": self.project_id}
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def stream(self, session_id: str, body: dict) -> list[tuple[str, dict]]:
        """POST to the streaming endpoint and parse the SSE events."""
        response = await self.client.post(
            f"{API}/chat/sessions/{session_id}/messages/stream", headers=self.headers, json=body
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        events = []
        for frame in response.text.split("\n\n"):
            if not frame.strip():
                continue
            lines = dict(line.split(": ", 1) for line in frame.split("\n"))
            events.append((lines["event"], json.loads(lines["data"])))
        return events


# ======================================================================
# Projects
# ======================================================================


class TestProjects:
    async def test_get_one_with_counts(self, env):
        first = await env.upload("a.txt")
        await env.upload("b.txt", TEXT + b"different")
        await env.set_document(first["id"], status="completed")

        response = await env.client.get(f"{API}/projects/{env.project_id}", headers=env.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "Handbooks"
        assert body["document_count"] == 2 and body["ready_count"] == 1

    async def test_rename_is_audited(self, env):
        response = await env.client.patch(
            f"{API}/projects/{env.project_id}", headers=env.headers, json={"name": "  Policies "}
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Policies"
        assert "project.rename" in await env.audit_actions()

    async def test_a_blank_name_is_refused(self, env):
        response = await env.client.patch(
            f"{API}/projects/{env.project_id}", headers=env.headers, json={"name": ""}
        )
        assert response.status_code == 422

    async def test_another_organizations_project_is_not_found(self, env):
        other = await env.sign_up()
        for method in ("get", "patch", "delete"):
            kwargs = {"json": {"name": "x"}} if method == "patch" else {}
            response = await getattr(env.client, method)(
                f"{API}/projects/{env.project_id}", headers=other, **kwargs
            )
            assert response.status_code == 404, method

    async def test_viewers_cannot_rename_or_delete(self, env):
        await env.demote(env.headers)
        assert (await env.client.patch(f"{API}/projects/{env.project_id}", headers=env.headers,
                                       json={"name": "x"})).status_code == 403
        assert (await env.client.delete(f"{API}/projects/{env.project_id}",
                                        headers=env.headers)).status_code == 403
        # ...but can still read it.
        assert (await env.client.get(f"{API}/projects/{env.project_id}",
                                     headers=env.headers)).status_code == 200

    async def test_delete_takes_everything_with_it(self, env, store_dir):
        document = await env.upload()
        await env.add_chunks(document["id"], 3)
        await env.new_session()
        assert any(store_dir.rglob("*.txt"))

        response = await env.client.delete(f"{API}/projects/{env.project_id}", headers=env.headers)

        assert response.status_code == 204, response.text
        assert await env.count(Document) == 0
        assert await env.count(Chunk) == 0
        assert await env.count(ChatSession) == 0
        assert not any(store_dir.rglob("*.txt")), "the stored file must be removed too"
        assert "project.delete" in await env.audit_actions()


# ======================================================================
# Documents
# ======================================================================


class TestDocuments:
    async def test_get_one(self, env):
        document = await env.upload()
        response = await env.client.get(f"{API}/documents/{document['id']}", headers=env.headers)
        assert response.status_code == 200 and response.json()["filename"] == "policy.txt"

    async def test_rename_keeps_the_real_extension(self, env):
        document = await env.upload()
        response = await env.client.patch(
            f"{API}/documents/{document['id']}", headers=env.headers,
            json={"filename": "../Refund rules.exe"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "Refund rules.exe.txt"
        assert "document.rename" in await env.audit_actions()

    async def test_delete_removes_chunks_and_file(self, env, store_dir):
        document = await env.upload()
        await env.add_chunks(document["id"], 4)

        response = await env.client.delete(f"{API}/documents/{document['id']}", headers=env.headers)

        assert response.status_code == 204, response.text
        assert await env.count(Chunk) == 0
        assert not any(store_dir.rglob("*.txt"))
        assert "document.delete" in await env.audit_actions()
        missing = await env.client.get(f"{API}/documents/{document['id']}", headers=env.headers)
        assert missing.status_code == 404

    async def test_viewers_cannot_change_documents(self, env):
        document = await env.upload()
        await env.demote(env.headers)
        url = f"{API}/documents/{document['id']}"
        assert (await env.client.delete(url, headers=env.headers)).status_code == 403
        assert (await env.client.patch(url, headers=env.headers, json={"filename": "x"})).status_code == 403
        assert (await env.client.post(f"{url}/reindex", headers=env.headers)).status_code == 403

    async def test_another_organization_cannot_touch_it(self, env):
        document = await env.upload()
        other = await env.sign_up()
        url = f"{API}/documents/{document['id']}"
        assert (await env.client.delete(url, headers=other)).status_code == 404
        assert (await env.client.get(f"{url}/chunks", headers=other)).status_code == 404
        assert await env.count(Document) == 1

    async def test_reindex_requeues_a_finished_document(self, env):
        document = await env.upload()
        await env.set_document(document["id"], status="failed", error_message="OCR missing")
        env.queued.clear()

        response = await env.client.post(
            f"{API}/documents/{document['id']}/reindex", headers=env.headers,
            json={"chunk_size": 300, "chunk_overlap": 30},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "pending" and body["error_message"] is None
        assert body["meta_data"]["chunk_size"] == 300
        assert env.queued == [document["id"]]
        assert "document.reindex" in await env.audit_actions()

    async def test_reindex_refuses_a_busy_document(self, env):
        document = await env.upload()  # still "pending"
        response = await env.client.post(f"{API}/documents/{document['id']}/reindex", headers=env.headers)
        assert response.status_code == 409

    async def test_reindex_refuses_a_quarantined_file(self, env):
        document = await env.upload()
        await env.set_document(document["id"], status="failed", meta_data={"quarantined": True})
        response = await env.client.post(f"{API}/documents/{document['id']}/reindex", headers=env.headers)
        assert response.status_code == 410

    async def test_reindex_validates_the_chunk_options(self, env):
        document = await env.upload()
        await env.set_document(document["id"], status="completed")
        response = await env.client.post(
            f"{API}/documents/{document['id']}/reindex", headers=env.headers,
            json={"chunk_size": 100, "chunk_overlap": 100},
        )
        assert response.status_code == 422

    async def test_chunks_around_a_citation(self, env):
        document = await env.upload()
        ids = await env.add_chunks(document["id"], 8)

        response = await env.client.get(
            f"{API}/documents/{document['id']}/chunks", headers=env.headers,
            params={"around": ids[4], "window": 1},
        )

        assert response.status_code == 200, response.text
        assert [c["chunk_index"] for c in response.json()] == [3, 4, 5]

    async def test_chunks_from_the_start(self, env):
        document = await env.upload()
        await env.add_chunks(document["id"], 8)
        response = await env.client.get(
            f"{API}/documents/{document['id']}/chunks", headers=env.headers, params={"window": 1}
        )
        assert [c["chunk_index"] for c in response.json()] == [0, 1, 2]

    async def test_a_stale_citation_is_404(self, env):
        document = await env.upload()
        response = await env.client.get(
            f"{API}/documents/{document['id']}/chunks", headers=env.headers,
            params={"around": str(uuid.uuid4())},
        )
        assert response.status_code == 404


# ======================================================================
# Chat history
# ======================================================================


def _chunk_result(document_id: str, number: int = 1) -> dict:
    return {
        "chunk_id": uuid.uuid4(),
        "document_id": uuid.UUID(document_id),
        "filename": "policy.txt",
        "page_number": 2,
        "chunk_index": number,
        "content": "Refunds are available within thirty days of purchase.",
        "score": 0.81,
        "meta_data": {},
    }


class TestChatHistory:
    async def test_rename_and_list_by_project(self, env):
        session_id = await env.new_session()
        other_project = await env.make_project("Other")
        await env.client.post(f"{API}/chat/sessions", headers=env.headers, json={"project_id": other_project})

        renamed = await env.client.patch(
            f"{API}/chat/sessions/{session_id}", headers=env.headers, json={"title": "Refunds"}
        )
        assert renamed.status_code == 200 and renamed.json()["title"] == "Refunds"

        listed = await env.client.get(
            f"{API}/chat/sessions", headers=env.headers, params={"project_id": env.project_id}
        )
        assert [s["id"] for s in listed.json()] == [session_id]

    async def test_someone_elses_session_cannot_be_renamed(self, env):
        session_id = await env.new_session()
        other = await env.sign_up()
        response = await env.client.patch(
            f"{API}/chat/sessions/{session_id}", headers=other, json={"title": "mine now"}
        )
        assert response.status_code == 404

    async def test_the_answer_is_saved_with_mode_and_model(self, env, searches):
        document = await env.upload()
        searches["results"].append(_chunk_result(document["id"]))
        session_id = await env.new_session()

        response = await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages", headers=env.headers,
            json={"message": "How long do refunds take?"},
        )
        assert response.status_code == 200, response.text

        messages = (await env.client.get(f"{API}/chat/sessions/{session_id}/messages",
                                         headers=env.headers)).json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["mode"] == "rag" and messages[1]["model"] == "mock"
        assert messages[1]["citations"][0]["filename"] == "policy.txt"

        sessions = (await env.client.get(f"{API}/chat/sessions", headers=env.headers)).json()
        assert sessions[0]["title"] == "How long do refunds take?"

    async def test_the_document_filter_reaches_retrieval(self, env, searches):
        document = await env.upload()
        session_id = await env.new_session()
        await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages", headers=env.headers,
            json={"message": "Refunds?", "document_ids": [document["id"]]},
        )
        assert searches["calls"][0]["document_ids"] == [uuid.UUID(document["id"])]

    async def test_too_many_documents_in_the_filter_is_422(self, env):
        session_id = await env.new_session()
        response = await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages", headers=env.headers,
            json={"message": "x", "document_ids": [str(uuid.uuid4()) for _ in range(51)]},
        )
        assert response.status_code == 422

    async def test_a_follow_up_is_searched_with_the_rewritten_query(self, env, searches):
        session_id = await env.new_session()
        url = f"{API}/chat/sessions/{session_id}/messages"
        await env.client.post(url, headers=env.headers,
                              json={"message": "How many days of annual leave do staff get?"})
        second = await env.client.post(url, headers=env.headers, json={"message": "and part-timers?"})

        assert second.status_code == 200, second.text
        assert "annual leave" in searches["calls"][1]["query"]
        assert second.json()["search_query"] == searches["calls"][1]["query"]

    async def test_export_as_markdown(self, env, searches):
        document = await env.upload()
        searches["results"].append(_chunk_result(document["id"]))
        session_id = await env.new_session()
        await env.client.post(f"{API}/chat/sessions/{session_id}/messages", headers=env.headers,
                              json={"message": "Refunds?"})

        response = await env.client.get(f"{API}/chat/sessions/{session_id}/export", headers=env.headers)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert 'filename="refunds.md"' in response.headers["content-disposition"]
        assert "## Question\n\nRefunds?" in response.text
        assert "1. policy.txt, page 2" in response.text


# ======================================================================
# Streaming
# ======================================================================


class TestStreaming:
    async def test_rag_streams_citations_then_tokens_then_done(self, env, searches):
        document = await env.upload()
        searches["results"].append(_chunk_result(document["id"]))
        session_id = await env.new_session()

        events = await env.stream(session_id, {"message": "How long do refunds take?"})

        names = [name for name, _ in events]
        assert names[0] == "start" and names[1] == "query" and names[2] == "citations"
        assert names[-1] == "done"
        assert names.count("token") > 3, "the answer must arrive in pieces"
        streamed = "".join(data["text"] for name, data in events if name == "token")
        done = events[-1][1]
        assert done["assistant_message"]["content"] == streamed.strip()
        assert done["mode"] == "rag" and done["model"] == "mock"

        # Persisted at the end, exactly like the JSON endpoint.
        assert await env.count(ChatMessage) == 2

    async def test_agent_streams_steps_live_and_saves_them(self, env, monkeypatch):
        document = await env.upload()
        chunk = {**_chunk_result(document["id"]), "number": 4}

        async def fake_run_agent(**kwargs):
            on_step = kwargs["on_step"]
            on_step({"tool": "list_documents", "args": {}, "summary": "Listed 1 document"})
            on_step({"tool": "search_documents", "args": {"query": "refunds"},
                     "summary": "Found 1 excerpt from 1 document"})
            return {
                "answer": "Thirty days [4].", "tokens_used": 12, "prompt_tokens": 9,
                "completion_tokens": 3, "latency": 0.2, "model": "agent-test",
                "citations_chunks": [chunk],
                "steps": [
                    {"tool": "list_documents", "args": {}, "summary": "Listed 1 document"},
                    {"tool": "search_documents", "args": {"query": "refunds"},
                     "summary": "Found 1 excerpt from 1 document"},
                ],
            }

        monkeypatch.setattr(chat_module, "run_agent", fake_run_agent)
        session_id = await env.new_session()

        events = await env.stream(session_id, {"message": "Refunds?", "mode": "agent"})

        names = [name for name, _ in events]
        assert names[:3] == ["start", "step", "step"]
        assert names.index("citations") > names.index("step")
        assert events[-1][0] == "done"
        assert [c["number"] for c in events[-1][1]["citations"]] == [4]

        messages = (await env.client.get(f"{API}/chat/sessions/{session_id}/messages",
                                         headers=env.headers)).json()
        saved = messages[1]
        assert saved["mode"] == "agent" and saved["model"] == "agent-test"
        assert [s["tool"] for s in saved["steps"]] == ["list_documents", "search_documents"]

    async def test_a_failure_mid_stream_is_an_error_event_and_saves_nothing(self, env, monkeypatch):
        async def broken_search(**kwargs):
            raise RuntimeError("index offline")

        monkeypatch.setattr(RetrievalService, "search", staticmethod(broken_search))
        session_id = await env.new_session()

        events = await env.stream(session_id, {"message": "Refunds?"})

        assert events[-1][0] == "error"
        assert await env.count(ChatMessage) == 0

    async def test_someone_elses_session_is_refused_before_streaming(self, env):
        session_id = await env.new_session()
        other = await env.sign_up()
        response = await env.client.post(
            f"{API}/chat/sessions/{session_id}/messages/stream", headers=other,
            json={"message": "hi"},
        )
        assert response.status_code == 404


# ======================================================================
# Feedback summary
# ======================================================================


class TestFeedbackSummary:
    async def test_the_summary(self, env, searches):
        session_id = await env.new_session()
        url = f"{API}/chat/sessions/{session_id}/messages"
        first = (await env.client.post(url, headers=env.headers, json={"message": "Q one?"})).json()
        # SQLite keeps whole seconds, so two quick exchanges would share a
        # timestamp; age the first one so "the question before this answer"
        # is unambiguous (Postgres has microseconds and needs no help).
        async with env.factory() as session:
            for message in (await session.execute(select(ChatMessage))).scalars():
                message.created_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
            await session.commit()
        second =(await env.client.post(url, headers=env.headers, json={"message": "Q two?"})).json()
        for answer, rating, comment in ((first, 1, None), (second, -1, "wrong page")):
            response = await env.client.post(
                f"{API}/chat/messages/{answer['assistant_message']['id']}/feedback",
                headers=env.headers, json={"rating": rating, "comment": comment},
            )
            assert response.status_code == 201

        response = await env.client.get(f"{API}/org/feedback", headers=env.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["up"], body["down"]) == (2, 1, 1)
        assert body["satisfaction"] == 0.5
        assert body["by_mode"] == [{"key": "rag", "up": 1, "down": 1}]
        negative = body["recent_negative"][0]
        assert negative["question"] == "Q two?" and negative["comment"] == "wrong page"

    async def test_other_organizations_votes_are_not_counted(self, env, searches):
        session_id = await env.new_session()
        answer = (await env.client.post(f"{API}/chat/sessions/{session_id}/messages",
                                        headers=env.headers, json={"message": "Q?"})).json()
        await env.client.post(f"{API}/chat/messages/{answer['assistant_message']['id']}/feedback",
                              headers=env.headers, json={"rating": -1})
        other = await env.sign_up()
        body = (await env.client.get(f"{API}/org/feedback", headers=other)).json()
        assert body["total"] == 0 and body["satisfaction"] is None
        assert await env.count(Feedback) == 1

    async def test_it_is_admin_only(self, env):
        await env.demote(env.headers, "Manager")
        response = await env.client.get(f"{API}/org/feedback", headers=env.headers)
        assert response.status_code == 403
