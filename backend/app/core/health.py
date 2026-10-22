"""
health.py
=========
WHAT THIS FILE DOES
-------------------
The checks behind GET /api/v1/health/ready -- "can this instance actually do
its job right now?" -- as opposed to GET /api/v1/health, which only says "the
process is up".

WHY TWO HEALTH ENDPOINTS
------------------------
They answer different questions, and hosting platforms ask both:

    /health         LIVENESS. Is the process alive and answering? Must be
                    instant and must NOT touch the database: if it did, a
                    database outage would make the platform restart every API
                    instance in a loop -- which fixes nothing and makes the
                    outage worse.
    /health/ready   READINESS. Should traffic be sent here? Checks every
                    dependency, and answers 503 when a hard one is down, so a
                    load balancer routes around this instance (or a deploy
                    waits) until it recovers.

WHAT IS CHECKED, AND WHAT COUNTS AS "HARD"
------------------------------------------
    database       always hard. Nothing works without it.
    redis          hard only with INGESTION_MODE=celery, where it is the job
                   queue. In inline mode the only user is the rate limiter,
                   which falls back to in-memory counters -- so Redis being
                   down is reported, but the instance is still ready.
    celery worker  hard only with INGESTION_MODE=celery: uploads would sit
                   "pending" for ever with no worker to process them. Not
                   checked at all in inline mode, where there is no worker.

Each check has its own timeout (READINESS_TIMEOUT_SECONDS) and they run
CONCURRENTLY, so the endpoint answers within one timeout even when everything
is down -- a readiness probe that itself hangs is worse than useless.

The response never contains connection strings or exception messages (which
can include hostnames and usernames) -- only the exception's type.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

from sqlalchemy import text

from app.core.config import settings


async def check_database() -> None:
    """One trivial query through the app's own connection pool."""
    from app.db.session import engine

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis() -> None:
    """PING the Redis server the queue and the rate limiter use."""
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=settings.READINESS_TIMEOUT_SECONDS,
        socket_timeout=settings.READINESS_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_celery_worker() -> None:
    """
    Ask the workers to answer a ping. At least one must reply.

    `control.ping` is a blocking call (it waits on Redis for replies), so it
    runs on a thread -- never on the event loop.
    """
    from app.core.celery_app import celery_app

    replies = await asyncio.to_thread(
        celery_app.control.ping, timeout=settings.READINESS_TIMEOUT_SECONDS
    )
    if not replies:
        raise RuntimeError("no Celery worker replied")


# Indirection so the tests can swap a check for a fake one.
CHECKS: Dict[str, Callable[[], Awaitable[None]]] = {
    "database": check_database,
    "redis": check_redis,
    "celery_worker": check_celery_worker,
}


def _plan() -> Dict[str, bool]:
    """Which components to check, and whether each is HARD (see above)."""
    celery = settings.INGESTION_MODE == "celery"
    plan = {"database": True, "redis": celery}
    if celery:
        plan["celery_worker"] = True
    return plan


async def _run(name: str) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    try:
        # The timeout wraps the WHOLE check, connect included.
        await asyncio.wait_for(CHECKS[name](), timeout=settings.READINESS_TIMEOUT_SECONDS)
        status = {"status": "ok"}
    except asyncio.TimeoutError:
        status = {"status": "down", "error": "timeout"}
    except Exception as exc:
        status = {"status": "down", "error": type(exc).__name__}
    status["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return name, status


async def readiness() -> Tuple[bool, Dict[str, Any]]:
    """
    Run every relevant check at once.

    Returns (ready, body). `ready` is False when any HARD component is down;
    the endpoint answers 503 then.
    """
    plan = _plan()
    results = dict(await asyncio.gather(*(_run(name) for name in plan)))
    for name, hard in plan.items():
        results[name]["required"] = hard
    ready = all(results[name]["status"] == "ok" for name, hard in plan.items() if hard)
    return ready, {
        "status": "ready" if ready else "unavailable",
        "ingestion_mode": settings.INGESTION_MODE,
        "components": results,
    }
