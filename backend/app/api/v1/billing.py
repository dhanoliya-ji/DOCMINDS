"""Plans and payment, through Stripe or Razorpay (services/billing.py; BILLING_PROVIDER picks which one new..."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.models import Organization
from app.models import User
from app.schemas import BillingStatus, CheckoutRequest, RedirectOut
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
    """Stop renewing. The plan stays until the end of the paid period, when"""
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
    payload = await request.body()
    provider = billing.webhook_provider(request.headers)
    try:
        update = provider.parse_webhook(payload, request.headers)
    except billing.SignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    outcome = await billing.apply_update(db, update)
    return {"received": True, "outcome": outcome}
