"""
connectors/scheduler.py
=======================
WHAT THIS FILE DOES
-------------------
Runs the connector sync sweep (service.sync_due) on a timer INSIDE the API
process, for INGESTION_MODE=inline -- the free demo has no Celery beat to
send it. With INGESTION_MODE=celery, beat sends tasks/connectors.py
instead, and this loop is never started.

    start()   from main.py's lifespan, when CONNECTOR_SYNC_ENABLED
    stop()    on shutdown

One loop per API PROCESS. Running several processes is safe -- each link is
claimed with a conditional UPDATE before it is synced, so two loops never
sync the same document -- it just means several processes take turns.

The first sweep waits one minute after startup, so a deploy's health checks
and the ingestion recovery sweep go first.
"""

import asyncio
import contextlib
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None
FIRST_DELAY_SECONDS = 60.0


async def _loop(first_delay: float) -> None:
    from app.services.connectors.service import sync_due

    await asyncio.sleep(first_delay)
    while True:
        try:
            await sync_due()
        except Exception as exc:  # a database blip must not end the loop
            logger.warning("Connector sync sweep failed", extra={"error": repr(exc)})
        await asyncio.sleep(settings.CONNECTOR_SYNC_INTERVAL_MINUTES * 60.0)


def should_run() -> bool:
    return bool(
        settings.CONNECTORS_ENABLED
        and settings.CONNECTOR_SYNC_ENABLED
        and settings.INGESTION_MODE == "inline"
    )


def start(first_delay: float = FIRST_DELAY_SECONDS) -> bool:
    """Start the loop if it should run and is not running. True if started."""
    global _task
    if not should_run() or (_task is not None and not _task.done()):
        return False
    _task = asyncio.get_running_loop().create_task(_loop(first_delay))
    return True


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await _task
    _task = None
