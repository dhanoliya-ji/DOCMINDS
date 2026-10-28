"""sync_connectors_task   sent by Celery beat every CONNECTOR_SYNC_INTERVAL_MINUTES."""
import asyncio

from app.core.celery_app import celery_app
from app.core.config import settings
from app.tasks.ingestion import InlineQueue


async def _run_import(import_id: str, *, force: bool = False) -> str:
    from app.services.connectors.service import run_import

    return await run_import(import_id)


inline_import_queue = InlineQueue(runner=_run_import, name="connector-import", concurrency=1)


@celery_app.task(
    bind=True,
    name="app.tasks.connectors.run_import_task",
    acks_late=True,
    max_retries=3,
)
def run_import_task(self, import_id: str) -> str:
    """Run one import job. "busy" (another worker has it, e.g. a redelivered"""
    outcome = asyncio.run(_run_import(import_id))
    if outcome == "busy":
        from app.services.connectors.service import import_finished

        if not asyncio.run(import_finished(import_id)):
            raise self.retry(countdown=settings.CONNECTOR_IMPORT_STALE_MINUTES * 60 + 30)
    return outcome


@celery_app.task(name="app.tasks.connectors.sync_connectors_task")
def sync_connectors_task() -> dict:
    if not settings.CONNECTORS_ENABLED:
        return {}
    from app.services.connectors.service import resume_imports, sync_due

    resumed = asyncio.run(resume_imports())
    if not settings.CONNECTOR_SYNC_ENABLED:
        return {"imports_resumed": resumed} if resumed else {}
    counts = asyncio.run(sync_due())
    if resumed:
        counts["imports_resumed"] = resumed
    return counts
