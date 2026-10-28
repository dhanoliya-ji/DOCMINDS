import json

import pytest

from app.core.config import settings
from app.core.upload_limit import UploadSizeLimitMiddleware

UPLOAD_PATH = f"{settings.API_V1_STR}/documents/upload"


@pytest.fixture
def limit(monkeypatch) -> int:
    """A small ceiling, so the tests send kilobytes rather than gigabytes."""
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 1000)
    monkeypatch.setattr(settings, "MAX_FILES_PER_UPLOAD", 1)
    return settings.max_upload_request_bytes


class ReadingApp:
    """Stands in for FastAPI: reads the whole body, then answers 200."""

    def __init__(self, on_receive_error: str = "respond"):
        self.called = False
        self.bytes_read = 0
        self.on_receive_error = on_receive_error

    async def __call__(self, scope, receive, send):
        self.called = True
        try:
            while True:
                message = await receive()
                self.bytes_read += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
        except Exception:
            if self.on_receive_error == "raise":
                raise
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"parse error"})
            return

        body = str(self.bytes_read).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


def http_scope(path: str = UPLOAD_PATH, method: str = "POST", content_length=None) -> dict:
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "method": method, "path": path, "headers": headers}


async def run(app, scope: dict, chunks: list[bytes]):
    """Push `chunks` through the middleware as the request body."""
    queue = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await UploadSizeLimitMiddleware(app)(scope, receive, send)

    starts = [m for m in sent if m["type"] == "http.response.start"]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return (starts[0]["status"] if starts else None), body, sent


class TestDeclaredLength:
    async def test_an_honest_oversized_request_is_refused_unread(self, limit):
        app = ReadingApp()
        status, body, _ = await run(app, http_scope(content_length=limit + 1), [b"x"])

        assert status == 413
        assert app.called is False
        assert "per request" in json.loads(body)["detail"]

    async def test_the_413_tells_the_client_to_stop_sending(self, limit):
        _, _, sent = await run(ReadingApp(), http_scope(content_length=limit + 1), [b"x"])
        headers = dict(sent[0]["headers"])
        assert headers[b"connection"] == b"close"
        assert headers[b"content-type"] == b"application/json"

    async def test_exactly_the_limit_is_allowed(self, limit):
        app = ReadingApp()
        status, body, _ = await run(app, http_scope(content_length=limit), [b"a" * limit])

        assert status == 200
        assert body == str(limit).encode()

    async def test_a_garbled_length_falls_back_to_counting(self, limit):
        scope = http_scope()
        scope["headers"].append((b"content-length", b"not-a-number"))

        status, _, _ = await run(ReadingApp(), scope, [b"a" * 10])

        assert status == 200


class TestCountedBody:
    async def test_a_chunked_upload_is_cut_off_when_it_crosses(self, limit):
        """No Content-Length at all, as with Transfer-Encoding: chunked."""
        app = ReadingApp()
        piece = b"a" * (256 * 1024)
        chunks = [piece] * 8  # 2 MB, about twice the ceiling

        status, body, sent = await run(app, http_scope(), chunks)

        assert status == 413
        assert "too large" in json.loads(body)["detail"]
        assert app.bytes_read < limit
        assert [m["type"] for m in sent].count("http.response.start") == 1

    async def test_a_lying_content_length_does_not_help(self, limit):
        chunks = [b"a" * (512 * 1024)] * 4

        status, _, _ = await run(ReadingApp(), http_scope(content_length=10), chunks)

        assert status == 413

    async def test_an_app_that_lets_the_error_escape_still_gets_a_413(self, limit):
        """Whatever the framework turns the interrupted read into."""
        app = ReadingApp(on_receive_error="raise")
        chunks = [b"a" * (512 * 1024)] * 4

        status, _, _ = await run(app, http_scope(), chunks)

        assert status == 413

    async def test_a_small_chunked_upload_passes_through(self, limit):
        status, body, _ = await run(ReadingApp(), http_scope(), [b"a" * 100, b"b" * 100])

        assert status == 200
        assert body == b"200"


class TestScope:
    @pytest.mark.parametrize(
        "scope",
        [
            http_scope(path=f"{settings.API_V1_STR}/chat/sessions"),
            http_scope(method="GET"),
        ],
        ids=["another-path", "not-a-post"],
    )
    async def test_other_requests_are_not_limited(self, limit, scope):
        scope["headers"].append((b"content-length", str(limit * 10).encode()))
        app = ReadingApp()

        status, _, _ = await run(app, scope, [b"a" * 10])

        assert status == 200
        assert app.called

    async def test_a_trailing_slash_is_still_the_upload_path(self, limit):
        scope = http_scope(path=UPLOAD_PATH + "/", content_length=limit + 1)
        status, _, _ = await run(ReadingApp(), scope, [b"x"])
        assert status == 413

    async def test_non_http_traffic_is_passed_straight_through(self, limit):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        await UploadSizeLimitMiddleware(app)({"type": "lifespan"}, None, None)

        assert seen == ["lifespan"]

    async def test_an_unrelated_error_is_not_disguised_as_a_413(self, limit):
        """A bug in an endpoint must surface as itself."""

        async def broken(scope, receive, send):
            await receive()
            raise RuntimeError("a genuine bug")

        with pytest.raises(RuntimeError, match="genuine bug"):
            await run(broken, http_scope(), [b"a" * 10])
