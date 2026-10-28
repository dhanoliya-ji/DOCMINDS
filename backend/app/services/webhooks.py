"""Everything about OUTGOING webhooks except the HTTP endpoints admins use to"""
import asyncio
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.webhook import FAILED, PENDING, SUCCEEDED, WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


EVENT_TYPES: Dict[str, str] = {
    "document.uploaded": "A document was uploaded and queued for processing.",
    "document.processed": "A document finished processing and is now searchable.",
    "document.failed": "A document could not be processed (the reason is included).",
    "document.deleted": "A document was deleted.",
    "member.joined": "Someone accepted an invite and joined the organization.",
    "member.role_changed": "A member's role was changed.",
    "member.deactivated": "A member was deactivated.",
    "member.reactivated": "A member was reactivated.",
    "member.removed": "A member was removed from the organization.",
    "invite.created": "An invite was sent.",
    "api_key.created": "An API key was created.",
    "api_key.revoked": "An API key was revoked.",
    "ping": "A test event sent from the webhook settings.",
}

WILDCARD = "*"

AUDIT_EVENT_MAP: Dict[str, str] = {
    "invite.accept": "member.joined",
    "member.role_change": "member.role_changed",
    "member.deactivate": "member.deactivated",
    "member.reactivate": "member.reactivated",
    "member.remove": "member.removed",
    "invite.create": "invite.created",
    "document.upload": "document.uploaded",
    "document.delete": "document.deleted",
    "api_key.create": "api_key.created",
    "api_key.revoke": "api_key.revoked",
}


def generate_secret() -> str:
    """A new signing secret: "whsec_" + 32 random bytes as hex."""
    return "whsec_" + secrets.token_hex(32)


def wants(endpoint: WebhookEndpoint, event_type: str) -> bool:
    """Does this endpoint subscribe to this event?"""
    types = endpoint.event_types or []
    return WILDCARD in types or event_type in types


class WebhookURLError(ValueError):
    """The URL may not be used as a webhook target. The message says why."""


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def _resolve(host: str, port: int) -> List[str]:
    """Every address `host` resolves to. Kept separate so tests can replace it."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def validate_target_url(url: str) -> str:
    """Return the cleaned URL if it is an acceptable webhook target; otherwise"""
    cleaned = (url or "").strip()
    if len(cleaned) > 2048:
        raise WebhookURLError("The URL is too long (2048 characters at most).")
    parts = urlsplit(cleaned)

    allowed_schemes = ("https", "http") if settings.WEBHOOK_ALLOW_HTTP else ("https",)
    if parts.scheme not in allowed_schemes:
        raise WebhookURLError(
            "The URL must start with https://"
            + ("" if settings.WEBHOOK_ALLOW_HTTP else " (plain http is switched off).")
        )
    if not parts.hostname:
        raise WebhookURLError("The URL has no host name.")
    if parts.username or parts.password:
        raise WebhookURLError("The URL may not contain a username or password.")

    if settings.WEBHOOK_ALLOW_PRIVATE_TARGETS:
        return cleaned

    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise WebhookURLError("The URL has an invalid port.") from None

    try:
        addresses = _resolve(parts.hostname, port)
    except (socket.gaierror, UnicodeError):
        raise WebhookURLError(f"The host name '{parts.hostname}' does not resolve.") from None
    if not addresses:
        raise WebhookURLError(f"The host name '{parts.hostname}' does not resolve.")

    for address in addresses:
        try:
            public = _is_public(address)
        except ValueError:
            public = False
        if not public:
            raise WebhookURLError(
                "The URL points at a private, loopback or reserved network "
                "address, which webhooks may not call."
            )
    return cleaned


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 of "<timestamp>.<body>", as lowercase hex."""
    message = str(timestamp).encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: int, body: bytes, header: str) -> bool:
    """The receiver's side of `sign`, used by the tests and the docs example."""
    expected = "sha256=" + sign(secret, timestamp, body)
    return hmac.compare_digest(expected, header or "")


def encode_body(payload: Dict[str, Any]) -> bytes:
    """The exact bytes POSTed: compact, key-sorted JSON (stable to re-sign)."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


_PENDING_KEY = "webhook_deliveries_to_send"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


async def emit(
    db: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
) -> List[WebhookDelivery]:
    """Stage one delivery per active endpoint of `org_id` subscribed to"""
    if not settings.WEBHOOKS_ENABLED:
        return []
    try:
        result = await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.org_id == org_id, WebhookEndpoint.is_active.is_(True)
            )
        )
        endpoints = [ep for ep in result.scalars().all() if wants(ep, event_type)]
        if not endpoints:
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        staged: List[WebhookDelivery] = []
        for endpoint in endpoints:
            delivery_id = uuid.uuid4()
            delivery = WebhookDelivery(
                id=delivery_id,
                endpoint_id=endpoint.id,
                org_id=org_id,
                event_type=event_type,
                payload={
                    "id": str(delivery_id),
                    "type": event_type,
                    "created_at": now.isoformat(),
                    "org_id": str(org_id),
                    "data": _jsonable(data or {}),
                },
                status=PENDING,
                attempts=0,
                next_attempt_at=now,
            )
            db.add(delivery)
            staged.append(delivery)

        send_after_commit(db, [d.id for d in staged])
        return staged
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not stage webhook deliveries", extra={"error": repr(exc)})
        return []


async def emit_for_audit(
    db: AsyncSession,
    action: str,
    *,
    org_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    details: Dict[str, Any],
) -> None:
    """Called by audit.record(): turn an audit action into a webhook event."""
    event_type = AUDIT_EVENT_MAP.get(action)
    if event_type is None:
        return
    data = dict(details or {})
    data["actor_user_id"] = str(user_id) if user_id else None
    await emit(db, org_id, event_type, data)


def send_after_commit(db: AsyncSession, delivery_ids: Iterable[uuid.UUID]) -> None:
    """Remember these ids on the session; send them once it COMMITS."""
    sync_session = db.sync_session
    pending = sync_session.info.setdefault(_PENDING_KEY, [])
    pending.extend(delivery_ids)

    if sync_session.info.get("webhook_listeners"):
        return
    sync_session.info["webhook_listeners"] = True

    def _after_commit(session) -> None:
        ids = session.info.pop(_PENDING_KEY, [])
        for delivery_id in ids:
            dispatch(str(delivery_id))

    def _after_rollback(session) -> None:
        session.info.pop(_PENDING_KEY, None)

    sa_event.listen(sync_session, "after_commit", _after_commit)
    sa_event.listen(sync_session, "after_soft_rollback", _after_rollback)


_inline_jobs: set = set()


def dispatch(delivery_id: str) -> None:
    """Hand one delivery to whatever sends it."""
    try:
        if settings.INGESTION_MODE == "celery":
            from app.tasks.webhooks import deliver_webhook_task

            deliver_webhook_task.apply_async(args=[delivery_id], retry=False)
        else:
            loop = asyncio.get_running_loop()
            job = loop.create_task(deliver_with_retries(delivery_id))
            _inline_jobs.add(job)
            job.add_done_callback(_inline_jobs.discard)
    except Exception as exc:
        logger.warning(
            "Could not queue webhook delivery; the sweep will retry it",
            extra={"delivery_id": delivery_id, "error": repr(exc)},
        )


_transport = None


def retry_delay_seconds(attempts_so_far: int) -> int:
    """30s, 60s, 120s, ... capped at one hour (with the default base)."""
    base = max(1, settings.WEBHOOK_RETRY_BACKOFF_SECONDS)
    return int(min(3600, base * (2 ** max(0, attempts_so_far - 1))))


async def _send(url: str, body: bytes, headers: Dict[str, str]) -> Tuple[Optional[int], Optional[str]]:
    """POST once. Returns (status code or None, error text or None)."""
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=settings.WEBHOOK_TIMEOUT_SECONDS,
            transport=_transport,
            follow_redirects=False,
        ) as client:
            response = await client.post(url, content=body, headers=headers)
        if 200 <= response.status_code < 300:
            return response.status_code, None
        return response.status_code, f"HTTP {response.status_code}"
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPError as exc:
        return None, f"connection error: {type(exc).__name__}"


async def deliver(delivery_id: str, session_factory=None) -> Tuple[str, int]:
    """Make ONE attempt at a delivery and record the outcome."""
    if session_factory is None:
        from app.db.session import SessionLocal as session_factory  # noqa: N813

    async with session_factory() as db:
        delivery = await db.get(WebhookDelivery, uuid.UUID(str(delivery_id)))
        if delivery is None or delivery.status != PENDING:
            return "skipped", 0
        endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            delivery.status = FAILED
            delivery.last_error = "endpoint deleted or paused"
            delivery.next_attempt_at = None
            await db.commit()
            return "skipped", 0

        delivery.attempts += 1
        now = datetime.datetime.now(datetime.timezone.utc)

        try:
            url = await asyncio.to_thread(validate_target_url, endpoint.url)
        except WebhookURLError as exc:
            delivery.status = FAILED
            delivery.last_error = f"blocked: {exc}"
            delivery.next_attempt_at = None
            await db.commit()
            return "failed", 0

        body = encode_body(delivery.payload)
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DocMinds-Webhooks/1.0",
            "X-DocMinds-Event": delivery.event_type,
            "X-DocMinds-Delivery": str(delivery.id),
            "X-DocMinds-Timestamp": str(timestamp),
            "X-DocMinds-Signature": "sha256=" + sign(endpoint.secret, timestamp, body),
        }
        status_code, error = await _send(url, body, headers)
        delivery.last_status_code = status_code

        if error is None:
            delivery.status = SUCCEEDED
            delivery.last_error = None
            delivery.delivered_at = now
            delivery.next_attempt_at = None
            outcome = ("succeeded", 0)
        elif delivery.attempts >= max(1, settings.WEBHOOK_MAX_ATTEMPTS):
            delivery.status = FAILED
            delivery.last_error = error
            delivery.next_attempt_at = None
            outcome = ("failed", 0)
        else:
            wait = retry_delay_seconds(delivery.attempts)
            delivery.last_error = error
            delivery.next_attempt_at = now + datetime.timedelta(seconds=wait)
            outcome = ("retry", wait)

        await db.commit()
        logger.info(
            "Webhook delivery attempt",
            extra={
                "delivery_id": str(delivery.id),
                "event_type": delivery.event_type,
                "outcome": outcome[0],
                "status_code": status_code,
                "attempt": delivery.attempts,
            },
        )
        return outcome


async def deliver_with_retries(delivery_id: str, session_factory=None) -> str:
    """Inline mode: attempt, sleep, attempt... until done (no Celery)."""
    while True:
        outcome, wait = await deliver(delivery_id, session_factory=session_factory)
        if outcome != "retry":
            return outcome
        await asyncio.sleep(wait)


async def due_delivery_ids(session_factory=None, *, grace_seconds: int = 300) -> List[str]:
    """Pending deliveries whose next attempt is overdue by more than"""
    if session_factory is None:
        from app.db.session import SessionLocal as session_factory  # noqa: N813

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=grace_seconds)
    async with session_factory() as db:
        result = await db.execute(
            select(WebhookDelivery.id)
            .where(WebhookDelivery.status == PENDING, WebhookDelivery.next_attempt_at < cutoff)
            .limit(500)
        )
        return [str(row) for row in result.scalars().all()]
