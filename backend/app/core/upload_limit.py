"""Refuses an over-sized upload request with 413 BEFORE its body is parsed."""
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

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await self._send_413(send, limit)
                    return

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
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
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
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
