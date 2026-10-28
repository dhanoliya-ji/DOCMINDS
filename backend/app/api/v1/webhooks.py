"""Lets an organization's Admins manage outgoing webhooks."""
import asyncio
import datetime
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.models.webhook import PENDING, WebhookDelivery, WebhookEndpoint
from app.schemas.webhook import (
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryOut,
    WebhookEventType,
    WebhookOut,
    WebhookUpdate,
)
from app.services import audit, webhooks


def _require_enabled() -> None:
    """404 for every route while the feature is switched off."""
    if not settings.WEBHOOKS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(dependencies=[Depends(_require_enabled), Depends(deps.require_admin)])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")


def _check_event_types(event_types: List[str]) -> List[str]:
    """Reject unknown names, so a typo does not silently subscribe to nothing."""
    cleaned = sorted({name.strip() for name in event_types if name.strip()})
    unknown = [
        name for name in cleaned if name != webhooks.WILDCARD and name not in webhooks.EVENT_TYPES
    ]
    if unknown or not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown event type(s): {', '.join(unknown) or '(none given)'}. "
            "See GET /api/v1/webhooks/events.",
        )
    return cleaned


async def _check_url(url: str) -> str:
    try:
        return await asyncio.to_thread(webhooks.validate_target_url, url)
    except webhooks.WebhookURLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


async def _get_endpoint(db: AsyncSession, user: User, webhook_id: uuid.UUID) -> WebhookEndpoint:
    """This organization's webhook, or 404 (also for another org's id)."""
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id, WebhookEndpoint.org_id == user.org_id
        )
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        raise _not_found()
    return endpoint


def _new_ping(endpoint: WebhookEndpoint) -> WebhookDelivery:
    delivery_id = uuid.uuid4()
    now = _utcnow()
    return WebhookDelivery(
        id=delivery_id,
        endpoint_id=endpoint.id,
        org_id=endpoint.org_id,
        event_type="ping",
        payload={
            "id": str(delivery_id),
            "type": "ping",
            "created_at": now.isoformat(),
            "org_id": str(endpoint.org_id),
            "data": {"message": "Hello from DocMinds. Your webhook is set up correctly."},
        },
        status=PENDING,
        attempts=0,
        next_attempt_at=now,
    )


@router.get("/events", response_model=List[WebhookEventType])
async def list_event_types():
    """Every event a webhook can subscribe to ("*" means all of them)."""
    return [{"name": name, "description": text} for name, text in webhooks.EVENT_TYPES.items()]


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Register a URL. The response is the ONLY time the secret is shown."""
    count = await db.scalar(
        select(func.count())
        .select_from(WebhookEndpoint)
        .where(WebhookEndpoint.org_id == current_user.org_id)
    )
    if (count or 0) >= settings.WEBHOOK_MAX_ENDPOINTS_PER_ORG:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An organization may have at most {settings.WEBHOOK_MAX_ENDPOINTS_PER_ORG} webhooks.",
        )

    endpoint = WebhookEndpoint(
        org_id=current_user.org_id,
        url=await _check_url(body.url),
        description=body.description,
        event_types=_check_event_types(body.event_types),
        secret=webhooks.generate_secret(),
        is_active=True,
        created_by=current_user.id,
    )
    db.add(endpoint)
    await db.flush()
    await audit.record(
        db, "webhook.create", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"webhook_id": endpoint.id, "url": endpoint.url, "event_types": endpoint.event_types},
    )
    await db.commit()
    await db.refresh(endpoint)
    return WebhookCreated.model_validate(endpoint, from_attributes=True)


@router.get("", response_model=List[WebhookOut])
async def list_webhooks(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """This organization's webhooks (never their secrets)."""
    result = await db.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.org_id == current_user.org_id)
        .order_by(WebhookEndpoint.created_at)
    )
    return result.scalars().all()


@router.patch("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Change the URL, the events or the description, or pause/resume it."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    if body.url is not None:
        endpoint.url = await _check_url(body.url)
    if body.event_types is not None:
        endpoint.event_types = _check_event_types(body.event_types)
    if body.description is not None:
        endpoint.description = body.description
    if body.is_active is not None:
        endpoint.is_active = body.is_active
    await audit.record(
        db, "webhook.update", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"webhook_id": endpoint.id, **body.model_dump(exclude_none=True)},
    )
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Remove a webhook. Its delivery log goes with it (ON DELETE CASCADE)."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    await audit.record(
        db, "webhook.delete", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"webhook_id": endpoint.id, "url": endpoint.url},
    )
    await db.delete(endpoint)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{webhook_id}/rotate-secret", response_model=WebhookCreated)
async def rotate_secret(
    webhook_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Replace the secret (e.g. after it leaked). The new one is shown once."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    endpoint.secret = webhooks.generate_secret()
    await audit.record(
        db, "webhook.rotate_secret", org_id=current_user.org_id, user_id=current_user.id,
        request=request, details={"webhook_id": endpoint.id},
    )
    await db.commit()
    await db.refresh(endpoint)
    return WebhookCreated.model_validate(endpoint, from_attributes=True)


@router.post(
    "/{webhook_id}/test", response_model=WebhookDeliveryOut, status_code=status.HTTP_202_ACCEPTED
)
async def send_test_event(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Queue a "ping" to this one endpoint, whatever events it subscribes to."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    if not endpoint.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This webhook is paused.")
    delivery = _new_ping(endpoint)
    db.add(delivery)
    webhooks.send_after_commit(db, [delivery.id])
    await db.commit()
    await db.refresh(delivery)
    return delivery


@router.get("/{webhook_id}/deliveries", response_model=List[WebhookDeliveryOut])
async def list_deliveries(
    webhook_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """The newest deliveries first: what was sent, and what came back."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.post(
    "/{webhook_id}/deliveries/{delivery_id}/redeliver",
    response_model=WebhookDeliveryOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def redeliver(
    webhook_id: uuid.UUID,
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Send a delivery again, with a fresh attempt budget."""
    endpoint = await _get_endpoint(db, current_user, webhook_id)
    delivery = await db.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id, WebhookDelivery.endpoint_id == endpoint.id
        )
    )
    if delivery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    delivery.status = PENDING
    delivery.attempts = 0
    delivery.last_error = None
    delivery.next_attempt_at = _utcnow()
    webhooks.send_after_commit(db, [delivery.id])
    await db.commit()
    await db.refresh(delivery)
    return delivery
