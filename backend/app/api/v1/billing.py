"""
billing.py
==========
WHAT THIS FILE DOES
-------------------
Plans and payment, through Stripe (services/billing.py).

    GET  /billing              the organization's plan, its limits and the
                               plans on offer          (any member)
    POST /billing/checkout     -> a Stripe Checkout URL for pro or team (admin)
    POST /billing/portal       -> the Stripe Customer Portal URL       (admin)
    POST /billing/webhook      Stripe -> us: plan changes  (signed, no login)

Every route answers 404 while BILLING_ENABLED is off.

THE WEBHOOK IS THE ONLY WRITER
------------------------------
Checkout and the portal only hand out Stripe URLs. Whatever the customer
does there reaches us as a webhook, verified against STRIPE_WEBHOOK_SECRET
before its body is even parsed; an unsigned, wrongly signed or stale request
is refused with 400 and changes nothing. That is why the webhook needs no
login -- the signature IS its authentication.
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
        details={"plan": payload.plan},
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


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(deps.get_db)):
    # The RAW bytes: the signature covers them exactly, so the body must not
    # be parsed and re-serialised before checking.
    payload = await request.body()
    try:
        billing.verify_signature(
            payload,
            request.headers.get("Stripe-Signature", ""),
            settings.STRIPE_WEBHOOK_SECRET,
            tolerance=settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS,
        )
        event = billing.parse_event(payload)
    except billing.SignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    outcome = await billing.handle_event(db, event)
    return {"received": True, "outcome": outcome}
