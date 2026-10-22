"""
test_upload_hardening.py
========================
WHAT THIS FILE TESTS
--------------------
The upload and preview endpoints over real HTTP requests, after hardening:

    413   a file over MAX_UPLOAD_SIZE_BYTES, and a whole request over the
          ceiling (refused by middleware before the body is parsed)
    415   an extension extractor.py cannot read; bytes that do not match
    safe  the client's filename is cleaned for display and NEVER used in
          the storage path; nothing is stored when a request is refused
    queue the job is queued after the commit, and a dead Redis does not
          lose the upload
    preview  streamed from storage; a quarantined file is 410 Gone

HOW IT RUNS
-----------
Like test_auth_flows.py: every test runs on in-memory SQLite always, and on
Postgres too when one is reachable (CI). Files go to LocalStorage under
tmp_path. Password hashing is swapped for SHA-256 -- nothing here tests it.
"""

import hashlib
import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api import deps
from app.core import security
from app.core.config import settings
from app.main import app
from app.models.document import Document
from app.models.organization import Organization
from app.services import storage as storage_module
from app.tasks import ingestion
from tests.conftest import database_available
from tests.sqlite_harness import make_sqlite_factory

API = settings.API_V1_STR
UPLOAD = f"{API}/documents/upload"
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


@pytest_asyncio.fixture(params=_backends())
async def env(request, monkeypatch, store_dir, use_mock_providers):
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

    # Inline ingestion and recovery read through this name.
    monkeypatch.setattr(ingestion, "SessionLocal", factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        harness = Env(client, factory, created_orgs, store_dir)
        await harness.sign_in()
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
    def __init__(self, client, factory, created_orgs, store_dir):
        self.client = client
        self.factory = factory
        self.created_orgs = created_orgs
        self.store_dir = store_dir
        self.headers: dict = {}
        self.project_id = ""

    async def sign_in(self):
        email = f"uploader-{uuid.uuid4().hex[:12]}@example.com"
        password = "a-perfectly-fine-password"
        signup = await self.client.post(f"{API}/auth/signup", json={"email": email, "password": password})
        assert signup.status_code == 201, signup.text
        self.created_orgs.append(signup.json()["org_id"])
        login = await self.client.post(f"{API}/auth/login", data={"username": email, "password": password})
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        project = await self.client.post(f"{API}/projects/", headers=self.headers, json={"name": "Uploads"})
        assert project.status_code == 201, project.text
        self.project_id = project.json()["id"]

    async def upload(self, *files, headers=None):
        """Each file is (name, bytes)."""
        return await self.client.post(
            UPLOAD,
            headers={**self.headers, **(headers or {})},
            data={"project_id": self.project_id},
            files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        )

    async def documents(self) -> list[Document]:
        async with self.factory() as session:
            rows = await session.execute(
                select(Document).where(Document.project_id == uuid.UUID(self.project_id))
            )
            return list(rows.scalars().all())

    def stored_files(self) -> list[str]:
        if not self.store_dir.exists():
            return []
        return [
            os.path.relpath(os.path.join(root, name), self.store_dir).replace("\\", "/")
            for root, _, names in os.walk(self.store_dir)
            for name in names
        ]


# ======================================================================
# Accepting a good upload
# ======================================================================


class TestAccepted:
    async def test_the_file_is_stored_under_a_server_key(self, env, queued):
        response = await env.upload(("../../../etc/policy.txt", TEXT))

        assert response.status_code == 202, response.text
        body = response.json()[0]
        assert body["filename"] == "policy.txt"
        assert body["status"] == "pending"
        assert body["error_message"] is None

        [document] = await env.documents()
        assert document.storage_backend == "local"
        assert document.file_path.startswith(f"{env.project_id}/")
        assert "policy" not in document.file_path
        assert env.stored_files() == [document.file_path]

    async def test_the_job_is_queued_after_the_commit(self, env, queued):
        response = await env.upload(("a.txt", TEXT))
        assert queued == [response.json()[0]["id"]]

    async def test_a_dead_queue_does_not_lose_the_upload(self, env, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "celery")

        def redis_down(document_id):
            raise ConnectionError("Redis is not running")

        monkeypatch.setattr(ingestion.process_document_task, "delay", redis_down)

        response = await env.upload(("a.txt", TEXT))

        # Accepted and safely stored as pending; recovery queues it later.
        assert response.status_code == 202
        assert [d.status for d in await env.documents()] == ["pending"]

    async def test_identical_files_in_one_request_are_stored_once(self, env, queued):
        response = await env.upload(("a.txt", TEXT), ("copy-of-a.txt", TEXT))

        assert response.status_code == 202, response.text
        assert len(response.json()) == 1
        assert len(env.stored_files()) == 1

    async def test_inline_mode_processes_it_end_to_end(self, env, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        await env.upload(("policy.txt", TEXT))

        listed = await env.client.get(
            f"{API}/documents/", headers=env.headers, params={"project_id": env.project_id}
        )
        document = listed.json()[0]
        assert document["status"] == "completed", document
        assert document["attempts"] == 1


# ======================================================================
# Refusals
# ======================================================================


class TestRefused:
    async def test_an_oversized_file_is_413(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 1000)
        response = await env.upload(("big.txt", b"a" * 1001))

        assert response.status_code == 413
        assert "big.txt" in response.json()["detail"]

    async def test_an_oversized_request_is_refused_before_parsing(self, env, queued, monkeypatch):
        """The middleware's ceiling: every file at the max, plus 1 MB."""
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 1000)
        monkeypatch.setattr(settings, "MAX_FILES_PER_UPLOAD", 1)
        response = await env.upload(("huge.txt", b"a" * (2 * 1024 * 1024)))

        assert response.status_code == 413
        assert "per request" in response.json()["detail"]

    async def test_an_unsupported_type_is_415(self, env, queued):
        response = await env.upload(("setup.exe", b"MZ\x90\x00"))
        assert response.status_code == 415
        assert "not supported" in response.json()["detail"]

    async def test_a_renamed_file_is_415(self, env, queued):
        response = await env.upload(("report.pdf", b"MZ\x90\x00 not a pdf at all"))
        assert response.status_code == 415
        assert "PDF" in response.json()["detail"]

    async def test_an_empty_file_is_400(self, env, queued):
        assert (await env.upload(("empty.txt", b""))).status_code == 400

    async def test_too_many_files_is_400(self, env, queued, monkeypatch):
        monkeypatch.setattr(settings, "MAX_FILES_PER_UPLOAD", 2)
        response = await env.upload(("a.txt", b"a"), ("b.txt", b"b"), ("c.txt", b"c"))
        assert response.status_code == 400

    async def test_one_bad_file_refuses_the_batch_and_stores_nothing(self, env, queued):
        response = await env.upload(("good.txt", TEXT), ("bad.pdf", b"not a pdf"))

        assert response.status_code == 415
        assert await env.documents() == []
        assert env.stored_files() == []
        assert queued == []

    async def test_a_storage_outage_is_503_and_leaves_nothing(self, env, queued, monkeypatch):
        store = storage_module.get_storage()

        def down(key, source_path):
            raise storage_module.StorageUnavailableError("disk on fire")

        monkeypatch.setattr(store, "save", down)
        response = await env.upload(("a.txt", TEXT))

        assert response.status_code == 503
        assert await env.documents() == []


# ======================================================================
# Preview
# ======================================================================


class TestPreview:
    async def test_the_original_bytes_come_back_under_the_original_name(self, env, queued):
        document_id = (await env.upload(("Policy 2026.txt", TEXT))).json()[0]["id"]

        response = await env.client.get(f"{API}/documents/{document_id}/preview", headers=env.headers)

        assert response.status_code == 200
        assert response.content == TEXT
        assert "Policy" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_a_quarantined_file_is_gone(self, env, queued):
        document_id = (await env.upload(("a.txt", TEXT))).json()[0]["id"]
        async with env.factory() as session:
            document = await session.get(Document, uuid.UUID(document_id))
            document.meta_data = {**document.meta_data, "quarantined": True}
            await session.commit()

        response = await env.client.get(f"{API}/documents/{document_id}/preview", headers=env.headers)
        assert response.status_code == 410

    async def test_a_missing_file_is_404(self, env, queued):
        document_id = (await env.upload(("a.txt", TEXT))).json()[0]["id"]
        for name in env.stored_files():
            os.remove(env.store_dir / name)

        response = await env.client.get(f"{API}/documents/{document_id}/preview", headers=env.headers)
        assert response.status_code == 404

    async def test_object_storage_is_streamed(self, env, queued, monkeypatch):
        """A document whose row says s3 is read through the S3 backend."""
        from tests.test_storage import FakeS3

        fake = storage_module.S3Storage(client=FakeS3(), bucket="docs", prefix="")
        monkeypatch.setitem(storage_module._instances, "s3", fake)
        monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")

        document_id = (await env.upload(("notes.md", b"# Notes\n\nhello"))).json()[0]["id"]
        [document] = await env.documents()
        assert document.storage_backend == "s3"

        response = await env.client.get(f"{API}/documents/{document_id}/preview", headers=env.headers)
        assert response.status_code == 200
        assert response.content == b"# Notes\n\nhello"
        assert "UTF-8''notes.md" in response.headers["content-disposition"]
