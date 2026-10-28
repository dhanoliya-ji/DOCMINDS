"""Plans and payment: which plan an organization is on, what that plan allows, and the conversations with the..."""
import dataclasses
import datetime
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, Mapping, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import BillingEvent
from app.models import Organization
from app.services import audit

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
RAZORPAY_API = "https://api.razorpay.com/v1"
PLANS = ("free", "pro", "team")
PAID_PLANS = ("pro", "team")
CANCELLING = "cancelling"
KEEPS_PLAN = ("active", "trialing", "past_due", "pending", CANCELLING)
ENDED = ("canceled", "cancelled", "completed", "expired")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_]{1,255}$")

_transport: Optional[httpx.AsyncBaseTransport] = None


class BillingError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SignatureError(Exception):
    """The webhook's signature does not verify (or the body is unusable)."""


@dataclasses.dataclass
class BillingUpdate:
    """One verified webhook event, in provider-neutral terms."""

    provider: str
    event_id: str
    event_type: str
    action: str
    org_id: Optional[str] = None
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    period_end: Optional[datetime.datetime] = None


def plan_limits(plan: str) -> Dict[str, int]:
    if plan in PAID_PLANS:
        configured = settings.BILLING_PLAN_LIMITS.get(plan) or {}
        return {
            "monthly_tokens": int(configured.get("monthly_tokens", settings.QUOTA_MONTHLY_TOKENS)),
            "max_documents": int(configured.get("max_documents", settings.QUOTA_MAX_DOCUMENTS)),
            "max_storage_bytes": int(configured.get("max_storage_bytes", settings.QUOTA_MAX_STORAGE_BYTES)),
        }
    return {
        "monthly_tokens": settings.QUOTA_MONTHLY_TOKENS,
        "max_documents": settings.QUOTA_MAX_DOCUMENTS,
        "max_storage_bytes": settings.QUOTA_MAX_STORAGE_BYTES,
    }


def price_for(plan: str) -> str:
    """The Stripe price id for a plan."""
    return {"pro": settings.STRIPE_PRICE_PRO, "team": settings.STRIPE_PRICE_TEAM}.get(plan, "")


def plan_for_price(price_id: Optional[str]) -> Optional[str]:
    for plan in PAID_PLANS:
        if price_id and price_for(plan) == price_id:
            return plan
    return None


def razorpay_plan_for(plan: str) -> str:
    """The Razorpay plan id for a plan."""
    return {"pro": settings.RAZORPAY_PLAN_PRO, "team": settings.RAZORPAY_PLAN_TEAM}.get(plan, "")


def plan_for_razorpay_plan(plan_id: Optional[str]) -> Optional[str]:
    for plan in PAID_PLANS:
        if plan_id and razorpay_plan_for(plan) == plan_id:
            return plan
    return None


def apply_plan(org: Organization, plan: str) -> None:
    """Put `org` on `plan`: the plan name and the three quota columns."""
    org.plan = plan
    if plan in PAID_PLANS:
        limits = plan_limits(plan)
        org.monthly_token_quota = limits["monthly_tokens"]
        org.max_documents = limits["max_documents"]
        org.max_storage_bytes = limits["max_storage_bytes"]
    else:
        org.monthly_token_quota = None
        org.max_documents = None
        org.max_storage_bytes = None


async def _request(
    host_name: str, url: str, *, auth: httpx.Auth,
    data: Optional[Dict[str, str]] = None, json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """POST to a provider. Raises BillingError with an admin-safe message."""
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=_transport, follow_redirects=False) as client:
            response = await client.post(url, data=data, json=json_body, headers=headers or {}, auth=auth)
    except httpx.HTTPError as exc:
        raise BillingError(f"Could not reach {host_name}. Please try again.", 503) from exc
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if response.status_code >= 400:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        logger.error(f"{host_name} request failed", extra={
            "path": httpx.URL(url).path, "status": response.status_code,
            "provider_error": error.get("message") or error.get("description"),
        })
        raise BillingError(f"{host_name} refused the request. The server log has the details.", 502)
    return body


def _epoch(value: Any) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromtimestamp(int(value), tz=datetime.timezone.utc) if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _header(headers: Mapping[str, str], name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or ""


def _holder(org: Organization) -> Optional[str]:
    """The provider holding this organization's subscription, if any."""
    return org.billing_provider or ("stripe" if org.stripe_customer_id else None)


class BillingProvider:
    """The interface every provider fills in:"""

    name = ""
    title = ""
    has_portal = False

    def configured(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def plan_ref(self, plan: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def purchasable(self, plan: str) -> bool:
        return plan in PAID_PLANS and self.configured() and bool(self.plan_ref(plan))

    async def checkout(self, db: AsyncSession, org: Organization, plan: str, email: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def portal(self, org: Organization) -> str:
        raise BillingError(f"{self.title} has no customer portal; use Cancel subscription instead.", 409)

    async def cancel(self, org: Organization) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> BillingUpdate:  # pragma: no cover
        raise NotImplementedError

    def can_cancel(self, org: Organization) -> bool:
        """Does this provider hold a live, not-yet-cancelling subscription for `org`?"""
        return (
            _holder(org) == self.name and bool(org.stripe_subscription_id)
            and (org.subscription_status or "") in KEEPS_PLAN and org.subscription_status != CANCELLING
        )

    def _subscription_to_cancel(self, org: Organization) -> str:
        sub = org.stripe_subscription_id or ""
        if not self.can_cancel(org) or not _SAFE_ID.match(sub):
            raise BillingError("This organization has no active subscription to cancel.", 409)
        return sub


class StripeProvider(BillingProvider):
    name = "stripe"
    title = "Stripe"
    has_portal = True

    def configured(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY)

    def plan_ref(self, plan: str) -> str:
        return price_for(plan)

    async def _call(self, path: str, data: Dict[str, str], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if not settings.STRIPE_SECRET_KEY:
            raise BillingError("Billing is not configured on this server (STRIPE_SECRET_KEY).", 503)
        return await _request(
            "Stripe", f"{STRIPE_API}{path}", data=data,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
            auth=httpx.BasicAuth(settings.STRIPE_SECRET_KEY, ""),
        )

    async def ensure_customer(self, db: AsyncSession, org: Organization, email: str) -> str:
        if org.stripe_customer_id:
            return org.stripe_customer_id
        customer = await self._call(
            "/customers",
            {"name": org.name, "email": email, "metadata[org_id]": str(org.id)},
            idempotency_key=f"customer-{org.id}",
        )
        org.stripe_customer_id = customer["id"]
        await db.commit()
        return customer["id"]

    async def checkout(self, db: AsyncSession, org: Organization, plan: str, email: str) -> str:
        price = price_for(plan)
        if not price:
            raise BillingError(f"The {plan} plan has no Stripe price configured.", 503)
        customer = await self.ensure_customer(db, org, email)
        base = settings.frontend_url
        session = await self._call(
            "/checkout/sessions",
            {
                "mode": "subscription",
                "customer": customer,
                "client_reference_id": str(org.id),
                "line_items[0][price]": price,
                "line_items[0][quantity]": "1",
                "metadata[org_id]": str(org.id),
                "metadata[plan]": plan,
                "subscription_data[metadata][org_id]": str(org.id),
                "subscription_data[metadata][plan]": plan,
                "allow_promotion_codes": "true",
                "success_url": f"{base}/settings/billing?checkout=success",
                "cancel_url": f"{base}/settings/billing?checkout=cancelled",
            },
        )
        return session["url"]

    async def portal(self, org: Organization) -> str:
        if not org.stripe_customer_id:
            raise BillingError("This organization has no billing account yet. Choose a plan first.", 409)
        session = await self._call(
            "/billing_portal/sessions",
            {"customer": org.stripe_customer_id, "return_url": f"{settings.frontend_url}/settings/billing"},
        )
        return session["url"]

    async def cancel(self, org: Organization) -> None:
        sub = self._subscription_to_cancel(org)
        await self._call(f"/subscriptions/{sub}", {"cancel_at_period_end": "true"})

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> BillingUpdate:
        verify_signature(
            payload, _header(headers, "Stripe-Signature"), settings.STRIPE_WEBHOOK_SECRET,
            tolerance=settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS,
        )
        return stripe_update(parse_event(payload))


def verify_signature(payload: bytes, header: str, secret: str, *, tolerance: int, now: Optional[float] = None) -> None:
    """Stripe's scheme: header "t=<unix time>,v1=<hex>[,v1=...]", where v1 is"""
    if not secret:
        raise SignatureError("STRIPE_WEBHOOK_SECRET is not set.")
    timestamp: Optional[int] = None
    signatures = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("Malformed Stripe-Signature header.")
    now = time.time() if now is None else now
    if abs(now - timestamp) > tolerance:
        raise SignatureError("The webhook is too old (or the clock is wrong).")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected.encode(), c.encode("utf-8", "replace")) for c in signatures):
        raise SignatureError("The signature does not match.")


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    """The header Stripe would send -- used by the tests and local tooling."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def parse_event(payload: bytes) -> Dict[str, Any]:
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise SignatureError("The body is not JSON.") from exc
    if not isinstance(event, dict):
        raise SignatureError("The body is not a webhook event.")
    return event


def _stripe_period_end(sub: Dict[str, Any]) -> Optional[datetime.datetime]:
    end = sub.get("current_period_end")
    if end is None:
        items = ((sub.get("items") or {}).get("data") or [{}])
        end = items[0].get("current_period_end") if items else None
    return _epoch(end)


def _stripe_price(sub: Dict[str, Any]) -> Optional[str]:
    items = (sub.get("items") or {}).get("data") or []
    return ((items[0].get("price") or {}).get("id")) if items else None


def stripe_update(event: Dict[str, Any]) -> BillingUpdate:
    """Translate one Stripe event into a BillingUpdate."""
    event_type = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object")) or {}
    metadata = obj.get("metadata") or {}
    customer = obj.get("customer")
    update = BillingUpdate(
        provider="stripe", event_id=str(event.get("id") or ""), event_type=event_type, action="ignore",
        org_id=metadata.get("org_id") or obj.get("client_reference_id"),
        customer_id=customer if isinstance(customer, str) else None,
    )
    if event_type == "checkout.session.completed":
        update.subscription_id = obj.get("subscription")
        plan = metadata.get("plan")
        if plan in PAID_PLANS and obj.get("payment_status") in (None, "paid", "no_payment_required"):
            update.action, update.plan, update.status = "sync", plan, "active"
        else:
            update.action = "link"
    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        status = obj.get("status")
        if status == "active" and obj.get("cancel_at_period_end"):
            status = CANCELLING
        update.action = "sync"
        update.subscription_id = obj.get("id")
        update.plan = plan_for_price(_stripe_price(obj)) or metadata.get("plan")
        update.status = status
        update.period_end = _stripe_period_end(obj)
    elif event_type == "customer.subscription.deleted":
        update.action, update.subscription_id, update.status = "end", obj.get("id"), "canceled"
    elif event_type == "invoice.payment_failed":
        update.action, update.status = "status", "past_due"
    return update


class RazorpayProvider(BillingProvider):
    name = "razorpay"
    title = "Razorpay"
    has_portal = False

    def configured(self) -> bool:
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    def plan_ref(self, plan: str) -> str:
        return razorpay_plan_for(plan)

    async def _call(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured():
            raise BillingError(
                "Billing is not configured on this server (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET).", 503
            )
        return await _request(
            "Razorpay", f"{RAZORPAY_API}{path}", json_body=body,
            auth=httpx.BasicAuth(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        )

    async def checkout(self, db: AsyncSession, org: Organization, plan: str, email: str) -> str:
        plan_id = razorpay_plan_for(plan)
        if not plan_id:
            raise BillingError(f"The {plan} plan has no Razorpay plan configured.", 503)
        if self.can_cancel(org):
            raise BillingError(
                "This organization already has an active subscription. Cancel it first, then choose the new plan.",
                409,
            )
        subscription = await self._call(
            "/subscriptions",
            {
                "plan_id": plan_id,
                "total_count": settings.RAZORPAY_TOTAL_COUNT,
                "quantity": 1,
                "customer_notify": 1,
                "notes": {"org_id": str(org.id), "plan": plan},
            },
        )
        url = str(subscription.get("short_url") or "")
        if not url.startswith("https://"):
            logger.error("Razorpay subscription without a short_url", extra={"subscription": subscription.get("id")})
            raise BillingError("Razorpay did not return a payment link. The server log has the details.", 502)
        return url

    async def cancel(self, org: Organization) -> None:
        sub = self._subscription_to_cancel(org)
        await self._call(f"/subscriptions/{sub}/cancel", {"cancel_at_cycle_end": 1})

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> BillingUpdate:
        verify_razorpay_signature(payload, _header(headers, "X-Razorpay-Signature"), settings.RAZORPAY_WEBHOOK_SECRET)
        return razorpay_update(parse_event(payload), payload, _header(headers, "X-Razorpay-Event-Id"))


def verify_razorpay_signature(payload: bytes, signature: str, secret: str) -> None:
    """Razorpay's scheme: X-Razorpay-Signature is the hex HMAC-SHA256 of the"""
    if not secret:
        raise SignatureError("RAZORPAY_WEBHOOK_SECRET is not set.")
    candidate = (signature or "").strip()
    if not candidate:
        raise SignatureError("Missing X-Razorpay-Signature header.")
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), candidate.encode("utf-8", "replace")):
        raise SignatureError("The signature does not match.")


def razorpay_sign(payload: bytes, secret: str) -> str:
    """The X-Razorpay-Signature Razorpay would send -- for tests and tooling."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


_RAZORPAY_ACTIONS = {
    "subscription.activated": "sync",
    "subscription.charged": "sync",
    "subscription.resumed": "sync",
    "subscription.halted": "sync",  # status "halted" -> back to free
    "subscription.pending": "status",
    "subscription.cancelled": "end",
    "subscription.completed": "end",
}


def razorpay_update(event: Dict[str, Any], payload: bytes, event_id_header: str = "") -> BillingUpdate:
    """Translate one Razorpay event into a BillingUpdate."""
    event_type = str(event.get("event") or "")
    container = (event.get("payload") or {}).get("subscription") if isinstance(event.get("payload"), dict) else None
    sub = (container or {}).get("entity") if isinstance(container, dict) else None
    if not isinstance(sub, dict):
        sub = {}
    notes = sub.get("notes") if isinstance(sub.get("notes"), dict) else {}
    raw_id = (event_id_header or "").strip()[:200] or "sha256-" + hashlib.sha256(payload).hexdigest()
    status = sub.get("status")
    action = _RAZORPAY_ACTIONS.get(event_type, "ignore")
    if action == "end":
        status = "completed" if event_type == "subscription.completed" else "cancelled"
    return BillingUpdate(
        provider="razorpay",
        event_id=f"razorpay:{raw_id}",
        event_type=event_type,
        action=action if sub.get("id") else "ignore",
        org_id=notes.get("org_id"),
        subscription_id=sub.get("id"),
        plan=plan_for_razorpay_plan(sub.get("plan_id")) or notes.get("plan"),
        status=status,
        period_end=_epoch(sub.get("current_end")),
    )


PROVIDERS: Dict[str, BillingProvider] = {"stripe": StripeProvider(), "razorpay": RazorpayProvider()}


def active_provider() -> BillingProvider:
    """The provider NEW subscriptions go through (BILLING_PROVIDER)."""
    return PROVIDERS.get(settings.BILLING_PROVIDER) or PROVIDERS["stripe"]


def provider_for(org: Organization) -> BillingProvider:
    """The provider holding this organization's subscription, else the active one."""
    return PROVIDERS.get(_holder(org) or "") or active_provider()


def webhook_provider(headers: Mapping[str, str]) -> BillingProvider:
    """Which provider signed this request -- by its signature header."""
    if _header(headers, "X-Razorpay-Signature"):
        return PROVIDERS["razorpay"]
    if _header(headers, "Stripe-Signature"):
        return PROVIDERS["stripe"]
    return active_provider()  # unsigned: its own check refuses it


def plans_view() -> list:
    provider = active_provider()
    return [
        {
            "id": plan,
            "price_label": settings.BILLING_PRICE_LABELS.get(plan, ""),
            "limits": plan_limits(plan),
            "purchasable": provider.purchasable(plan),
        }
        for plan in PLANS
    ]


def status_view(org: Organization) -> Dict[str, Any]:
    plan = org.plan or "free"
    renews = org.plan_renews_at
    if renews is not None and renews.tzinfo is None:
        renews = renews.replace(tzinfo=datetime.timezone.utc)
    holder = provider_for(org)
    return {
        "plan": plan,
        "provider": active_provider().name,
        "subscription_status": org.subscription_status,
        "renews_at": renews,
        "has_customer": bool(org.stripe_customer_id or (org.billing_provider and org.stripe_subscription_id)),
        "can_manage": holder.has_portal and _holder(org) == holder.name and bool(org.stripe_customer_id),
        "can_cancel": (not holder.has_portal) and holder.can_cancel(org),
        "limits": {
            "monthly_tokens": org.monthly_token_quota if org.monthly_token_quota is not None else settings.QUOTA_MONTHLY_TOKENS,
            "max_documents": org.max_documents if org.max_documents is not None else settings.QUOTA_MAX_DOCUMENTS,
            "max_storage_bytes": org.max_storage_bytes if org.max_storage_bytes is not None else settings.QUOTA_MAX_STORAGE_BYTES,
        },
        "plans": plans_view(),
    }


async def create_checkout(db: AsyncSession, org: Organization, plan: str, email: str) -> str:
    return await active_provider().checkout(db, org, plan, email)


async def create_portal(org: Organization) -> str:
    return await provider_for(org).portal(org)


async def cancel_subscription(db: AsyncSession, org: Organization) -> None:
    """Cancel at the end of the paid period. The plan stays until the"""
    await provider_for(org).cancel(org)
    org.subscription_status = CANCELLING
    await db.commit()


async def _find_org(db: AsyncSession, update: BillingUpdate) -> Optional[Organization]:
    if update.org_id:
        try:
            org = await db.get(Organization, uuid.UUID(str(update.org_id)))
        except ValueError:
            org = None
        if org is not None:
            return org
    if update.customer_id and update.provider == "stripe":
        org = (
            await db.execute(select(Organization).where(Organization.stripe_customer_id == update.customer_id))
        ).scalars().first()
        if org is not None:
            return org
    if update.subscription_id:
        return (
            await db.execute(
                select(Organization).where(Organization.stripe_subscription_id == update.subscription_id)
            )
        ).scalars().first()
    return None


async def apply_update(db: AsyncSession, update: BillingUpdate) -> str:
    """Apply one verified update. Returns "applied", "duplicate" or "ignored"."""
    event_id = update.event_id[:255]
    if not event_id:
        return "ignored"
    if await db.get(BillingEvent, event_id) is not None:
        return "duplicate"

    org = await _find_org(db, update)
    outcome = "ignored"
    if org is not None and update.action != "ignore":
        before = org.plan or "free"
        stale = bool(
            update.subscription_id and org.stripe_subscription_id == update.subscription_id
            and (org.subscription_status or "") in ENDED
        )
        if update.action in ("link", "sync") and not stale:
            org.billing_provider = update.provider
            if update.customer_id and update.provider == "stripe":
                org.stripe_customer_id = update.customer_id
            if update.subscription_id:
                org.stripe_subscription_id = update.subscription_id
        if update.action == "sync" and not stale:
            org.subscription_status = update.status
            if update.period_end is not None:
                org.plan_renews_at = update.period_end
            keep = update.status in KEEPS_PLAN and update.plan in PAID_PLANS
            apply_plan(org, update.plan if keep and update.plan else "free")
        elif update.action == "status" and not stale:
            org.subscription_status = update.status
        elif update.action == "end":
            if not org.stripe_subscription_id or org.stripe_subscription_id == update.subscription_id:
                apply_plan(org, "free")
                org.subscription_status = update.status
                org.plan_renews_at = None
        outcome = "applied"

        if (org.plan or "free") != before:
            await audit.record(
                db, "billing.plan_change", org_id=org.id,
                details={"from": before, "to": org.plan, "provider": update.provider,
                         "event": event_id, "event_type": update.event_type},
            )
    db.add(BillingEvent(id=event_id, event_type=update.event_type[:128], org_id=org.id if org else None))
    await db.commit()
    return outcome
