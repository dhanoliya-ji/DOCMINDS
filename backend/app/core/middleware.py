"""Two pieces of middleware -- code that wraps EVERY request, before any router or endpoint sees it."""
import logging
import re
import time
import uuid
from typing import Optional

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import observability
from app.core.client_ip import client_ip_from_scope
from app.core.config import settings

access_logger = logging.getLogger("app.access")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestContextMiddleware:
    """Request id + logging context + one access-log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex

        token = observability.new_context(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        error: Optional[BaseException] = None
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            error = exc
            raise
        finally:
            _log_request(scope, status_code, started, error)
            observability.reset_context(token)


def _log_request(
    scope: Scope, status_code: int, started: float, error: Optional[BaseException]
) -> None:
    """Write the access-log line for one finished request."""
    failed = error is not None
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    route = scope.get("route")
    literal = scope.get("path", "")
    template = getattr(route, "path", None)
    if template:
        literal_parts = literal.rstrip("/").split("/")
        template_parts = template.rstrip("/").split("/")
        prefix_len = len(literal_parts) - len(template_parts)
        prefix = "/".join(literal_parts[: prefix_len + 1]) if prefix_len > 0 else ""
        path = prefix + template if prefix and not template.startswith(prefix + "/") else template
    else:
        path = literal
    method = scope.get("method", "")

    if failed or status_code >= 500:
        level = logging.ERROR
    elif path.startswith(f"{settings.API_V1_STR}/health"):
        level = logging.DEBUG
    else:
        level = logging.INFO

    access_logger.log(
        level,
        "%s %s %s",
        method,
        path,
        status_code,
        extra={
            "method": method,
            "path": path,
            "status": status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip_from_scope(scope),
        },
        exc_info=error,
    )


def security_headers() -> list[tuple[str, str]]:
    """The headers added to every response, and why each one is there."""
    headers = [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ]
    if settings.ENV == "production" and settings.HSTS_MAX_AGE_SECONDS > 0:
        headers.append(
            (
                "Strict-Transport-Security",
                f"max-age={settings.HSTS_MAX_AGE_SECONDS}; includeSubDomains",
            )
        )
    return headers


class SecurityHeadersMiddleware:
    """Adds `security_headers()` to every HTTP response that lacks them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in security_headers():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
