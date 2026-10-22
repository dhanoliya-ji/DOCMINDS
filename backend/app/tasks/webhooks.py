"""
tasks/webhooks.py
=================
WHAT THIS FILE DOES
-------------------
The two Celery tasks that SEND webhooks (services/webhooks.py builds them):

    deliver_webhook_task      one attempt at one delivery. On failure it
                              re-schedules ITSELF with a growing wait (30s,
                              60s, 120s... see WEBHOOK_RETRY_BACKOFF_SECONDS)
                              until WEBHOOK_MAX_ATTEMPTS is reached.
    sweep_webhooks_task       run by Celery beat every few minutes: re-queues
                              any pending delivery whose message was lost
                              (Redis flushed, queue down when the event fired).

WHY THE ATTEMPT COUNT LIVES IN THE DATABASE, NOT IN CELERY
----------------------------------------------------------
Celery counts its own retries, but that count dies with the message. The
delivery row's `attempts` column survives a lost message, a re-queue by the
sweep and a worker restart, so the limit is enforced exactly once, there.
"""

import asyncio

from app.core.celery_app import celery_app
from app.services import webhooks


@celery_app.task(
    bind=True,
    name="app.tasks.webhooks.deliver_webhook_task",
    # Retries are driven by the outcome below, not by exceptions; this only
    # stops Celery's own counter from ending them early.
    max_retries=None,
    acks_late=True,
)
def deliver_webhook_task(self, delivery_id: str) -> str:
    """Attempt one delivery; reschedule on a retryable failure."""
    outcome, wait = asyncio.run(webhooks.deliver(delivery_id))
    if outcome == "retry":
        # `self.retry` re-queues THIS task with a countdown and ends the
        # current run. It raises Retry on purpose -- that is how Celery knows.
        raise self.retry(countdown=wait)
    return outcome


@celery_app.task(name="app.tasks.webhooks.sweep_webhooks_task")
def sweep_webhooks_task() -> int:
    """Re-queue overdue pending deliveries. Returns how many were re-queued."""
    ids = asyncio.run(webhooks.due_delivery_ids())
    for delivery_id in ids:
        deliver_webhook_task.apply_async(args=[delivery_id])
    return len(ids)
