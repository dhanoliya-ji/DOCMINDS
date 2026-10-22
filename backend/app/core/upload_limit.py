"""
upload_limit.py
===============
WHAT THIS FILE DOES
-------------------
Refuses an over-sized upload request with 413 BEFORE its body is parsed.

WHY THE ENDPOINT'S OWN CHECK IS NOT ENOUGH
------------------------------------------
By the time the upload endpoint runs, FastAPI has already parsed the whole
multipart body: every file has been read off the network and spooled to a
temporary file on disk. The endpoint's per-file check (services/uploads.py)
gives a precise "this file is too big" message, but the disk space and the
bandwidth have already been spent. A client could POST 20 GB and the server
would dutifully write 20 GB to /tmp before saying no.

This middleware sits in front of all of that and looks at the request as it
arrives:

    Content-Length present and over the limit
        -> 413 immediately; not one byte of the body is read
    no Content-Length (chunked transfer) or a lying one
        -> the bytes are COUNTED as they stream in, and the moment the total
           crosses the limit the request is cut off and answered with 413

The limit is for the WHOLE request: settings.max_upload_request_bytes, i.e.
MAX_UPLOAD_SIZE_BYTES x MAX_FILES_PER_UPLOAD plus a little room for the form
fields. Only the upload path is checked; every other endpoint is untouched.

WHY A "PURE ASGI" MIDDLEWARE
----------------------------
Starlette's convenient `BaseHTTPMiddleware` reads the request through its own
wrapper, which makes counting the body as it streams awkward. An ASGI
middleware is just a callable that receives `(scope, receive, send)`, so it
can wrap `receive` -- the function the app calls to pull each piece of the
body -- and count every piece on its way past.
"""

import json

from app.core.config import settings


class _BodyTooLarge(Exception):  # noqa: N818 -- reads better at the raise site
    """Raised from inside `receive` when the running total crosses the limit."""


class UploadSizeLimitMiddleware:
    def __init__(self, app, path_suffix: str = "/documents/upload"):
        self.app = app
        self.path_suffix = path_suffix

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").rstrip("/").endswith(self.path_suffix)
        ):
            await self.app(scope, receive, send)
            return

        limit = settings.max_upload_request_bytes

        # ---- 1. The cheap check: what the client SAYS it will send ----
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await self._send_413(send, limit)
                    return

        # ---- 2. The real check: what it ACTUALLY sends ----
        received = 0
        exceeded = False
        response_started = False

        async def counting_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal response_started
            # Once the limit is crossed, whatever the app tries to answer
            # (FastAPI turns a body-read failure into a generic 400) is
            # replaced by our own 413 below.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            # Our own _BodyTooLarge, or whatever the framework turned it into
            # on the way back out. Anything raised for another reason is
            # someone else's error and must keep propagating.
            if not exceeded:
                raise

        if exceeded and not response_started:
            await self._send_413(send, limit)

    @staticmethod
    async def _send_413(send, limit: int) -> None:
        body = json.dumps(
            {
                "detail": (
                    "The upload is too large. The limit is "
                    f"{settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB per file and "
                    f"{limit // (1024 * 1024)} MB per request."
                )
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    # Tell the client we will not read the rest of its body.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
