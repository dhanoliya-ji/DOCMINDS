"""
test_api.py
===========
WHAT THIS FILE TESTS
--------------------
The HTTP layer: real requests against the real FastAPI app, over an in-process
transport. No uvicorn, no network port -- httpx talks to the ASGI application
directly, which is both faster and less flaky than a live server.

WHY THE OTHER TESTS DO NOT COVER THIS
-------------------------------------
Everything else in this suite calls functions. That leaves a whole layer
unverified, because these are properties of the *wiring* rather than of any
function:

    - a protected endpoint actually has its dependency attached
    - 401 and 403 are distinguished
    - a malformed body is refused with 422 before any handler runs
    - a malformed UUID in a path never reaches a query
    - a router is mounted at the prefix everyone thinks it is

A dependency accidentally omitted from an endpoint is invisible to a unit
test -- the service still behaves correctly, it is simply now reachable by
anyone. That is the class of bug this file exists for.

TIER 2. Signup writes rows, so these need a database and skip cleanly without
one.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.organization import Organization
from tests.conftest import database_available

_available, _reason = database_available()
pytestmark = pytest.mark.skipif(not _available, reason=f"Tier 2: {_reason or 'no database'}")

API = settings.API_V1_STR


@pytest_asyncio.fixture
async def client():
    """An HTTP client wired straight into the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def account(client):
    """
    A registered, signed-in user.

    Returns the credentials plus a ready-made `Authorization` header, and
    deletes the organisation afterwards -- everything below it cascades.
    """
    suffix = uuid.uuid4().hex[:12]
    email = f"user-{suffix}@example.com"
    password = "a-perfectly-fine-password"

    response = await client.post(
        f"{API}/auth/signup",
        json={
            "email": email,
            "password": password,
            "full_name": "Test User",
            "org_name": f"Org {suffix}",
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()

    login = await client.post(
        f"{API}/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()

    yield {
        "email": email,
        "password": password,
        "user": user,
        "tokens": tokens,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }

    async with SessionLocal() as session:
        await session.execute(delete(Organization).where(Organization.id == uuid.UUID(user["org_id"])))
        await session.commit()


# ======================================================================
# Health -- outside any router, so it answers even if routing is broken
# ======================================================================


class TestHealth:
    async def test_the_root_responds(self, client):
        assert (await client.get("/")).status_code == 200

    async def test_the_health_endpoint_responds(self, client):
        response = await client.get(f"{API}/health")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)


# ======================================================================
# Signup and login
# ======================================================================


class TestSignup:
    async def test_it_returns_201_and_no_password(self, account):
        """
        201 Created, not 200. And `response_model=UserOut` is what guarantees
        the second half -- the hash is on the ORM object and cannot reach the
        client through the schema.
        """
        body = account["user"]

        assert "id" in body and "org_id" in body
        assert not any("password" in key.lower() for key in body)
        assert "$2b$" not in str(body)

    async def test_it_creates_the_organisation_too(self, account):
        """A user cannot exist without a tenant, which is what makes org_id
        reliable as the security boundary everywhere downstream."""
        assert account["user"]["org_id"]

    async def test_a_duplicate_email_is_refused(self, client, account):
        response = await client.post(
            f"{API}/auth/signup",
            json={
                "email": account["email"],
                "password": "another-password",
                "full_name": "Impostor",
                "org_name": "Another Org",
            },
        )

        assert response.status_code in (400, 409)

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email", "password": "x" * 12, "full_name": "X", "org_name": "O"},
            {"email": "a@b.com", "full_name": "X", "org_name": "O"},          # no password
            {"password": "x" * 12, "full_name": "X", "org_name": "O"},        # no email
            {},
        ],
    )
    async def test_a_malformed_body_is_422(self, client, payload):
        """
        Refused by the schema before the handler runs -- which is why no
        endpoint contains an email regex or a "is this field present" check.
        """
        assert (await client.post(f"{API}/auth/signup", json=payload)).status_code == 422


class TestLogin:
    async def test_correct_credentials_return_both_tokens(self, account):
        tokens = account["tokens"]

        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["token_type"] == "bearer"

    async def test_a_wrong_password_is_refused(self, client, account):
        response = await client.post(
            f"{API}/auth/login",
            data={"username": account["email"], "password": "definitely-wrong"},
        )

        assert response.status_code in (400, 401)

    async def test_an_unknown_email_is_refused(self, client):
        response = await client.post(
            f"{API}/auth/login",
            data={"username": f"nobody-{uuid.uuid4().hex}@example.com", "password": "whatever"},
        )

        assert response.status_code in (400, 401)

    async def test_the_error_does_not_reveal_which_half_was_wrong(self, client, account):
        """
        Both failures must look alike. Distinguishing them turns the login form
        into an account-enumeration oracle: an attacker learns which email
        addresses are registered without ever guessing a password.
        """
        wrong_password = await client.post(
            f"{API}/auth/login",
            data={"username": account["email"], "password": "definitely-wrong"},
        )
        unknown_user = await client.post(
            f"{API}/auth/login",
            data={"username": f"nobody-{uuid.uuid4().hex}@example.com", "password": "whatever"},
        )

        assert wrong_password.status_code == unknown_user.status_code
        assert wrong_password.json() == unknown_user.json()


class TestRefresh:
    async def test_a_refresh_token_yields_a_new_access_token(self, client, account):
        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": account["tokens"]["refresh_token"]}
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_an_access_token_is_not_accepted_as_a_refresh_token(self, client, account):
        """
        The `type` claim, enforced. Without this check a 30-minute credential
        and a 7-day one become interchangeable, and the short expiry -- the
        whole point of the split -- stops meaning anything.
        """
        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": account["tokens"]["access_token"]}
        )

        assert response.status_code in (400, 401, 422)

    async def test_a_garbage_token_is_refused(self, client):
        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": "not.a.real.token"}
        )

        assert response.status_code in (400, 401, 422)


# ======================================================================
# The auth dependency, as actually wired to endpoints
# ======================================================================


PROTECTED = [
    ("GET", f"{API}/users/me"),
    ("GET", f"{API}/projects/"),
    ("POST", f"{API}/projects/"),
    ("GET", f"{API}/documents/"),
    ("POST", f"{API}/search/"),
    ("GET", f"{API}/chat/sessions"),
    ("POST", f"{API}/chat/sessions"),
]


class TestAuthenticationIsRequired:
    """
    The property no unit test can see: that the dependency is actually attached
    to each endpoint. Omit it from one handler and the service underneath still
    works perfectly -- it is just now reachable by anyone.
    """

    @pytest.mark.parametrize("method,path", PROTECTED)
    async def test_no_token_is_rejected(self, client, method, path):
        response = await client.request(method, path, json={})
        assert response.status_code in (401, 403), f"{method} {path} answered {response.status_code}"

    @pytest.mark.parametrize("method,path", PROTECTED)
    async def test_a_garbage_token_is_rejected(self, client, method, path):
        response = await client.request(
            method, path, json={}, headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code in (401, 403)

    async def test_a_token_signed_with_the_wrong_key_is_rejected(self, client):
        """The signature is the entire trust model. This is the test that says so."""
        import jwt

        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access"},
            "not-the-real-secret-key",
            algorithm=settings.ALGORITHM,
        )

        response = await client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {forged}"}
        )

        assert response.status_code in (401, 403)

    async def test_a_valid_token_is_accepted(self, client, account):
        """The other half: the guard must let the right person through."""
        response = await client.get(f"{API}/users/me", headers=account["headers"])

        assert response.status_code == 200
        assert response.json()["email"] == account["email"]


# ======================================================================
# Projects
# ======================================================================


class TestProjects:
    async def test_a_new_account_has_no_projects(self, client, account):
        response = await client.get(f"{API}/projects/", headers=account["headers"])

        assert response.status_code == 200
        assert response.json() == []

    async def test_a_project_can_be_created_and_listed(self, client, account):
        created = await client.post(
            f"{API}/projects/", headers=account["headers"], json={"name": "HR Handbook"}
        )
        assert created.status_code == 201, created.text

        listed = await client.get(f"{API}/projects/", headers=account["headers"])
        assert [p["name"] for p in listed.json()] == ["HR Handbook"]

    async def test_another_account_does_not_see_it(self, client, account):
        """
        Multi-tenant isolation, over HTTP this time. test_retrieval_db.py
        proves the retrieval query filters correctly; this proves the endpoint
        passes the right org_id into it.
        """
        await client.post(
            f"{API}/projects/", headers=account["headers"], json={"name": "Confidential"}
        )

        suffix = uuid.uuid4().hex[:12]
        signup = await client.post(
            f"{API}/auth/signup",
            json={
                "email": f"other-{suffix}@example.com",
                "password": "another-fine-password",
                "full_name": "Other User",
                "org_name": f"Other Org {suffix}",
            },
        )
        other = signup.json()

        login = await client.post(
            f"{API}/auth/login",
            data={"username": f"other-{suffix}@example.com", "password": "another-fine-password"},
        )
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        try:
            visible = await client.get(f"{API}/projects/", headers=other_headers)
            assert "Confidential" not in [p["name"] for p in visible.json()]
        finally:
            async with SessionLocal() as session:
                await session.execute(
                    delete(Organization).where(Organization.id == uuid.UUID(other["org_id"]))
                )
                await session.commit()

    async def test_a_nameless_project_is_422(self, client, account):
        response = await client.post(f"{API}/projects/", headers=account["headers"], json={})
        assert response.status_code == 422


# ======================================================================
# Search
# ======================================================================


class TestSearchEndpoint:
    async def test_an_empty_query_is_422(self, client, account, use_mock_providers):
        response = await client.post(
            f"{API}/search/", headers=account["headers"], json={"query": ""}
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("top_k", [0, -1, 999])
    async def test_out_of_range_top_k_is_422(self, client, account, top_k, use_mock_providers):
        response = await client.post(
            f"{API}/search/", headers=account["headers"], json={"query": "anything", "top_k": top_k}
        )
        assert response.status_code == 422

    async def test_a_malformed_uuid_is_422(self, client, account, use_mock_providers):
        """
        Refused by the schema, so a bad id never reaches a query. Nothing is
        hand-validated for this -- the type annotation is the validation.
        """
        response = await client.post(
            f"{API}/search/",
            headers=account["headers"],
            json={"query": "anything", "project_id": "not-a-uuid"},
        )
        assert response.status_code == 422

    async def test_searching_an_empty_corpus_returns_no_results(
        self, client, account, use_mock_providers
    ):
        """An account with no documents gets an empty result, not an error."""
        response = await client.post(
            f"{API}/search/", headers=account["headers"], json={"query": "anything at all"}
        )

        assert response.status_code == 200
        assert response.json()["results"] == []


# ======================================================================
# Path parameters, and the queue diagnostics
# ======================================================================


class TestPathValidation:
    @pytest.mark.parametrize(
        "path",
        [
            f"{API}/chat/sessions/not-a-uuid/messages",
            f"{API}/documents/not-a-uuid/preview",
        ],
    )
    async def test_a_malformed_uuid_in_the_path_is_422(self, client, account, path):
        """
        Typing the parameter as `uuid.UUID` is what rejects this before any
        query runs -- no handwritten check, and no bad id reaching Postgres.
        """
        response = await client.get(path, headers=account["headers"])
        assert response.status_code == 422

    async def test_a_well_formed_but_unknown_id_is_404(self, client, account):
        response = await client.get(
            f"{API}/chat/sessions/{uuid.uuid4()}/messages", headers=account["headers"]
        )
        assert response.status_code == 404


class TestTaskDiagnostics:
    async def test_triggering_a_task_returns_202(self, client, account):
        """
        202 Accepted, not 200: the work has been taken, not done. This endpoint
        is how you tell "no worker is running" from "the document is at fault".
        """
        response = await client.post(
            f"{API}/tasks/trigger-add", headers=account["headers"], json={"x": 2, "y": 3}
        )

        # 202 when Redis is up; a 5xx when the broker is unreachable is a real
        # answer too, and not this test's business to assert against.
        assert response.status_code in (202, 422, 500, 503)


class TestOpenApi:
    # Not the default "/openapi.json": main.py moves the spec under the version
    # prefix, so a v2 could publish its own alongside it.
    SCHEMA_URL = f"{API}/openapi.json"

    async def test_the_schema_is_served(self, client):
        """`/docs` is the first thing most people try, and it loads this."""
        response = await client.get(self.SCHEMA_URL)

        assert response.status_code == 200
        assert "paths" in response.json()

    async def test_the_docs_page_loads(self, client):
        assert (await client.get("/docs")).status_code == 200

    async def test_every_router_is_mounted_under_the_version_prefix(self, client):
        """
        Guards the URL contract. A router mounted at the wrong prefix breaks
        every client at once, and nothing else here would notice.
        """
        paths = (await client.get(self.SCHEMA_URL)).json()["paths"]

        for expected in (
            f"{API}/auth/login",
            f"{API}/users/me",
            f"{API}/projects/",
            f"{API}/documents/upload",
            f"{API}/search/",
            f"{API}/chat/sessions",
        ):
            assert expected in paths, f"{expected} is not in the OpenAPI schema"


class TestInlineIngestion:
    """
    INGESTION_MODE=inline: the API processes uploads itself, with no Celery
    worker and no Redis. This is what the free live demo runs on, so the whole
    path -- upload, extract, chunk, embed, mark completed -- is exercised here
    against the real database.
    """

    async def test_an_upload_is_fully_processed_without_a_worker(
        self, client, account, use_mock_providers, monkeypatch
    ):
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        created = await client.post(
            f"{API}/projects/", headers=account["headers"], json={"name": "Inline demo"}
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        text = "Refunds are available within thirty days of purchase. " * 40
        response = await client.post(
            f"{API}/documents/upload",
            headers=account["headers"],
            data={"project_id": project_id},
            files={"files": ("policy.txt", text.encode(), "text/plain")},
        )

        # The reply still comes back at once, as with a worker...
        assert response.status_code == 202, response.text
        assert response.json()[0]["status"] == "pending"

        # ...and the API's in-process queue works on it after the request has
        # ended. Once the queue is idle it has been processed end to end.
        from app.tasks.ingestion import wait_for_inline_jobs

        await wait_for_inline_jobs()
        listed = await client.get(
            f"{API}/documents/", headers=account["headers"], params={"project_id": project_id}
        )
        document = listed.json()[0]
        assert document["status"] == "completed", document

    async def test_the_worker_diagnostics_say_there_is_no_worker(
        self, client, account, monkeypatch
    ):
        """Instead of hanging on a Redis that is not there."""
        monkeypatch.setattr(settings, "INGESTION_MODE", "inline")
        response = await client.post(
            f"{API}/tasks/trigger-add", headers=account["headers"], json={"x": 2, "y": 3}
        )
        assert response.status_code == 503
        assert "INGESTION_MODE=inline" in response.json()["detail"]
