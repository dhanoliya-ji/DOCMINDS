# ruff: noqa: F811 -- the fixtures imported from test_upload_hardening are re-used as test arguments
"""
test_connectors.py
==================
WHAT THIS FILE TESTS
--------------------
Cloud imports and auto-sync (services/connectors/, api/v1/integrations.py):

    * token encryption: round trip, key rotation, tampering, derived key
    * the OAuth state: signed, typed, per provider
    * the SSRF guard: https only, allowlisted hosts, public addresses,
      redirects checked and stripped of our token, the size cap
    * item ids are validated before they reach a URL
    * provider parsing against faked APIs (Drive export types, Graph ids,
      Notion -> Markdown, Confluence pages)
    * through the API with a fake provider: connect via the callback, browse,
      import (queued like an upload, audited, de-duplicated), Sync now
      (busy / unchanged / updated / source deleted), the periodic sweep,
      disconnect, viewers refused, everything 404 while switched off

Runs on in-memory SQLite always, and on Postgres too when one is reachable.
"""

import datetime
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.audit import AuditLog
from app.models.connector import ConnectorConnection, ConnectorItem
from app.models.document import Document
from app.models.user import User
from app.services import connectors
from app.services.connectors import crypto, http, service
from app.services.connectors.base import Provider, RemoteItem, TokenSet, check_id
from app.services.connectors.confluence import Confluence
from app.services.connectors.google_drive import GoogleDrive
from app.services.connectors.notion import Notion, block_to_markdown, page_title
from app.services.connectors.onedrive import OneDrive, split_id
from tests.test_upload_hardening import (  # noqa: F401 -- pytest fixtures
    env,
    fast_password_hashing,
    queued,
    store_dir,
)

API = settings.API_V1_STR
TEXT = ("Travel expenses are reimbursed within fourteen days. " * 30).encode()


# ======================================================================
# Encryption
# ======================================================================


class TestCrypto:
    def test_round_trip_and_ciphertext_hides_the_token(self, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", "")
        blob = crypto.encrypt_tokens({"access_token": "ya29.secret"})
        assert "ya29" not in blob
        assert crypto.decrypt_tokens(blob) == {"access_token": "ya29.secret"}

    def test_derived_key_depends_on_secret_key(self, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", "")
        blob = crypto.encrypt_tokens({"a": 1})
        monkeypatch.setattr(settings, "SECRET_KEY", "x" * 64)
        with pytest.raises(crypto.TokenDecryptError):
            crypto.decrypt_tokens(blob)

    def test_rotation_old_key_still_decrypts(self, monkeypatch):
        from cryptography.fernet import Fernet

        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", old)
        blob = crypto.encrypt_tokens({"a": 1})
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", f"{new},{old}")
        assert crypto.decrypt_tokens(blob) == {"a": 1}
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", new)
        with pytest.raises(crypto.TokenDecryptError):
            crypto.decrypt_tokens(blob)

    def test_a_passphrase_is_stretched_into_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", "not a fernet key")
        assert crypto.decrypt_tokens(crypto.encrypt_tokens({"a": 2})) == {"a": 2}

    def test_tampering_is_detected(self, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_ENCRYPTION_KEY", "")
        blob = crypto.encrypt_tokens({"a": 1})
        with pytest.raises(crypto.TokenDecryptError):
            crypto.decrypt_tokens(blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB"))


# ======================================================================
# OAuth state, ids
# ======================================================================


class TestState:
    def _user(self):
        return User(id=uuid.uuid4(), org_id=uuid.uuid4(), email="a@b.c", role="Admin", hashed_password="x")

    def test_round_trip(self):
        user = self._user()
        claims = service.read_state(service.make_state(user, "notion"), "notion")
        assert claims["sub"] == str(user.id) and claims["org"] == str(user.org_id)

    def test_wrong_provider_or_forged(self):
        state = service.make_state(self._user(), "notion")
        with pytest.raises(http.ConnectorError):
            service.read_state(state, "google_drive")
        with pytest.raises(http.ConnectorError):
            service.read_state(state + "x", "notion")

    def test_an_access_token_is_not_a_state(self):
        from app.core.security import create_access_token

        token = create_access_token(subject=str(uuid.uuid4()))
        with pytest.raises(http.ConnectorError):
            service.read_state(token, "notion")

    @pytest.mark.parametrize("bad", ["", "../etc", "a/b", "x?y=1", "a b", "a#b", "x" * 401])
    def test_bad_ids_are_refused(self, bad):
        with pytest.raises(http.ConnectorError):
            check_id(bad)

    def test_good_ids(self):
        for good in ("1AbC-_x", "D4648F06C91D9D3D!54927", "b!abc~01XYZ", "12345", "site~contoso.sharepoint.com,1,2"):
            assert check_id(good) == good
        assert split_id("b!drive~item!1") == ("b!drive", "item!1")


# ======================================================================
# The SSRF guard
# ======================================================================


@pytest.fixture
def fake_net(monkeypatch):
    """Route connector HTTP through a handler; every host 'resolves publicly'."""
    calls: list = []
    routes: dict = {}

    def handler(request: httpx.Request):
        calls.append(request)
        key = (request.method, f"{request.url.scheme}://{request.url.host}{request.url.path}")
        responder = routes.get(key)
        if responder is None:
            return httpx.Response(404, json={"error": "no route"})
        return responder(request)

    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(http, "_resolves_publicly", lambda host: True)
    return routes, calls


class TestGuard:
    async def test_http_and_unlisted_hosts_are_refused(self, fake_net):
        with pytest.raises(http.ConnectorError):
            await http.request("GET", "http://api.notion.com/v1/x", allowed=("api.notion.com",))
        with pytest.raises(http.ConnectorError):
            await http.request("GET", "https://evil.example/v1/x", allowed=("api.notion.com",))
        with pytest.raises(http.ConnectorError):
            await http.request("GET", "https://user:pw@api.notion.com/", allowed=("api.notion.com",))
        assert fake_net[1] == []

    async def test_private_resolution_is_refused(self, monkeypatch):
        monkeypatch.setattr(http, "_resolves_publicly", lambda host: False)
        with pytest.raises(http.ConnectorError, match="private"):
            await http.request("GET", "https://api.notion.com/v1/x", allowed=("api.notion.com",))

    def test_real_resolver_rejects_loopback(self):
        assert http._resolves_publicly("localhost") is False
        assert http._host_allowed("a.sharepoint.com", (".sharepoint.com",))
        assert not http._host_allowed("sharepoint.com.evil.io", (".sharepoint.com",))
        assert not http._host_allowed("evilsharepoint.com", (".sharepoint.com",))

    async def test_status_mapping(self, fake_net):
        routes, _ = fake_net
        for code, exc in ((401, http.ConnectorAuthError), (404, http.SourceNotFound), (429, http.ConnectorUnavailable)):
            routes[("GET", "https://api.notion.com/s")] = lambda r, c=code: httpx.Response(c)
            with pytest.raises(exc):
                await http.request("GET", "https://api.notion.com/s", allowed=("api.notion.com",))

    async def test_download_follows_allowlisted_redirect_without_our_token(self, fake_net, tmp_path):
        routes, calls = fake_net
        routes[("GET", "https://graph.microsoft.com/v1.0/content")] = lambda r: httpx.Response(
            302, headers={"location": "https://tenant.sharepoint.com/file?sig=1"})
        routes[("GET", "https://tenant.sharepoint.com/file")] = lambda r: httpx.Response(200, content=b"hello")
        size = await http.download(
            "https://graph.microsoft.com/v1.0/content", str(tmp_path / "f"),
            allowed=("graph.microsoft.com",), redirect_allowed=(".sharepoint.com",),
            headers={"Authorization": "Bearer t"},
        )
        assert size == 5 and (tmp_path / "f").read_bytes() == b"hello"
        assert "authorization" in calls[0].headers and "authorization" not in calls[1].headers

    async def test_download_refuses_redirect_elsewhere(self, fake_net, tmp_path):
        routes, _ = fake_net
        routes[("GET", "https://graph.microsoft.com/v1.0/content")] = lambda r: httpx.Response(
            302, headers={"location": "https://169.254.169.254/latest/meta-data"})
        with pytest.raises(http.ConnectorError):
            await http.download("https://graph.microsoft.com/v1.0/content", str(tmp_path / "f"),
                                allowed=("graph.microsoft.com",), redirect_allowed=(".sharepoint.com",))

    async def test_download_size_cap(self, fake_net, tmp_path):
        routes, _ = fake_net
        routes[("GET", "https://www.googleapis.com/big")] = lambda r: httpx.Response(200, content=b"x" * 2048)
        with pytest.raises(http.TooLarge):
            await http.download("https://www.googleapis.com/big", str(tmp_path / "f"),
                                allowed=("www.googleapis.com",), max_bytes=1024)
        assert not (tmp_path / "f").exists()


# ======================================================================
# Providers against faked APIs
# ======================================================================


class TestProviders:
    async def test_google_drive_listing_and_export(self, fake_net, tmp_path):
        routes, calls = fake_net
        routes[("GET", "https://www.googleapis.com/drive/v3/files")] = lambda r: httpx.Response(200, json={
            "files": [
                {"id": "d1", "name": "Plan", "mimeType": "application/vnd.google-apps.document", "version": "7"},
                {"id": "f1", "name": "Docs", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "x1", "name": "Form", "mimeType": "application/vnd.google-apps.form"},
            ],
            "nextPageToken": "n2",
        })
        routes[("GET", "https://www.googleapis.com/drive/v3/files/d1/export")] = lambda r: httpx.Response(200, content=b"PK")
        items, cursor = await GoogleDrive().list_items(TokenSet("t"), query="it's")
        assert cursor == "n2"
        assert "name contains 'it\\'s'" in calls[0].url.params["q"]
        doc, folder, form = items
        assert (doc.kind, doc.importable, doc.revision, doc.export_extension) == ("file", True, "7", ".docx")
        assert folder.kind == "folder" and not folder.importable
        assert not form.importable
        fetched = await GoogleDrive().fetch(TokenSet("t"), doc, str(tmp_path / "d"))
        assert fetched.filename == "Plan.docx"
        assert "wordprocessingml" in calls[-1].url.params["mimeType"]

    async def test_onedrive_items_and_followed_sites(self, fake_net):
        routes, _ = fake_net
        routes[("GET", "https://graph.microsoft.com/v1.0/me/drive/root/children")] = lambda r: httpx.Response(200, json={
            "value": [{"id": "I1", "name": "a.docx", "file": {"mimeType": "x"}, "cTag": "c1",
                       "parentReference": {"driveId": "D1"}}]})
        routes[("GET", "https://graph.microsoft.com/v1.0/me/followedSites")] = lambda r: httpx.Response(200, json={
            "value": [{"id": "contoso.sharepoint.com,1,2", "displayName": "HR"}]})
        items, _ = await OneDrive().list_items(TokenSet("t"))
        assert items[0].id == "site~contoso.sharepoint.com,1,2" and items[0].kind == "folder"
        assert (items[1].id, items[1].revision, items[1].importable) == ("D1~I1", "c1", True)

    async def test_onedrive_cursor_must_be_graph(self):
        with pytest.raises(http.ConnectorError):
            await OneDrive().list_items(TokenSet("t"), cursor="https://evil.example/next")

    def test_notion_markdown(self):
        assert page_title({"properties": {"Name": {"type": "title", "title": [{"plain_text": "Leave"}]}}}) == "Leave"
        para = {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Rules"}]}}
        assert block_to_markdown(para)[0] == "### Rules"
        todo = {"type": "to_do", "to_do": {"checked": True, "rich_text": [{"plain_text": "Sign", "href": "https://x"}]}}
        assert block_to_markdown(todo) == ["- [x] [Sign](https://x)"]

    async def test_confluence_page_and_export(self, fake_net, tmp_path):
        routes, _ = fake_net
        base = "https://api.atlassian.com/ex/confluence/cloud1/wiki/rest/api"
        routes[("GET", f"{base}/content/42")] = lambda r: httpx.Response(200, json={
            "id": "42", "title": "On-call <runbook>", "status": "current", "version": {"number": 3},
            "body": {"export_view": {"value": "<p>Page the SRE.</p>"}},
            "_links": {"base": "https://acme.atlassian.net/wiki", "webui": "/spaces/X/pages/42"}})
        tokens = TokenSet("t", extra={"cloud_id": "cloud1"})
        item = await Confluence().get_item(tokens, "42")
        assert item.revision == "3" and item.web_url == "https://acme.atlassian.net/wiki/spaces/X/pages/42"
        fetched = await Confluence().fetch(tokens, item, str(tmp_path / "p"))
        html = (tmp_path / "p").read_text("utf-8")
        assert fetched.filename.endswith(".html") and "&lt;runbook&gt;" in html and "Page the SRE." in html


# ======================================================================
# Through the API, with a fake provider
# ======================================================================


class FakeProvider(Provider):
    id = "fake"
    name = "Fake Drive"
    allowed_hosts = ("fake.example",)

    def __init__(self):
        self.files = {"f1": ["notes.txt", TEXT, "r1"]}
        self.fail_next_with_401 = False
        self.refreshed = 0

    def configured(self):
        return True

    def authorize_url(self, state, redirect_uri):
        return "https://fake.example/auth?" + httpx.QueryParams({"state": state, "redirect_uri": redirect_uri}).__str__()

    async def exchange_code(self, code, redirect_uri):
        assert code == "the-code"
        return TokenSet("access-1", "refresh-1", time.time() + 3600), "me@fake.example"

    async def refresh(self, tokens):
        self.refreshed += 1
        return TokenSet("access-2", tokens.refresh_token, time.time() + 3600)

    async def list_items(self, tokens, parent=None, query=None, cursor=None):
        items = [RemoteItem(id=k, name=v[0], kind="file", revision=v[2]) for k, v in self.files.items()]
        items.append(RemoteItem(id="folder1", name="Folder", kind="folder", importable=False))
        return items, None

    async def get_item(self, tokens, item_id):
        if self.fail_next_with_401:
            self.fail_next_with_401 = False
            raise http.ConnectorAuthError("expired")
        if item_id == "folder1":
            return RemoteItem(id="folder1", name="Folder", kind="folder", importable=False)
        if item_id not in self.files:
            raise http.SourceNotFound("gone", 404)
        name, _, revision = self.files[item_id]
        return RemoteItem(id=item_id, name=name, kind="file", revision=revision, web_url="https://fake.example/f")

    async def fetch(self, tokens, item, dest_path):
        with open(dest_path, "wb") as out:
            out.write(self.files[item.id][1])
        from app.services.connectors.base import FetchedFile

        return FetchedFile(path=dest_path, filename=item.name)


@pytest.fixture
def fake(monkeypatch, env):
    from app.tasks import connectors as connector_tasks

    env.import_jobs = []
    monkeypatch.setattr(connector_tasks.run_import_task, "delay", lambda import_id: env.import_jobs.append(import_id))
    monkeypatch.setattr(settings, "INGESTION_MODE", "celery")
    provider = FakeProvider()
    monkeypatch.setitem(connectors.PROVIDERS, "fake", provider)
    monkeypatch.setattr(settings, "CONNECTORS_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://front.test")
    return provider


async def _connect(env) -> str:
    started = await env.client.post(f"{API}/integrations/fake/authorize", headers=env.headers)
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
    assert query["redirect_uri"][0].endswith("/api/v1/integrations/callback/fake")
    back = await env.client.get(
        f"{API}/integrations/callback/fake", params={"code": "the-code", "state": query["state"][0]}
    )
    assert back.status_code == 303
    assert back.headers["location"] == "http://front.test/settings/integrations?connected=fake"
    listed = await env.client.get(f"{API}/integrations/connections", headers=env.headers)
    assert listed.status_code == 200
    (connection,) = listed.json()
    assert connection["account_label"] == "me@fake.example" and "token" not in str(connection).lower()
    return connection["id"]


async def _start_import(env, connection_id, *ids):
    return await env.client.post(
        f"{API}/integrations/connections/{connection_id}/import",
        headers=env.headers,
        json={"project_id": env.project_id, "item_ids": list(ids)},
    )


async def _import(env, connection_id, *ids):
    """Start an import (202, every item pending), run the background job the
    way the worker would, and return the finished job's items."""
    started = await _start_import(env, connection_id, *ids)
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["status"] == "queued" and {i["status"] for i in job["items"]} == {"pending"}
    assert env.import_jobs[-1] == job["id"]  # dispatched after the commit
    assert await service.run_import(job["id"], env.factory) == "completed"
    polled = await env.client.get(f"{API}/integrations/imports/{job['id']}", headers=env.headers)
    assert polled.status_code == 200 and polled.json()["status"] == "completed"
    return polled.json()["items"]


async def _link(env, document_id) -> ConnectorItem:
    async with env.factory() as db:
        return (
            await db.execute(select(ConnectorItem).where(ConnectorItem.document_id == uuid.UUID(document_id)))
        ).scalar_one()


class TestThroughTheApi:
    async def test_everything_404s_while_switched_off(self, env):
        assert settings.CONNECTORS_ENABLED is False
        response = await env.client.get(f"{API}/integrations/providers", headers=env.headers)
        assert response.status_code == 404

    async def test_providers_listed_with_configuration(self, env, fake):
        response = await env.client.get(f"{API}/integrations/providers", headers=env.headers)
        assert response.status_code == 200
        by_id = {p["id"]: p for p in response.json()}
        assert by_id["fake"]["configured"] is True
        assert by_id["google_drive"]["configured"] is False

    async def test_connect_tokens_encrypted_and_audited(self, env, fake):
        await _connect(env)
        async with env.factory() as db:
            connection = (await db.execute(select(ConnectorConnection))).scalars().all()[-1]
            assert "access-1" not in connection.encrypted_tokens
            assert crypto.decrypt_tokens(connection.encrypted_tokens)["access_token"] == "access-1"
            actions = (await db.execute(select(AuditLog.action))).scalars().all()
        assert "connector.connect" in actions

    async def test_callback_with_forged_state_changes_nothing(self, env, fake):
        back = await env.client.get(f"{API}/integrations/callback/fake", params={"code": "the-code", "state": "junk"})
        assert back.status_code == 303 and "error=" in back.headers["location"]
        listed = await env.client.get(f"{API}/integrations/connections", headers=env.headers)
        assert listed.json() == []

    async def test_browse_and_import(self, env, fake, queued):
        connection_id = await _connect(env)
        browse = await env.client.get(f"{API}/integrations/connections/{connection_id}/items", headers=env.headers)
        assert browse.status_code == 200
        assert [i["id"] for i in browse.json()["items"]] == ["f1", "folder1"]

        results = {r["item_id"]: r for r in await _import(env, connection_id, "f1", "folder1", "missing")}
        assert results["f1"]["status"] == "imported" and results["f1"]["name"] == "notes.txt"
        assert results["folder1"]["status"] == "skipped"
        assert results["missing"]["status"] == "failed" and results["missing"]["detail"]
        document_id = results["f1"]["document_id"]
        assert queued == [document_id]  # the upload path's ingestion entry point

        (document,) = await env.documents()
        assert (document.status, document.filename, document.meta_data["source"]["provider"]) == (
            "pending", "notes.txt", "fake")
        link = await _link(env, document_id)
        assert (link.external_id, link.revision) == ("f1", "r1")

        again = await _import(env, connection_id, "f1")
        assert again[0]["status"] == "already_imported"

        listed = await env.client.get(f"{API}/integrations/documents", headers=env.headers,
                                      params={"project_id": env.project_id})
        assert listed.json()[0]["sync_status"] == "syncing"
        await env.update_document(document_id, status="completed")
        listed = await env.client.get(f"{API}/integrations/documents", headers=env.headers)
        assert listed.json()[0]["sync_status"] == "synced"

    async def test_unsupported_or_mismatched_content_is_refused(self, env, fake, queued):
        fake.files["bad"] = ["tool.exe", b"MZ\x00\x00", "r"]
        fake.files["fake"] = ["image.png", b"not a png", "r"]
        connection_id = await _connect(env)
        results = await _import(env, connection_id, "bad", "fake")
        assert [r["status"] for r in results] == ["failed", "failed"]
        assert await env.documents() == [] and env.stored_files() == []

    async def test_sync_now(self, env, fake, queued):
        connection_id = await _connect(env)
        document_id = (await _import(env, connection_id, "f1"))[0]["document_id"]
        sync = f"{API}/integrations/documents/{document_id}/sync"

        busy = await env.client.post(sync, headers=env.headers)
        assert busy.json()["outcome"] == "busy"

        await env.update_document(document_id, status="completed")
        unchanged = await env.client.post(sync, headers=env.headers)
        assert unchanged.json()["outcome"] == "unchanged"

        old_path = (await env.documents())[0].file_path
        fake.files["f1"] = ["notes.txt", TEXT + b"New policy: twenty days.", "r2"]
        updated = await env.client.post(sync, headers=env.headers)
        assert updated.json()["outcome"] == "updated", updated.text
        (document,) = await env.documents()
        assert document.status == "pending" and document.file_path != old_path
        assert old_path not in env.stored_files() and document.file_path in env.stored_files()
        assert queued[-1] == document_id
        assert (await _link(env, document_id)).revision == "r2"

        await env.update_document(document_id, status="completed")
        del fake.files["f1"]
        gone = await env.client.post(sync, headers=env.headers)
        assert gone.json()["sync_status"] == "source_deleted"

    async def test_a_401_refreshes_once(self, env, fake, queued):
        connection_id = await _connect(env)
        document_id = (await _import(env, connection_id, "f1"))[0]["document_id"]
        await env.update_document(document_id, status="completed")
        fake.fail_next_with_401 = True
        response = await env.client.post(f"{API}/integrations/documents/{document_id}/sync", headers=env.headers)
        assert response.json()["outcome"] == "unchanged" and fake.refreshed == 1
        async with env.factory() as db:
            connection = await db.get(ConnectorConnection, uuid.UUID(connection_id))
            assert crypto.decrypt_tokens(connection.encrypted_tokens)["access_token"] == "access-2"

    async def test_periodic_sweep(self, env, fake, queued, monkeypatch):
        connection_id = await _connect(env)
        document_id = (await _import(env, connection_id, "f1"))[0]["document_id"]
        await env.update_document(document_id, status="completed")
        fake.files["f1"][1] = TEXT + b" Changed."
        fake.files["f1"][2] = "r9"

        # Checked just now: not due.
        assert await service.sync_due(env.factory) == {}
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=settings.CONNECTOR_SYNC_INTERVAL_MINUTES + 1)
        assert await service.sync_due(env.factory, now=later) == {"updated": 1}

    async def test_disconnect_keeps_documents(self, env, fake, queued):
        connection_id = await _connect(env)
        document_id = (await _import(env, connection_id, "f1"))[0]["document_id"]
        response = await env.client.delete(f"{API}/integrations/connections/{connection_id}", headers=env.headers)
        assert response.status_code == 200
        # The fake provider has no revocation API: the reply says so.
        assert response.json()["revoked"] is False
        assert len(await env.documents()) == 1
        link = await _link(env, document_id)
        assert link.connection_id is None and link.sync_status == "disconnected"

    async def test_viewers_are_refused(self, env, fake):
        me = (await env.client.get(f"{API}/users/me", headers=env.headers)).json()
        async with env.factory() as db:
            user = await db.get(User, uuid.UUID(me["id"]))
            user.role = "Employee"
            await db.commit()
        response = await env.client.get(f"{API}/integrations/connections", headers=env.headers)
        assert response.status_code == 403

    async def test_other_orgs_connection_is_not_found(self, env, fake):
        response = await env.client.get(f"{API}/integrations/connections/{uuid.uuid4()}/items", headers=env.headers)
        assert response.status_code == 404


def test_scheduler_only_runs_inline_and_enabled(monkeypatch):
    from app.services.connectors import scheduler

    monkeypatch.setattr(settings, "CONNECTORS_ENABLED", True)
    monkeypatch.setattr(settings, "CONNECTOR_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "INGESTION_MODE", "celery")
    assert scheduler.should_run() is False
    monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
    assert scheduler.should_run() is True
    monkeypatch.setattr(settings, "CONNECTOR_SYNC_ENABLED", False)
    assert scheduler.should_run() is False


def test_beat_schedule_has_the_sync():
    from app.core.celery_app import celery_app

    assert celery_app.conf.beat_schedule["sync-connectors"]["task"] == "app.tasks.connectors.sync_connectors_task"


def test_document_model_unchanged_for_imports():
    # Imports are plain Document rows: nothing in the pipeline is import-specific.
    assert not hasattr(Document, "connector")


class TestBackgroundImports:
    async def test_bad_ids_are_refused_before_any_job(self, env, fake, queued):
        connection_id = await _connect(env)
        response = await _start_import(env, connection_id, "../etc")
        assert response.status_code == 400
        assert env.import_jobs == []

    async def test_progress_is_visible_while_running(self, env, fake, queued, monkeypatch):
        fake.files["f2"] = ["second.txt", TEXT + b" Second.", "r1"]
        connection_id = await _connect(env)
        job = (await _start_import(env, connection_id, "f1", "f2")).json()
        seen = []
        original = fake.fetch

        async def watching_fetch(tokens, item, dest_path):
            polled = await env.client.get(f"{API}/integrations/imports/{job['id']}", headers=env.headers)
            seen.append([i["status"] for i in polled.json()["items"]])
            return await original(tokens, item, dest_path)

        monkeypatch.setattr(fake, "fetch", watching_fetch)
        await service.run_import(job["id"], env.factory)
        assert seen == [["downloading", "pending"], ["imported", "downloading"]]
        listed = await env.client.get(f"{API}/integrations/imports", headers=env.headers)
        assert listed.json()[0]["counts"] == {"imported": 2}

    async def test_a_claimed_job_is_not_run_twice_and_resumes_when_stale(self, env, fake, queued):
        connection_id = await _connect(env)
        job = (await _start_import(env, connection_id, "f1")).json()
        import_id = uuid.UUID(job["id"])
        async with env.factory() as db:
            assert await service.claim_import(db, import_id) is True  # a worker that then died
        assert await service.run_import(import_id, env.factory) == "busy"
        assert await service.resume_imports(env.factory) == 0
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=settings.CONNECTOR_IMPORT_STALE_MINUTES + 1)
        assert await service.resume_imports(env.factory, now=later) == 1
        async with env.factory() as db:
            assert await service.claim_import(db, import_id, now=later) is True
            job_row = await db.get(service.ConnectorImport, import_id)
            job_row.heartbeat_at = None
            await db.commit()
        assert await service.run_import(import_id, env.factory) == "completed"
        assert len(await env.documents()) == 1

    async def test_disconnect_mid_import_fails_the_rest(self, env, fake, queued):
        connection_id = await _connect(env)
        job = (await _start_import(env, connection_id, "f1")).json()
        await env.client.delete(f"{API}/integrations/connections/{connection_id}", headers=env.headers)
        await service.run_import(job["id"], env.factory)
        polled = (await env.client.get(f"{API}/integrations/imports/{job['id']}", headers=env.headers)).json()
        assert polled["status"] == "completed" and polled["items"][0]["status"] == "failed"
        assert await env.documents() == []

    async def test_other_orgs_job_is_not_found(self, env, fake):
        response = await env.client.get(f"{API}/integrations/imports/{uuid.uuid4()}", headers=env.headers)
        assert response.status_code == 404


class TestRevocation:
    async def test_revocation_failure_never_fails_the_disconnect(self, env, fake, monkeypatch):
        async def broken(tokens):
            raise http.ConnectorUnavailable("down", 503)

        fake.manage_access_url = "https://fake.example/apps"
        monkeypatch.setattr(fake, "revoke", broken)
        connection_id = await _connect(env)
        response = await env.client.delete(f"{API}/integrations/connections/{connection_id}", headers=env.headers)
        assert response.status_code == 200
        body = response.json()
        assert body["revoked"] is False and body["manage_url"] == "https://fake.example/apps"
        assert (await env.client.get(f"{API}/integrations/connections", headers=env.headers)).json() == []

    async def test_a_successful_revocation(self, env, fake, monkeypatch):
        calls = []

        async def ok(tokens):
            calls.append(tokens.access_token)
            return True

        monkeypatch.setattr(fake, "revoke", ok)
        connection_id = await _connect(env)
        response = await env.client.delete(f"{API}/integrations/connections/{connection_id}", headers=env.headers)
        assert response.json() == {"revoked": True, "manage_url": None, "detail": None}
        assert calls == ["access-1"]

    async def test_google_revokes_the_refresh_token(self, fake_net):
        routes, calls = fake_net
        routes[("POST", "https://oauth2.googleapis.com/revoke")] = lambda r: httpx.Response(200, json={})
        assert await GoogleDrive().revoke(TokenSet("a", "r")) is True
        assert calls[-1].content == b"token=r"

    async def test_google_already_revoked_counts_as_revoked(self, fake_net):
        routes, _ = fake_net
        routes[("POST", "https://oauth2.googleapis.com/revoke")] = lambda r: httpx.Response(400, json={"error": "x"})
        assert await GoogleDrive().revoke(TokenSet("a", "r")) is True

    async def test_notion_revokes(self, fake_net, monkeypatch):
        monkeypatch.setattr(settings, "CONNECTOR_NOTION_CLIENT_ID", "id")
        monkeypatch.setattr(settings, "CONNECTOR_NOTION_CLIENT_SECRET", "secret")
        routes, calls = fake_net
        routes[("POST", "https://api.notion.com/v1/oauth/revoke")] = lambda r: httpx.Response(200, json={})
        assert await Notion().revoke(TokenSet("a")) is True
        assert b'"token"' in calls[-1].content and calls[-1].headers["authorization"].startswith("Basic ")

    async def test_microsoft_and_confluence_cannot_revoke_and_link_instead(self):
        assert await OneDrive().revoke(TokenSet("a", "r")) is False
        assert await Confluence().revoke(TokenSet("a", "r")) is False
        assert OneDrive.manage_access_url.startswith("https://")
        assert Confluence.manage_access_url.startswith("https://id.atlassian.com/")
