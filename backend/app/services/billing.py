"""
billing.py  (service)
=====================
WHAT THIS FILE DOES
-------------------
Plans and Stripe: which plan an organization is on, what that plan allows,
and the three conversations with Stripe that change it.

    plan_limits("pro")              what a plan allows (tokens, documents, bytes)
    apply_plan(org, "pro")          write those limits onto the organization
    create_checkout(...)            a Stripe Checkout page to subscribe
    create_portal(...)              the Stripe Customer Portal (change card,
                                    switch plan, cancel, invoices)
    verify_signature(...)           is this webhook really from Stripe?
    handle_event(db, event)         apply one webhook event, exactly once

PLANS ARE QUOTAS
----------------
There is no separate "entitlements" system. A plan is a set of values for the
three quotas services/usage.py already enforces:

    free   the QUOTA_* settings -- the override columns are cleared to NULL,
           so the organization behaves exactly as it did before billing
    pro    BILLING_PLAN_LIMITS["pro"]   written into the override columns
    team   BILLING_PLAN_LIMITS["team"]

WHERE THE TRUTH LIVES
---------------------
Stripe. Our endpoints never set a plan from what the browser says -- not even
on the Checkout "success" redirect, which anyone can type into the address
bar. A plan changes ONLY when a webhook arrives whose HMAC signature
(STRIPE_WEBHOOK_SECRET) verifies, inside the replay window, and whose event
id has not been applied before (models/billing.py).

NO STRIPE SDK
-------------
Three form-encoded POSTs and one HMAC check do not justify a dependency:
they go through httpx (already pinned) to the fixed host api.stripe.com.
"""

import datetime
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.billing import BillingEvent
from app.models.organization import Organization
from app.services import audit

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
PLANS = ("free", "pro", "team")
PAID_PLANS = ("pro", "team")
# Subscription states that keep the paid plan. past_due keeps it while
# Stripe retries the card (its "smart retries"); the plan drops only once
# Stripe gives up and the subscription becomes canceled or unpaid.
KEEPS_PLAN = ("active", "trialing", "past_due")

# Tests swap in an httpx.MockTransport here; None means the real network.
_transport: Optional[httpx.AsyncBaseTransport] = None


class BillingError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SignatureError(Exception):
    """The webhook's Stripe-Signature does not verify."""


# ======================================================================
# PLANS
# ======================================================================


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
    return {"pro": settings.STRIPE_PRICE_PRO, "team": settings.STRIPE_PRICE_TEAM}.get(plan, "")


def plan_for_price(price_id: Optional[str]) -> Optional[str]:
    for plan in PAID_PLANS:
        if price_id and price_for(plan) == price_id:
            return plan
    return None


def plans_view() -> list:
    return [
        {
            "id": plan,
            "price_label": settings.BILLING_PRICE_LABELS.get(plan, ""),
            "limits": plan_limits(plan),
            "purchasable": plan in PAID_PLANS and bool(price_for(plan) and settings.STRIPE_SECRET_KEY),
        }
        for plan in PLANS
    ]


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


def status_view(org: Organization) -> Dict[str, Any]:
    plan = org.plan or "free"
    renews = org.plan_renews_at
    if renews is not None and renews.tzinfo is None:
        renews = renews.replace(tzinfo=datetime.timezone.utc)
    return {
        "plan": plan,
        "subscription_status": org.subscription_status,
        "renews_at": renews,
        "has_customer": bool(org.stripe_customer_id),
        # What is ENFORCED right now (an operator's manual override included).
        "limits": {
            "monthly_tokens": org.monthly_token_quota if org.monthly_token_quota is not None else settings.QUOTA_MONTHLY_TOKENS,
            "max_documents": org.max_documents if org.max_documents is not None else settings.QUOTA_MAX_DOCUMENTS,
            "max_storage_bytes": org.max_storage_bytes if org.max_storage_bytes is not None else settings.QUOTA_MAX_STORAGE_BYTES,
        },
        "plans": plans_view(),
    }


# ======================================================================
# TALKING TO STRIPE
# ======================================================================


async def _stripe(path: str, data: Dict[str, str], *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    if not settings.STRIPE_SECRET_KEY:
        raise BillingError("Billing is not configured on this server (STRIPE_SECRET_KEY).", 503)
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=_transport, follow_redirects=False) as client:
            response = await client.post(
                f"{STRIPE_API}{path}", data=data, headers=headers,
                auth=httpx.BasicAuth(settings.STRIPE_SECRET_KEY, ""),
            )
    except httpx.HTTPError as exc:
        raise BillingError("Could not reach Stripe. Please try again.", 503) from exc
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        # Stripe's message names the problem ("No such price"); log it in
        # full, show the admin a version without internals.
        logger.error("Stripe request failed", extra={"path": path, "status": response.status_code,
                                                     "stripe_error": (body.get("error") or {}).get("message")})
        raise BillingError("Stripe refused the request. The server log has the details.", 502)
    return body


async def ensure_customer(db: AsyncSession, org: Organization, email: str) -> str:
    if org.stripe_customer_id:
        return org.stripe_customer_id
    customer = await _stripe(
        "/customers",
        {"name": org.name, "email": email, "metadata[org_id]": str(org.id)},
        idempotency_key=f"customer-{org.id}",
    )
    org.stripe_customer_id = customer["id"]
    await db.commit()
    return customer["id"]


async def create_checkout(db: AsyncSession, org: Organization, plan: str, email: str) -> str:
    price = price_for(plan)
    if not price:
        raise BillingError(f"The {plan} plan has no Stripe price configured.", 503)
    customer = await ensure_customer(db, org, email)
    base = settings.frontend_url
    session = await _stripe(
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


async def create_portal(org: Organization) -> str:
    if not org.stripe_customer_id:
        raise BillingError("This organization has no billing account yet. Choose a plan first.", 409)
    session = await _stripe(
        "/billing_portal/sessions",
        {"customer": org.stripe_customer_id, "return_url": f"{settings.frontend_url}/settings/billing"},
    )
    return session["url"]


# ======================================================================
# WEBHOOKS
# ======================================================================


def verify_signature(payload: bytes, header: str, secret: str, *, tolerance: int, now: Optional[float] = None) -> None:
    """
    Stripe's scheme: header "t=<unix time>,v1=<hex>[,v1=...]", where v1 is
    HMAC-SHA256(secret, "<t>.<raw body>"). Raises SignatureError.
    """
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
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureError("The signature does not match.")


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    """The header Stripe would send -- used by the tests and local tooling."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def _find_org(db: AsyncSession, obj: Dict[str, Any]) -> Optional[Organization]:
    org_id = (obj.get("metadata") or {}).get("org_id") or obj.get("client_reference_id")
    if org_id:
        try:
            org = await db.get(Organization, uuid.UUID(str(org_id)))
        except ValueError:
            org = None
        if org is not None:
            return org
    customer = obj.get("customer")
    if customer:
        return (
            await db.execute(select(Organization).where(Organization.stripe_customer_id == customer))
        ).scalars().first()
    return None


def _period_end(sub: Dict[str, Any]) -> Optional[datetime.datetime]:
    end = sub.get("current_period_end")
    if end is None:
        # Newer API versions moved it onto each subscription item.
        items = ((sub.get("items") or {}).get("data") or [{}])
        end = items[0].get("current_period_end") if items else None
    return datetime.datetime.fromtimestamp(int(end), tz=datetime.timezone.utc) if end else None


def _subscription_price(sub: Dict[str, Any]) -> Optional[str]:
    items = (sub.get("items") or {}).get("data") or []
    return ((items[0].get("price") or {}).get("id")) if items else None


async def handle_event(db: AsyncSession, event: Dict[str, Any]) -> str:
    """
    Apply one verified event. Returns "applied", "duplicate" or "ignored".
    Commits. The event id is recorded in the SAME transaction as the change.
    """
    event_id = event.get("id")
    event_type = event.get("type") or ""
    if not event_id:
        return "ignored"
    if await db.get(BillingEvent, event_id) is not None:
        return "duplicate"

    obj = ((event.get("data") or {}).get("object")) or {}
    org = await _find_org(db, obj)
    outcome = "ignored"
    if org is not None:
        before = org.plan or "free"
        if event_type == "checkout.session.completed":
            if obj.get("customer"):
                org.stripe_customer_id = obj["customer"]
            if obj.get("subscription"):
                org.stripe_subscription_id = obj["subscription"]
            plan = (obj.get("metadata") or {}).get("plan")
            if plan in PAID_PLANS and obj.get("payment_status") in (None, "paid", "no_payment_required"):
                apply_plan(org, plan)
                org.subscription_status = "active"
            outcome = "applied"
        elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
            status = obj.get("status")
            plan = plan_for_price(_subscription_price(obj)) or (obj.get("metadata") or {}).get("plan")
            org.stripe_subscription_id = obj.get("id") or org.stripe_subscription_id
            if obj.get("customer"):
                org.stripe_customer_id = obj["customer"]
            org.subscription_status = status
            org.plan_renews_at = _period_end(obj)
            apply_plan(org, plan if status in KEEPS_PLAN and plan in PAID_PLANS else "free")
            outcome = "applied"
        elif event_type == "customer.subscription.deleted":
            # Only the subscription we know about can end the plan: a stale
            # event for an older, replaced subscription must not.
            if not org.stripe_subscription_id or org.stripe_subscription_id == obj.get("id"):
                apply_plan(org, "free")
                org.subscription_status = "canceled"
                org.stripe_subscription_id = None
                org.plan_renews_at = None
            outcome = "applied"
        elif event_type == "invoice.payment_failed":
            org.subscription_status = "past_due"
            outcome = "applied"

        if (org.plan or "free") != before:
            await audit.record(
                db, "billing.plan_change", org_id=org.id,
                details={"from": before, "to": org.plan, "stripe_event": event_id, "event_type": event_type},
            )
    db.add(BillingEvent(id=event_id, event_type=event_type[:128], org_id=org.id if org else None))
    await db.commit()
    return outcome


def parse_event(payload: bytes) -> Dict[str, Any]:
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise SignatureError("The body is not JSON.") from exc
    if not isinstance(event, dict):
        raise SignatureError("The body is not a Stripe event.")
    return event
