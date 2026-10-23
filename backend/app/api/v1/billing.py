"""
billing.py
==========
WHAT THIS FILE DOES
-------------------
Plans and payment, through Stripe or Razorpay (services/billing.py;
BILLING_PROVIDER picks which one new subscriptions use).

    GET  /billing              the organization's plan, its limits, the
                               plans on offer and the provider (any member)
    POST /billing/checkout     -> the provider's payment page for pro or
                               team: Stripe Checkout, or a Razorpay
                               subscription link                    (admin)
    POST /billing/portal       -> the Stripe Customer Portal URL     (admin)
    POST /billing/cancel       cancel at the end of the paid period  (admin)
    POST /billing/webhook      provider -> us: plan changes (signed, no login)

Every route answers 404 while BILLING_ENABLED is off.

THE WEBHOOK IS THE ONLY WRITER
------------------------------
Checkout and the portal only hand out provider URLs, and cancel only asks
the provider to stop renewing. Whatever the customer does reaches us as a
webhook, verified (Stripe-Signature against STRIPE_WEBHOOK_SECRET, or
X-Razorpay-Signature against RAZORPAY_WEBHOOK_SECRET) before its body is
even parsed; an unsigned, wrongly signed or stale request is refused with
400 and changes nothing. That is why the webhook needs no login -- the
signature IS its authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.models.organization import Organization
from app.models.user import User
from app.schemas.integrations import BillingStatus, CheckoutRequest, RedirectOut
from app.services import audit, billing


def _require_enabled() -> None:
    if not settings.BILLING_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(dependencies=[Depends(_require_enabled)])


async def _org(db: AsyncSession, user: User) -> Organization:
    org = await db.get(Organization, user.org_id)
    if org is None:  # pragma: no cover - a user always has an organization
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return org


def _error(exc: billing.BillingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("", response_model=BillingStatus)
async def get_billing(
    db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_viewer)
):
    """The plan badge and the upgrade page read this. Every member may see the plan."""
    return billing.status_view(await _org(db, current_user))


@router.post("/checkout", response_model=RedirectOut)
async def checkout(
    payload: CheckoutRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
):
    org = await _org(db, current_user)
    try:
        url = await billing.create_checkout(db, org, payload.plan, current_user.email)
    except billing.BillingError as exc:
        raise _error(exc) from exc
    await audit.record(
        db, "billing.checkout", org_id=org.id, user_id=current_user.id, request=request,
        details={"plan": payload.plan, "provider": billing.active_provider().name},
    )
    await db.commit()
    return {"url": url}


@router.post("/portal", response_model=RedirectOut)
async def portal(
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
):
    org = await _org(db, current_user)
    try:
        url = await billing.create_portal(org)
    except billing.BillingError as exc:
        raise _error(exc) from exc
    await audit.record(db, "billing.portal", org_id=org.id, user_id=current_user.id, request=request)
    await db.commit()
    return {"url": url}


@router.post("/cancel", response_model=BillingStatus)
async def cancel(
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
):
    """
    Stop renewing. The plan stays until the end of the paid period, when
    the provider's signed webhook moves the organization back to free.
    """
    org = await _org(db, current_user)
    try:
        await billing.cancel_subscription(db, org)
    except billing.BillingError as exc:
        raise _error(exc) from exc
    await audit.record(
        db, "billing.cancel", org_id=org.id, user_id=current_user.id, request=request,
        details={"provider": billing.provider_for(org).name},
    )
    await db.commit()
    await db.refresh(org)
    return billing.status_view(org)


@router.post("/webhook", include_in_schema=False)
async def billing_webhook(request: Request, db: AsyncSession = Depends(deps.get_db)):
    # The RAW bytes: the signature covers them exactly, so the body must not
    # be parsed and re-serialised before checking.
    payload = await request.body()
    provider = billing.webhook_provider(request.headers)
    try:
        update = provider.parse_webhook(payload, request.headers)
    except billing.SignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    outcome = await billing.apply_update(db, update)
    return {"received": True, "outcome": outcome}
