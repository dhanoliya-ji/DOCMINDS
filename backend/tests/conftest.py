import asyncio
import logging
import os
import uuid

import pytest

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


_DB_AVAILABLE: bool | None = None
_DB_SKIP_REASON = ""


def _probe_database() -> tuple[bool, str]:
    """Try to connect and confirm pgvector is installed."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        return False, "asyncpg is not installed"

    from app.core.config import settings

    async def check() -> tuple[bool, str]:
        try:
            connection = await asyncio.wait_for(
                asyncpg.connect(
                    host=settings.POSTGRES_HOST,
                    port=settings.POSTGRES_PORT,
                    user=settings.POSTGRES_USER,
                    password=settings.POSTGRES_PASSWORD,
                    database=settings.POSTGRES_DB,
                ),
                timeout=5.0,
            )
        except Exception as error:
            return False, f"cannot reach Postgres at {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT} ({type(error).__name__})"

        try:
            has_vector = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            if not has_vector:
                return False, "connected, but the pgvector extension is not installed (run: alembic upgrade head)"
            return True, ""
        finally:
            await connection.close()

    try:
        return asyncio.run(check())
    except Exception as error:  # pragma: no cover
        return False, f"database probe failed ({type(error).__name__})"


def database_available() -> tuple[bool, str]:
    """Memoised wrapper around `_probe_database`."""
    global _DB_AVAILABLE, _DB_SKIP_REASON

    if _DB_AVAILABLE is None:
        if os.getenv("SKIP_DB_TESTS"):
            _DB_AVAILABLE, _DB_SKIP_REASON = False, "SKIP_DB_TESTS is set"
        else:
            _DB_AVAILABLE, _DB_SKIP_REASON = _probe_database()

    return _DB_AVAILABLE, _DB_SKIP_REASON


def requires_database(test_item):
    """Mark a test or class as Tier 2."""
    available, reason = database_available()
    return pytest.mark.skipif(
        not available, reason=f"Tier 2: {reason or 'no database'}"
    )(test_item)


@pytest.fixture(scope="session")
def anyio_backend():
    """Run async tests on asyncio (not trio)."""
    return "asyncio"


@pytest.fixture
def use_mock_providers(monkeypatch):
    """Force every external dependency to its `mock` implementation."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "OCR_PROVIDER", "mock")
    return settings


@pytest.fixture
def unique_name():
    """A name no other test will collide with."""
    return lambda prefix="test": f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _rate_limits_off(monkeypatch):
    from app.core import rate_limit
    from app.core.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()
