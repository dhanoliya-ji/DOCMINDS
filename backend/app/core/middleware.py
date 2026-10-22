"""
middleware.py
=============
WHAT THIS FILE DOES
-------------------
Two pieces of middleware -- code that wraps EVERY request, before any router
or endpoint sees it:

    RequestContextMiddleware   gives each request an id, and writes one
                               structured access-log line when it finishes
    SecurityHeadersMiddleware  adds the browser security headers to every
                               response

WHY "PURE ASGI" AND NOT @app.middleware("http")
-----------------------------------------------
Starlette offers a convenient `BaseHTTPMiddleware`. It runs the rest of the
app in a separate task and buffers in ways that break streaming responses and
make background tasks awkward. These two only need to watch the response
start go past and add headers to it, which plain ASGI does directly: wrap the
`send` function, look at each message, pass it on. No buffering, no extra
task, and it behaves identically on every Starlette version.

THE REQUEST ID
--------------
Each request gets an id -- the caller's own `X-Request-ID` if it sent a sane
one (a load balancer or the frontend may already have assigned it), otherwise
a new random one. It is

    * put in the logging context, so EVERY log line written while serving the
      request carries it (core/observability.py),
    * stored on the usage event the request creates (services/usage.py),
    * returned in the `X-Request-ID` response header.

So a user reporting "my question failed" can quote the id from the browser's
network tab, and one search for it finds every line that request produced.

THE ACCESS LOG
--------------
One line per request, written after the response:

    {"message": "POST /api/v1/chat/sessions/{session_id}/messages 200",
     "method": "POST", "path": "/api/v1/chat/sessions/{session_id}/messages",
     "status": 200, "duration_ms": 812.4, "client_ip": "203.0.113.7",
     "request_id": "3f9c...", "user_id": "...", "org_id": "..."}

`path` is the ROUTE TEMPLATE when one matched, not the literal URL. That
groups all chats together for latency charts -- and keeps secrets that live
in URLs (`/auth/invites/{token}`) out of the logs. The query string is never
logged, for the same reason.
"""

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

# A request id we accept from the caller: short, and only characters that are
# harmless in a log line and a header. Anything else is replaced, so a client
# cannot inject newlines or megabytes into every log line of its request.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestContextMiddleware:
    """Request id + logging context + one access-log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket and lifespan messages pass straight through.
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex

        token = observability.new_context(request_id=request_id)
        started = time.perf_counter()
        # 500 until a response actually starts: if the app raises before
        # sending anything, Starlette's error handler (outside us) sends a 500.
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
    # FastAPI records the matched route in the scope; its `path` is the
    # template ("/documents/{document_id}/preview"). No match (a 404, a CORS
    # preflight) leaves the literal path.
    #
    # Newer FastAPI versions give a template RELATIVE to its router's prefix
    # ("/{document_id}/preview"). The prefix is whatever the literal path has
    # in front of the template's segments, so we put it back in front.
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
        # Hosting platforms poll the health check every few seconds; at INFO
        # those lines would drown out everything else.
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
        # The traceback rides on the same line as the request id, so the
        # crash and the request that caused it are found together.
        exc_info=error,
    )


# ======================================================================
# SECURITY HEADERS
# ======================================================================


def security_headers() -> list[tuple[str, str]]:
    """
    The headers added to every response, and why each one is there.

    Read per response (not once at startup) so ENV and HSTS_MAX_AGE_SECONDS
    are always current -- which also lets the tests flip them.
    """
    headers = [
        # Browsers sometimes "sniff" a response and decide a file served as
        # text is really HTML -- and then run its scripts. This forbids it:
        # the declared Content-Type is final. It matters most for the
        # document preview, which serves files that USERS uploaded.
        ("X-Content-Type-Options", "nosniff"),
        # Never render this API's responses inside a frame on another site,
        # which is how "clickjacking" tricks a user into clicking something
        # they cannot see.
        ("X-Frame-Options", "DENY"),
        # When a link is followed from an API response, send no Referer: URLs
        # here can carry ids that are nobody else's business.
        ("Referrer-Policy", "no-referrer"),
        # A JSON API needs no camera, microphone, location or payment APIs.
        # Denying them means nothing served from here can ever ask for them.
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ]
    if settings.ENV == "production" and settings.HSTS_MAX_AGE_SECONDS > 0:
        # "Only ever talk to this host over HTTPS, for the next year."
        # Production only: on http://localhost it would be ignored at best,
        # and at worst would leave a developer's browser refusing plain HTTP
        # to localhost for a year.
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
                    # An endpoint that set its own value keeps it.
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
