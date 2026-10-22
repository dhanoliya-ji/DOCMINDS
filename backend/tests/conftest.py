"""
conftest.py
===========
WHAT THIS FILE DOES
-------------------
Shared pytest setup. Everything here is available to every test file without
being imported -- that is pytest's convention for a file with this name.

THE TWO TIERS
-------------
Tests in this suite come in two kinds, and the split is deliberate.

    TIER 1  no database, no Redis, no API key, no network.
            Chunking, hashing, tokens, prompt building, schema validation.
            These ALWAYS run. If they fail, something is genuinely broken.

    TIER 2  needs a real PostgreSQL with the pgvector extension.
            Retrieval, the multi-tenant filter, the ownership cascade.
            These SKIP cleanly when no database is reachable.

WHY TIER 2 NEEDS A REAL POSTGRES
--------------------------------
The usual trick of swapping in SQLite does not work here. The queries under
test depend on things only Postgres has:

    - the `vector` column type and the `<=>` distance operator (pgvector)
    - JSONB
    - the HNSW index

A SQLite run would not be a cheaper version of these tests. It would be a
different test that passes while the real query is broken -- which is worse
than no test at all.

SO THEY SKIP RATHER THAN FAIL
-----------------------------
A contributor with no Docker running should see "9 skipped", not nine red
failures they cannot act on. Skipping keeps the signal honest: red means
broken, not "you have not started a container".

    docker compose up -d      # then Tier 2 runs too
"""

import asyncio
import logging
import os
import uuid

import pytest

# ----------------------------------------------------------------------
# Make `app.*` importable no matter which directory pytest was started from.
# ----------------------------------------------------------------------
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ----------------------------------------------------------------------
# Quieten SQLAlchemy's statement logging.
# ----------------------------------------------------------------------
# The engine is created with `echo=True` in development, which prints every
# SQL statement. That is genuinely useful while building a query and actively
# harmful in a test run: a single failure gets buried under hundreds of lines
# of INSERT logging, and the assertion that actually matters scrolls away.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# ======================================================================
# Is a usable database actually there?
# ======================================================================

# Probed ONCE per session rather than per test. The probe involves a TCP
# connection that may have to time out, and paying that on every test would
# add minutes to a run that is supposed to take seconds.
_DB_AVAILABLE: bool | None = None
_DB_SKIP_REASON = ""


def _probe_database() -> tuple[bool, str]:
    """
    Try to connect and confirm pgvector is installed.

    Returns (usable, reason). The reason is shown in the skip message, so a
    contributor can tell "nothing is running" from "running, but pgvector is
    missing" -- two problems with different fixes.
    """
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


# The decorator Tier 2 files apply. Written as a function so the reason string
# is computed lazily -- at collection time the probe has not run yet.
def requires_database(test_item):
    """Mark a test or class as Tier 2."""
    available, reason = database_available()
    return pytest.mark.skipif(
        not available, reason=f"Tier 2: {reason or 'no database'}"
    )(test_item)


# ======================================================================
# Shared fixtures
# ======================================================================


@pytest.fixture(scope="session")
def anyio_backend():
    """Run async tests on asyncio (not trio)."""
    return "asyncio"


@pytest.fixture
def use_mock_providers(monkeypatch):
    """
    Force every external dependency to its `mock` implementation.

    This is what the mock providers were built for. With all three set, the
    full pipeline runs with no API key, no model download and no network --
    and, critically, produces the SAME output every run, so a test can assert
    on it.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "OCR_PROVIDER", "mock")
    return settings


@pytest.fixture
def unique_name():
    """
    A name no other test will collide with.

    Tier 2 tests share one database and may run in any order, so a hard-coded
    "Test Org" turns into a unique-constraint failure the moment two tests use
    it -- or, worse, one test reading another's rows.
    """
    return lambda prefix="test": f"{prefix}-{uuid.uuid4().hex[:12]}"


# ======================================================================
# Rate limits are OFF for every test unless a test switches them on
# ======================================================================
# The whole suite talks to the app from ONE address and a handful of users.
# With the real limits on, the eleventh signup in a run would get a 429 and
# fail a test that has nothing to do with rate limiting. tests/test_rate_limit.py
# turns them back on (with the `rate_limits_on` fixture) where they ARE the
# subject. Counters are kept in memory and wiped between tests, so no test
# depends on a Redis server or on what an earlier test counted.
@pytest.fixture(autouse=True)
def _rate_limits_off(monkeypatch):
    from app.core import rate_limit
    from app.core.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE", "memory")
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()
