"""
tasks/connectors.py
===================
The Celery side of connector auto-sync (services/connectors/service.py):

    sync_connectors_task   sent by Celery beat every
                           CONNECTOR_SYNC_INTERVAL_MINUTES. Checks the
                           imported documents that are due and re-imports
                           the ones whose source changed; the re-index itself
                           is queued as an ordinary ingestion job.

A no-op unless CONNECTORS_ENABLED and CONNECTOR_SYNC_ENABLED, so beat can
always carry the schedule entry. With INGESTION_MODE=inline the API runs
the same sweep in-process instead (services/connectors/scheduler.py).
"""

import asyncio

from app.core.celery_app import celery_app
from app.core.config import settings


@celery_app.task(name="app.tasks.connectors.sync_connectors_task")
def sync_connectors_task() -> dict:
    if not (settings.CONNECTORS_ENABLED and settings.CONNECTOR_SYNC_ENABLED):
        return {}
    from app.services.connectors.service import sync_due

    return asyncio.run(sync_due())
