"""
sqlite_harness.py
=================
WHAT THIS FILE DOES
-------------------
Builds a throwaway in-memory database from the models, for tests that exercise
plain row bookkeeping -- statuses, timestamps, counters -- rather than pgvector.

conftest.py explains why retrieval tests REFUSE SQLite: they test the `<=>`
operator and the HNSW index, which SQLite does not have. The ingestion
durability rules (the claim, retries, recovery) and the upload endpoint's
validation are not like that. They are ordinary UPDATEs and INSERTs, which
SQLite runs faithfully, so they are checked on every run with no Docker.

The tests that use this ALSO run against Postgres when one is reachable (CI),
which is what proves the Alembic migration matches the models.

Two stand-ins make the models build on SQLite:
    JSONB    -> rendered as SQLite's JSON
    Vector   -> pgvector's type already renders as "VECTOR(n)", which SQLite
                accepts as a column of no particular type; the vector arrives
                as its text form "[0.1, 0.2, ...]" and is stored as given.
"""

from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models import Base


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element, compiler, **kw):
    return "JSON"


async def make_sqlite_factory():
    """Return (engine, session_factory) for a fresh in-memory database."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        # One shared connection: an in-memory SQLite database exists only
        # inside the connection that created it.
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        # SQLite ignores ON DELETE CASCADE unless asked.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory
