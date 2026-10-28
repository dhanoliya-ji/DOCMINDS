import datetime
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import Chunk
from app.models import Document
from app.models import Organization
from app.models import Project
from app.services import malware, storage as storage_module
from app.services.embedding import EmbeddingService
from app.services.storage import get_storage, new_key
from app.tasks import ingestion
from app.tasks.ingestion import (
    MalwareDetected,
    TransientIngestionError,
    async_process_document,
    claim_document,
    describe_failure,
    is_transient,
    process_document_task,
    recover_stuck_documents,
    retry_delay_seconds,
    run_inline_ingestion,
)
from tests.conftest import database_available
from tests.sqlite_harness import make_sqlite_factory

TEXT = "Refunds are available within thirty days of purchase. " * 40


class OperationalError(Exception):
    """Named like SQLAlchemy's, which is how it is recognised."""


class RateLimitError(Exception):
    """Named like openai's."""


class TestClassification:
    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionError("reset"),
            TimeoutError("slow"),
            OperationalError("server closed the connection"),
            RateLimitError("429"),
            storage_module.StorageUnavailableError("s3 down"),
            malware.ScannerUnavailableError("clamd down"),
        ],
    )
    def test_transient(self, exc):
        assert is_transient(exc)

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("No text could be extracted"),
            MalwareDetected("Eicar"),
            storage_module.StorageObjectNotFound("k"),
            KeyError("x"),
            RuntimeError("anything unrecognised"),
        ],
    )
    def test_permanent(self, exc):
        assert not is_transient(exc)

    def test_backoff_grows_and_is_capped(self, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_RETRY_BACKOFF_SECONDS", 2)
        monkeypatch.setattr(settings, "INGESTION_RETRY_BACKOFF_MAX_SECONDS", 10)
        assert [retry_delay_seconds(n) for n in range(5)] == [2, 4, 8, 10, 10]

    def test_the_default_retries_outlast_a_short_outage(self):
        """The point of retrying is to ride out an outage: a rate limit that"""
        total = sum(retry_delay_seconds(n) for n in range(settings.INGESTION_MAX_RETRIES))
        assert total >= 60

    def test_messages_are_for_people(self):
        from celery.exceptions import SoftTimeLimitExceeded

        assert "longer than" in describe_failure(SoftTimeLimitExceeded())
        assert "missing from storage" in describe_failure(storage_module.StorageObjectNotFound("k"))
        assert len(describe_failure(ValueError("x" * 5000))) <= 1000


class TestCeleryConfiguration:
    def test_the_task_survives_a_dead_worker(self):
        assert process_document_task.acks_late is True
        assert process_document_task.reject_on_worker_lost is True

    def test_it_retries_only_transient_failures(self):
        assert process_document_task.autoretry_for == (TransientIngestionError,)
        assert process_document_task.max_retries == settings.INGESTION_MAX_RETRIES

    def test_celery_waits_as_long_as_inline_mode_does(self):
        """Both use base x 2^retries, so the two modes behave alike."""
        assert process_document_task.retry_backoff == settings.INGESTION_RETRY_BACKOFF_SECONDS
        assert process_document_task.retry_backoff_max == settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS
        assert process_document_task.retry_jitter is True

    def test_it_has_time_limits(self):
        assert process_document_task.soft_time_limit == settings.INGESTION_SOFT_TIME_LIMIT_SECONDS
        assert process_document_task.time_limit == settings.INGESTION_TIME_LIMIT_SECONDS
        assert settings.INGESTION_SOFT_TIME_LIMIT_SECONDS < settings.INGESTION_TIME_LIMIT_SECONDS

    def test_stale_means_longer_than_any_live_job(self):
        assert settings.INGESTION_STALE_AFTER_MINUTES * 60 > settings.INGESTION_TIME_LIMIT_SECONDS

    def test_beat_runs_the_recovery_sweep(self):
        entry = celery_app.conf.beat_schedule["recover-stuck-documents"]
        assert entry["task"] == "app.tasks.ingestion.recover_stuck_documents_task"
        assert entry["task"] in celery_app.tasks

    def test_one_message_at_a_time(self):
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_redis_does_not_redeliver_a_job_that_is_merely_slow(self):
        """With acks_late, Redis re-delivers any message still unacknowledged"""
        timeout = celery_app.conf.broker_transport_options["visibility_timeout"]
        assert timeout >= 3600
        assert timeout > (
            settings.INGESTION_TIME_LIMIT_SECONDS + settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS
        )


def _backends():
    available, reason = database_available()
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(not available, reason=f"Tier 2: {reason or 'no database'}"),
        ),
    ]


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    """LocalStorage rooted in tmp_path, as the configured default."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path / "store"))
    storage_module.reset_storage_cache()
    yield get_storage()
    storage_module.reset_storage_cache()


@pytest_asyncio.fixture(params=_backends())
async def db(request, monkeypatch, use_mock_providers, local_store):
    """A session factory the pipeline is pointed at, plus cleanup."""
    created_orgs: list[uuid.UUID] = []
    engine = None
    if request.param == "sqlite":
        engine, factory = await make_sqlite_factory()
    else:
        from app.db.session import SessionLocal as factory  # noqa: N813

    monkeypatch.setattr(ingestion, "SessionLocal", factory)
    dispatched: list[str] = []
    monkeypatch.setattr(ingestion, "dispatch", lambda document_id: dispatched.append(document_id))

    yield Harness(factory, created_orgs, dispatched)

    if engine is not None:
        await engine.dispose()
    else:
        async with factory() as session:
            for org_id in created_orgs:
                await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.commit()


class Harness:
    def __init__(self, factory, created_orgs, dispatched):
        self.factory = factory
        self.created_orgs = created_orgs
        self.dispatched = dispatched

    async def document(self, content: bytes = TEXT.encode(), extension: str = "txt", **fields) -> uuid.UUID:
        """An org, a project and a stored document; returns the document id."""
        async with self.factory() as session:
            org = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
            session.add(org)
            await session.flush()
            self.created_orgs.append(org.id)
            project = Project(name="Project", org_id=org.id)
            session.add(project)
            await session.flush()

            key = new_key(project.id, extension)
            path = get_storage().path_for(key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(content)

            document = Document(
                filename=f"file.{extension}",
                file_type=extension,
                file_size=len(content),
                file_path=key,
                storage_backend="local",
                status=fields.pop("status", "pending"),
                duplicate_hash=uuid.uuid4().hex,
                project_id=project.id,
                meta_data={},
                **fields,
            )
            session.add(document)
            await session.commit()
            return document.id

    async def get(self, document_id) -> Document:
        async with self.factory() as session:
            return await session.get(Document, document_id)

    async def chunk_count(self, document_id) -> int:
        async with self.factory() as session:
            result = await session.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
            return result.scalar_one()

    async def set(self, document_id, **values) -> None:
        async with self.factory() as session:
            await session.execute(update(Document).where(Document.id == document_id).values(**values))
            await session.commit()


def minutes_ago(minutes: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)


class TestClaim:
    async def test_a_pending_document_is_claimed_once(self, db):
        document_id = await db.document()

        async with db.factory() as session:
            first = await claim_document(session, document_id)
        async with db.factory() as session:
            second = await claim_document(session, document_id)

        assert first is not None and first.status == "processing"
        assert first.attempts == 1 and first.processing_started_at is not None
        assert second is None

    async def test_a_stale_claim_can_be_taken_over(self, db):
        document_id = await db.document(status="processing", attempts=1)
        await db.set(document_id, processing_started_at=minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5))

        async with db.factory() as session:
            claimed = await claim_document(session, document_id)

        assert claimed is not None and claimed.attempts == 2

    async def test_a_fresh_claim_is_left_alone(self, db):
        document_id = await db.document(status="processing", attempts=1)
        await db.set(document_id, processing_started_at=minutes_ago(1))

        async with db.factory() as session:
            assert await claim_document(session, document_id) is None

    async def test_completed_needs_force(self, db):
        document_id = await db.document(status="completed")
        async with db.factory() as session:
            assert await claim_document(session, document_id) is None
        async with db.factory() as session:
            assert await claim_document(session, document_id, force=True) is not None

    async def test_a_duplicate_delivery_skips(self, db):
        document_id = await db.document()
        assert await async_process_document(str(document_id)) == "completed"
        assert await async_process_document(str(document_id)) == "skipped"
        assert (await db.get(document_id)).attempts == 1


class TestOutcomes:
    async def test_success(self, db):
        document_id = await db.document()

        assert await async_process_document(str(document_id)) == "completed"

        document = await db.get(document_id)
        assert document.status == "completed"
        assert document.error_message is None
        assert document.meta_data["malware_scan"] == "off"
        assert await db.chunk_count(document_id) > 0

    async def test_a_permanent_failure_is_not_retried(self, db):
        document_id = await db.document(content=b"   \n  ")

        result = await async_process_document(str(document_id), final_attempt=False)

        document = await db.get(document_id)
        assert result == "failed"
        assert document.status == "failed"
        assert "No text could be extracted" in document.error_message
        assert document.meta_data["error"] == document.error_message

    async def test_a_missing_file_fails_with_a_clear_reason(self, db):
        document_id = await db.document()
        get_storage().delete((await db.get(document_id)).file_path)

        await async_process_document(str(document_id), final_attempt=False)

        document = await db.get(document_id)
        assert document.status == "failed"
        assert "missing from storage" in document.error_message

    async def test_a_transient_failure_goes_back_to_pending_then_recovers(self, db, monkeypatch):
        document_id = await db.document()
        real = EmbeddingService.get_embeddings

        def flaky(texts):
            raise ConnectionError("embedding API unreachable")

        monkeypatch.setattr(EmbeddingService, "get_embeddings", flaky)
        with pytest.raises(TransientIngestionError):
            await async_process_document(str(document_id), final_attempt=False)

        waiting = await db.get(document_id)
        assert waiting.status == "pending"
        assert "unreachable" in waiting.error_message
        chunks_before = await db.chunk_count(document_id)
        assert chunks_before > 0
        assert waiting.meta_data["chunking_complete"] is True

        monkeypatch.setattr(EmbeddingService, "get_embeddings", real)
        assert await async_process_document(str(document_id)) == "completed"

        done = await db.get(document_id)
        assert done.status == "completed"
        assert await db.chunk_count(document_id) == chunks_before
        assert done.attempts == 2
        assert done.error_message is None
        assert "error" not in done.meta_data

    async def test_the_final_attempt_fails_for_good(self, db, monkeypatch):
        document_id = await db.document()
        monkeypatch.setattr(
            EmbeddingService, "get_embeddings", lambda texts: (_ for _ in ()).throw(TimeoutError("slow"))
        )

        assert await async_process_document(str(document_id), final_attempt=True) == "failed"
        assert (await db.get(document_id)).status == "failed"

    async def test_rerunning_replaces_chunks_instead_of_doubling_them(self, db):
        document_id = await db.document()
        await async_process_document(str(document_id))
        first = await db.chunk_count(document_id)

        await async_process_document(str(document_id), force=True)

        assert await db.chunk_count(document_id) == first


class TestQuarantine:
    async def test_an_infected_file_is_failed_flagged_and_deleted(self, db, monkeypatch):
        document_id = await db.document()
        key = (await db.get(document_id)).file_path
        monkeypatch.setattr(
            malware,
            "scan_file",
            lambda path: malware.ScanResult(clean=False, signature="Eicar-Signature", scanner="clamav"),
        )

        await async_process_document(str(document_id), final_attempt=False)

        document = await db.get(document_id)
        assert document.status == "failed"
        assert document.meta_data["quarantined"] is True
        assert document.meta_data["malware_signature"] == "Eicar-Signature"
        assert "malware" in document.error_message
        assert not get_storage().exists(key)
        assert await db.chunk_count(document_id) == 0

    async def test_an_unreachable_scanner_is_retried_not_skipped(self, db, monkeypatch):
        document_id = await db.document()

        def down(path):
            raise malware.ScannerUnavailableError("clamd is restarting")

        monkeypatch.setattr(malware, "scan_file", down)

        with pytest.raises(TransientIngestionError):
            await async_process_document(str(document_id), final_attempt=False)
        document = await db.get(document_id)
        assert document.status == "pending"
        assert "malware_scan" not in document.meta_data

    async def test_a_clean_scan_is_remembered_when_a_later_stage_fails(self, db, monkeypatch):
        """The scan passed, then extraction found no text. The document fails"""
        document_id = await db.document(content=b"   \n  ")
        monkeypatch.setattr(
            malware, "scan_file", lambda path: malware.ScanResult(clean=True, scanner="clamav")
        )

        assert await async_process_document(str(document_id)) == "failed"

        document = await db.get(document_id)
        assert "No text could be extracted" in document.error_message
        assert document.meta_data["malware_scan"] == "clamav"

    async def test_a_clean_scan_is_recorded_on_success(self, db, monkeypatch):
        document_id = await db.document()
        monkeypatch.setattr(
            malware, "scan_file", lambda path: malware.ScanResult(clean=True, scanner="clamav")
        )

        assert await async_process_document(str(document_id)) == "completed"
        assert (await db.get(document_id)).meta_data["malware_scan"] == "clamav"


class TestRecovery:
    async def test_a_stuck_processing_document_is_requeued(self, db):
        stale = minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5)
        document_id = await db.document(status="processing", attempts=1)
        await db.set(document_id, processing_started_at=stale, updated_at=stale)

        result = await recover_stuck_documents()

        assert str(document_id) in result["requeued"]
        assert str(document_id) in db.dispatched
        document = await db.get(document_id)
        assert document.status == "pending"
        assert document.processing_started_at is None

    async def test_a_lost_pending_document_is_requeued(self, db):
        document_id = await db.document(status="pending")
        await db.set(document_id, updated_at=minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5))

        result = await recover_stuck_documents()

        assert str(document_id) in result["requeued"]

    async def test_out_of_attempts_is_failed_not_requeued(self, db):
        stale = minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5)
        document_id = await db.document(status="processing", attempts=settings.INGESTION_MAX_RETRIES + 1)
        await db.set(document_id, processing_started_at=stale, updated_at=stale)

        result = await recover_stuck_documents()

        assert str(document_id) in result["failed"]
        assert str(document_id) not in db.dispatched
        document = await db.get(document_id)
        assert document.status == "failed"
        assert "attempts" in document.error_message

    async def test_live_and_finished_documents_are_left_alone(self, db):
        busy = await db.document(status="processing", attempts=1)
        await db.set(busy, processing_started_at=minutes_ago(1))
        done = await db.document(status="completed")
        await db.set(done, updated_at=minutes_ago(10_000))
        fresh = await db.document(status="pending")

        result = await recover_stuck_documents()

        touched = result["requeued"] + result["failed"]
        for document_id in (busy, done, fresh):
            assert str(document_id) not in touched
        assert (await db.get(busy)).status == "processing"

    async def test_a_recovered_document_then_processes(self, db):
        stale = minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5)
        document_id = await db.document(status="processing", attempts=1)
        await db.set(document_id, processing_started_at=stale, updated_at=stale)

        await recover_stuck_documents()
        assert await async_process_document(str(document_id)) == "completed"

    async def test_one_failed_dispatch_does_not_stop_the_sweep(self, db, monkeypatch):
        """The queue refusing ONE document (Redis restarting under the API at"""
        stale = minutes_ago(settings.INGESTION_STALE_AFTER_MINUTES + 5)
        first = await db.document(status="pending")
        second = await db.document(status="pending")
        for document_id in (first, second):
            await db.set(document_id, updated_at=stale)
        attempted: list[str] = []

        def flaky_dispatch(document_id):
            attempted.append(document_id)
            if document_id == str(first):
                raise ConnectionError("Redis is restarting")

        monkeypatch.setattr(ingestion, "dispatch", flaky_dispatch)

        result = await recover_stuck_documents()

        assert str(first) in attempted and str(second) in attempted
        assert str(first) in result["requeued"] and str(second) in result["requeued"]
        assert (await db.get(first)).status == "pending"
        assert (await db.get(second)).status == "pending"


class TestInlineRetries:
    async def test_transient_failures_are_retried_in_process(self, db, monkeypatch):
        monkeypatch.setattr(ingestion, "retry_delay_seconds", lambda attempt: 0)
        document_id = await db.document()
        real = EmbeddingService.get_embeddings
        calls = {"n": 0}

        def flaky_twice(texts):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ConnectionError("blip")
            return real(texts)

        monkeypatch.setattr(EmbeddingService, "get_embeddings", flaky_twice)

        assert await run_inline_ingestion(str(document_id)) == "completed"
        assert (await db.get(document_id)).attempts == 3

    async def test_it_gives_up_after_the_configured_retries(self, db, monkeypatch):
        monkeypatch.setattr(ingestion, "retry_delay_seconds", lambda attempt: 0)
        monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 2)
        document_id = await db.document()

        def always_down(texts):
            raise ConnectionError("down")

        monkeypatch.setattr(EmbeddingService, "get_embeddings", always_down)

        assert await run_inline_ingestion(str(document_id)) == "failed"
        document = await db.get(document_id)
        assert document.status == "failed"
        assert document.attempts == 3
