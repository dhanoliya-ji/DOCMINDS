"""The checks behind GET /api/v1/health/ready -- "can this instance actually do its job right now?" -- as..."""
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
    """Ask the workers to answer a ping. At least one must reply."""
    from app.core.celery_app import celery_app

    replies = await asyncio.to_thread(
        celery_app.control.ping, timeout=settings.READINESS_TIMEOUT_SECONDS
    )
    if not replies:
        raise RuntimeError("no Celery worker replied")


class StorageCheckFailed(Exception):
    """Carries only the S3 error CODE, which is safe to show publicly."""


async def check_storage() -> None:
    """HEAD the S3 bucket with the configured credentials."""
    from app.services.storage import S3Storage

    def _head() -> None:
        client = S3Storage._make_client()
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET)
        except Exception as exc:
            response = getattr(exc, "response", None) or {}
            code = response.get("Error", {}).get("Code") or type(exc).__name__
            raise StorageCheckFailed(str(code)) from None

    await asyncio.to_thread(_head)


CHECKS: Dict[str, Callable[[], Awaitable[None]]] = {
    "database": check_database,
    "redis": check_redis,
    "celery_worker": check_celery_worker,
    "storage": check_storage,
}


def _plan() -> Dict[str, bool]:
    """Which components to check, and whether each is HARD (see above)."""
    celery = settings.INGESTION_MODE == "celery"
    plan = {"database": True, "redis": celery}
    if celery:
        plan["celery_worker"] = True
    if settings.STORAGE_BACKEND == "s3":
        plan["storage"] = True
    return plan


async def _run(name: str) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(CHECKS[name](), timeout=settings.READINESS_TIMEOUT_SECONDS)
        status = {"status": "ok"}
    except asyncio.TimeoutError:
        status = {"status": "down", "error": "timeout"}
    except StorageCheckFailed as exc:
        status = {"status": "down", "error": str(exc)}
    except Exception as exc:
        status = {"status": "down", "error": type(exc).__name__}
    status["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return name, status


async def readiness() -> Tuple[bool, Dict[str, Any]]:
    """Run every relevant check at once."""
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
