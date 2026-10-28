"""deliver_webhook_task      one attempt at one delivery."""
import asyncio

from app.core.celery_app import celery_app
from app.services import webhooks


@celery_app.task(
    bind=True,
    name="app.tasks.webhooks.deliver_webhook_task",
    max_retries=None,
    acks_late=True,
)
def deliver_webhook_task(self, delivery_id: str) -> str:
    """Attempt one delivery; reschedule on a retryable failure."""
    outcome, wait = asyncio.run(webhooks.deliver(delivery_id))
    if outcome == "retry":
        raise self.retry(countdown=wait)
    return outcome


@celery_app.task(name="app.tasks.webhooks.sweep_webhooks_task")
def sweep_webhooks_task() -> int:
    """Re-queue overdue pending deliveries. Returns how many were re-queued."""
    ids = asyncio.run(webhooks.due_delivery_ids())
    for delivery_id in ids:
        deliver_webhook_task.apply_async(args=[delivery_id])
    return len(ids)
