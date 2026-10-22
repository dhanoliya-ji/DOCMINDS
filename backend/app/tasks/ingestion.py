"""
ingestion.py
============
WHAT THIS FILE DOES
-------------------
This is the document processing pipeline -- everything that happens to a file
AFTER it is uploaded and BEFORE it can be searched.

WHY IT RUNS IN THE BACKGROUND
-----------------------------
Processing a document is slow. A 200-page scanned PDF can take minutes: every
page must be rasterised, OCR'd, chunked and embedded.

If we did that inside the upload request, the user's browser would sit on a
loading spinner for two minutes and then very likely time out.

So instead:

    1. The upload endpoint saves the file, creates a Document row with
       status="pending", and IMMEDIATELY replies to the browser.
    2. It drops a job id onto a Redis queue.
    3. A separate Celery worker process picks that job up and runs THIS file.
    4. The frontend polls the document's status until it reads "completed".

The user gets an instant response, and the heavy work happens out of sight.
This is the standard "task queue" pattern, and it is what the `status` column
on Document exists to support.

THE STAGES OF THE PIPELINE
--------------------------
      stored file (services/storage.py: local disk or S3)
          |
      0.  CLAIM              atomically mark it "processing" -- or skip it if
          |                  another worker already has it
      1.  fetch + SCAN       a local copy; ClamAV if MALWARE_SCAN=clamav
          |
      2.  EXTRACT            extractor.py -> raw text  (+ OCR if it is a scan)
          |
      3.  CHUNK              chunker.py   -> ~500-token pieces, page-tagged
          |
      4.  save chunks        the readable text goes into the `chunks` table
          |
      5.  EMBED              embedding.py -> a 384-number vector per chunk
          |
      6.  save vectors       into `chunk_embeddings`, ready for search
          |
      7.  record metadata    language, word count, timings, settings used
          |
      status = "completed"   the document is now searchable

WHAT HAPPENS WHEN SOMETHING GOES WRONG  (the durability rules)
--------------------------------------------------------------
Failures come in two kinds, and treating them the same is the classic mistake:

    PERMANENT   the file is corrupt, empty, a zip bomb, malware, an unsupported
                type. Trying again does exactly the same thing. -> "failed"
                at once, with the reason in `error_message`.
    TRANSIENT   the database dropped a connection, the embedding API said
                "rate limited", S3 timed out, ClamAV restarted. Trying again a
                little later very likely works. -> back to "pending" and
                RETRIED with exponential backoff (10s, 20s, 40s ... capped, by
                default), up to INGESTION_MAX_RETRIES times; only then "failed".

And for failures that never get the chance to be recorded at all -- the worker
is OOM-killed, the machine reboots, the API restarts mid-job in inline mode --
there are three safety nets:

    acks_late + reject_on_worker_lost
        The queue message is acknowledged only AFTER the task finishes, so if
        the worker dies mid-job, Redis hands the message to another worker.
    the CLAIM
        Re-delivery and retries can run the same document twice. The claim is
        a single conditional UPDATE ("set processing WHERE status is pending"),
        so exactly one run wins and the others skip. Re-running is also safe
        because stage 4 deletes any chunks a previous run left behind.
    recover_stuck_documents()
        Runs at API startup and every few minutes from Celery beat. Anything
        "processing" or "pending" for longer than INGESTION_STALE_AFTER_MINUTES
        was orphaned; it is re-queued, or failed if it has used every attempt.

HOW THE ASYNC/SYNC BRIDGE WORKS
-------------------------------
Celery is a SYNCHRONOUS system -- its tasks are ordinary blocking functions.
Our database layer is ASYNCHRONOUS. The two cannot call each other directly.

The solution is at the bottom of this file: the Celery task is a small ordinary
function that calls `asyncio.run(...)`, which spins up an event loop, runs our
async code to completion inside it, then shuts it down. This is also exactly
why db/session.py configures `NullPool` -- connections must not be reused
across these short-lived event loops, or you get "Event loop is closed" errors.
"""

import asyncio
import datetime
import logging
import time
import uuid
from typing import Optional

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import and_, or_, select, update

from app.core.celery_app import celery_app
from app.core import observability
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.services import malware
from app.services.chunker import ChunkerService
from app.services.embedding import EmbeddingService
from app.services.extractor import DocumentExtractor
from app.services.storage import (
    StorageObjectNotFound,
    StorageUnavailableError,
    get_storage,
)

logger = logging.getLogger(__name__)


# ======================================================================
# FAILURE CLASSIFICATION
# ======================================================================


class TransientIngestionError(Exception):
    """
    Raised OUT of the pipeline when a failure is worth retrying.

    The Celery task is configured with `autoretry_for=(TransientIngestionError,)`
    -- so raising this is how the pipeline says "schedule me again", and
    raising anything else (or returning) means "done, one way or the other".
    """


class MalwareDetected(Exception):
    """The scanner found something. Permanent, and the file is quarantined."""

    def __init__(self, signature: str):
        super().__init__(f"Rejected by the malware scan: {signature}")
        self.signature = signature


# Exception class names (anywhere in the class hierarchy) that mean "the other
# side is temporarily unavailable". Matched by NAME so this module does not
# have to import every provider's SDK -- openai's RateLimitError, Google's
# ResourceExhausted, botocore's EndpointConnectionError and SQLAlchemy's
# OperationalError are all recognised without importing any of them.
_TRANSIENT_NAME_FRAGMENTS = (
    "RateLimit",
    "Timeout",
    "Connection",
    "Throttl",
    "Unavailable",
    "ResourceExhausted",
    "DeadlineExceeded",
    "OperationalError",  # sqlalchemy: the database went away mid-query
    "InterfaceError",  # sqlalchemy / asyncpg: the connection is unusable
    "InternalServerError",  # a provider's 5xx
)


def is_transient(exc: BaseException) -> bool:
    """
    Would doing the same thing again, a little later, plausibly succeed?

    Deliberately conservative: anything not recognised is PERMANENT. Retrying
    a genuinely broken file three times wastes minutes of worker time and
    delays the "failed" the user is waiting to see; missing an occasional
    transient error only costs one manual re-upload.
    """
    if isinstance(exc, (TransientIngestionError, StorageUnavailableError, malware.ScannerUnavailableError)):
        return True
    if isinstance(exc, (MalwareDetected, StorageObjectNotFound, SoftTimeLimitExceeded)):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    names = [cls.__name__ for cls in type(exc).__mro__]
    return any(fragment in name for name in names for fragment in _TRANSIENT_NAME_FRAGMENTS)


def describe_failure(exc: BaseException) -> str:
    """The message stored in `error_message` -- written for the user."""
    if isinstance(exc, SoftTimeLimitExceeded):
        return (
            "Processing took longer than "
            f"{settings.INGESTION_SOFT_TIME_LIMIT_SECONDS} seconds and was stopped."
        )
    if isinstance(exc, StorageObjectNotFound):
        return "The uploaded file is missing from storage."
    text = str(exc).strip() or type(exc).__name__
    # Long tracebacks or provider error bodies are not useful in a UI badge.
    return text if len(text) <= 1000 else text[:997] + "..."


def retry_delay_seconds(retries_so_far: int) -> int:
    """
    Exponential backoff: the base, then double it each time -- 10, 20, 40 ...
    seconds with the defaults -- capped at the configured maximum.

    The same formula Celery applies for `retry_backoff` (factor x 2^retries),
    so inline mode waits as long as the worker would, minus the jitter.
    """
    return min(
        settings.INGESTION_RETRY_BACKOFF_SECONDS * 2**retries_so_far,
        settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS,
    )


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stale_cutoff(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    return (now or _utcnow()) - datetime.timedelta(minutes=settings.INGESTION_STALE_AFTER_MINUTES)


# ======================================================================
# STAGE 0: THE CLAIM
# ======================================================================


async def claim_document(db, document_id, *, force: bool = False) -> Optional[Document]:
    """
    Atomically move a document to "processing" and return it -- or return
    None if this run should not process it.

    One UPDATE with the conditions in its WHERE clause, so the check and the
    change happen as ONE step inside the database. Two workers racing for the
    same document both send it; Postgres applies one, and the other's WHERE no
    longer matches, so it updates nothing and skips. A separate "SELECT, check
    in Python, then UPDATE" would let both pass the check before either wrote.

    A document can be claimed when it is:
      * pending                       -- the normal case, and a pending retry
      * processing, but stale         -- its previous worker died
      * anything but freshly
        processing, with force=True   -- an explicit re-index
    """
    document_id = uuid.UUID(str(document_id))
    claimable = or_(
        Document.status == "pending",
        and_(
            Document.status == "processing",
            or_(
                Document.processing_started_at.is_(None),
                Document.processing_started_at < _stale_cutoff(),
            ),
        ),
    )
    if force:
        claimable = or_(claimable, Document.status.in_(("completed", "failed")))

    result = await db.execute(
        update(Document)
        .where(Document.id == document_id, claimable)
        .values(
            status="processing",
            processing_started_at=_utcnow(),
            attempts=Document.attempts + 1,
            error_message=None,
        )
        # The rows are re-read below; there is nothing in the session to sync.
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    if result.rowcount != 1:
        return None
    return await db.get(Document, document_id, populate_existing=True)


def _fetch_scan_and_extract(
    storage_name: str, key: str, file_type: str, progress: Optional[dict] = None
):
    """
    Stages 1 and 2, in one blocking function run on a thread.

    Grouped because they share the local copy: with S3 the object is
    downloaded once, scanned, extracted, and the temporary file deleted --
    all inside the `with` block.

    `progress` is filled in with {"malware_scan": <scanner>} the moment the
    scan passes. The caller reads it if a LATER stage fails, so a document
    that failed for an unrelated reason (no text in it, say) is still on
    record as scanned clean -- which is what the preview endpoint checks
    before it will serve the file back.
    """
    storage = get_storage(storage_name)
    with storage.local_copy(key) as local_path:
        verdict = malware.scan_file(local_path)
        if not verdict.clean:
            raise MalwareDetected(verdict.signature or "unknown")
        if progress is not None:
            progress["malware_scan"] = verdict.scanner
        return verdict, DocumentExtractor.extract(local_path, file_type)


# ======================================================================
# THE PIPELINE
# ======================================================================


async def async_process_document(
    document_id: str, *, final_attempt: bool = True, force: bool = False
) -> str:
    """
    Run the full ingestion pipeline for one document.

    This is where a raw file becomes searchable knowledge. It is `async`
    because every database operation is awaited.

    `final_attempt` -- when False, a transient failure puts the document back
    to "pending" and raises TransientIngestionError so the caller retries it.
    When True there are no retries left, so every failure is final.

    Returns what happened: "completed", "failed", "retrying" (only via the
    exception) or "skipped" (someone else has it, or it is already done).
    """
    # Measure total processing time, so we can store it as a metric and show
    # "processed in 4.2s" in the UI.
    started_at = time.perf_counter()

    # `async with` opens a database session and guarantees it is closed again
    # even if an exception is raised part-way through.
    async with SessionLocal() as db:

        # ==============================================================
        # STAGE 0: Claim the document
        # ==============================================================
        # Committed straight away, so the frontend's polling sees
        # "processing" at once rather than sitting on "pending".
        document = await claim_document(db, document_id, force=force)

        if document is None:
            # Deleted since the upload, already completed, or claimed by
            # another worker (a duplicate delivery). All are fine: exit
            # quietly rather than crashing the worker or doing the work twice.
            logger.warning(f"Document '{document_id}' is not claimable. Skipping.")
            return "skipped"

        # Captured now: after a failed write, touching the ORM object again
        # can itself raise, and the failure handler needs to know which row.
        claimed_id = document.id
        # Every later line of this run says which document and tenant it is about.
        observability.bind(document_id=document.id, project_id=document.project_id)

        logger.info(
            f"=== Starting pipeline for: {document.filename} "
            f"(attempt {document.attempts}) ==="
        )

        # Filled in by stage 1 once the malware scan passes; read by the
        # failure handler (see `_fetch_scan_and_extract`).
        progress: dict = {}

        try:
            # ==========================================================
            # STAGES 1-2: fetch from storage, scan, EXTRACT
            # ==========================================================
            # extractor.py inspects the file type and dispatches to the right
            # library: PyMuPDF for PDF, python-docx for Word, and so on.
            # For a scanned PDF it detects the lack of a text layer and falls
            # back to OCR automatically.
            logger.info(
                f"1/5 Fetching, scanning and extracting text "
                f"(type: {document.file_type}, storage: {document.storage_backend})..."
            )
            # The three heavy steps (extraction/OCR, chunking, embedding) are
            # plain blocking code, so each runs on a thread. In the worker that
            # changes nothing; with INGESTION_MODE=inline this job runs inside
            # the API's event loop, and without the threads one large PDF would
            # freeze every other request until it finished.
            verdict, extraction = await asyncio.to_thread(
                _fetch_scan_and_extract,
                document.storage_backend,
                document.file_path,
                document.file_type,
                progress,
            )
            if not extraction.text or not extraction.text.strip():
                raise ValueError(
                    "No text could be extracted. The file may be empty, "
                    "corrupted, or an image with no readable text."
                )

            logger.info(
                f"Extracted {len(extraction.text):,} characters "
                f"(language: {extraction.language}, OCR used: {extraction.needs_ocr})"
            )

            # ==========================================================
            # STAGE 3: CHUNK -- split the text into searchable pieces
            # ==========================================================
            # The chunking settings were chosen by the user at upload time and
            # stashed in meta_data. If they are absent we fall back to the
            # global defaults from .env.
            document_meta = document.meta_data or {}
            chunk_strategy = (
                document_meta.get("chunk_strategy") or settings.DEFAULT_CHUNK_STRATEGY
            )
            chunk_size = int(
                document_meta.get("chunk_size") or settings.DEFAULT_CHUNK_SIZE
            )
            chunk_overlap = int(
                document_meta.get("chunk_overlap") or settings.DEFAULT_CHUNK_OVERLAP
            )

            logger.info(
                f"2/5 Chunking (strategy: {chunk_strategy}, "
                f"size: {chunk_size} tokens, overlap: {chunk_overlap})..."
            )
            chunks_data = await asyncio.to_thread(
                ChunkerService.chunk_document,
                text=extraction.text,
                strategy=chunk_strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            if not chunks_data:
                raise ValueError("Chunking produced no chunks from the extracted text.")

            logger.info(f"Produced {len(chunks_data)} chunks.")

            # ==========================================================
            # STAGE 4: Clear any previous chunks (idempotent re-processing)
            # ==========================================================
            # A document can legitimately be processed more than once -- for
            # example after retrying a failure, or after switching embedding
            # models. Deleting the old chunks first means re-running is SAFE
            # and repeatable rather than silently doubling the data.
            #
            # Deleting the Chunk rows also removes their embeddings, because
            # the relationship cascades (see models/chunk.py).
            existing = await db.execute(
                select(Chunk).where(Chunk.document_id == document.id)
            )
            old_chunks = existing.scalars().all()
            if old_chunks:
                logger.info(f"Removing {len(old_chunks)} previous chunks.")
                for old_chunk in old_chunks:
                    await db.delete(old_chunk)
                # `flush` sends the DELETEs to the database now, so the inserts
                # that follow cannot collide with rows we are about to remove.
                await db.flush()

            # ==========================================================
            # STAGE 5: Save the chunk text
            # ==========================================================
            chunk_objects = []
            for item in chunks_data:
                chunk_objects.append(
                    Chunk(
                        # The id is generated HERE in Python rather than by the
                        # database, so we can pair each chunk with its vector
                        # below without a second round trip to fetch the ids.
                        id=uuid.uuid4(),
                        document_id=document.id,
                        content=item["content"],
                        chunk_index=item["chunk_index"],
                        page_number=item["page_number"],
                        meta_data=item["meta_data"],
                    )
                )

            # `add_all` queues every row in one operation, far faster than
            # adding them one at a time.
            db.add_all(chunk_objects)
            await db.flush()

            # ==========================================================
            # STAGE 6: EMBED -- convert each chunk into a vector
            # ==========================================================
            # THIS IS THE STEP THAT MAKES SEMANTIC SEARCH POSSIBLE.
            # Each chunk becomes 384 numbers describing its meaning. Later, a
            # user's question becomes 384 numbers too, and Postgres finds the
            # chunks whose numbers point in the most similar direction.
            logger.info(
                f"3/5 Embedding {len(chunk_objects)} chunks "
                f"via '{settings.EMBEDDING_PROVIDER}'..."
            )

            chunk_texts = [chunk.content for chunk in chunk_objects]
            # One batched call rather than a loop: dramatically faster, because
            # the model processes many texts in parallel.
            vectors = await asyncio.to_thread(EmbeddingService.get_embeddings, chunk_texts)

            # Record WHICH model produced these vectors. Vectors from different
            # models are not comparable, so this column is what identifies
            # stale rows if the model is ever changed.
            model_name = EmbeddingService.current_model_name()

            # Sanity check. If the counts disagree, pairing chunk[i] with
            # vector[i] would attach the wrong meaning to the wrong text --
            # a silent corruption that would be very hard to diagnose later.
            if len(vectors) != len(chunk_objects):
                raise ValueError(
                    f"Embedding count mismatch: got {len(vectors)} vectors "
                    f"for {len(chunk_objects)} chunks."
                )

            logger.info(f"4/5 Saving vectors (model: {model_name})...")

            # `zip` walks both lists together, pairing each chunk with the
            # vector produced from its own text.
            embedding_objects = [
                ChunkEmbedding(
                    id=uuid.uuid4(),
                    chunk_id=chunk.id,
                    embedding=vector,
                    model_name=model_name,
                )
                for chunk, vector in zip(chunk_objects, vectors, strict=True)
            ]

            db.add_all(embedding_objects)
            await db.flush()

            # ==========================================================
            # STAGE 7: Record what happened, for the UI and for debugging
            # ==========================================================
            elapsed = round(time.perf_counter() - started_at, 2)

            # Start from the existing metadata and layer the new facts on top,
            # so the user's original chunking choices are preserved.
            metadata = dict(document.meta_data) if document.meta_data else {}
            # A success after a retry: the earlier attempt's error no longer
            # describes this document.
            for stale_key in ("error", "failed_at_seconds"):
                metadata.pop(stale_key, None)
            metadata.update(extraction.metadata)

            metadata.update(
                {
                    # --- content facts ---
                    "language": extraction.language,
                    "needs_ocr": extraction.needs_ocr,
                    "word_count": len(extraction.text.split()),
                    "char_count": len(extraction.text),
                    # --- how it was chunked ---
                    "chunk_count": len(chunk_objects),
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    # --- how it was embedded ---
                    "embedding_model": model_name,
                    "embedding_provider": settings.EMBEDDING_PROVIDER,
                    "embedding_dimension": settings.EMBEDDING_DIMENSION,
                    # --- safety ---
                    "malware_scan": verdict.scanner,
                    # --- performance ---
                    "processing_seconds": elapsed,
                    "attempts": document.attempts,
                }
            )

            # Reassigning the whole dictionary (rather than mutating it in
            # place) is required for SQLAlchemy to notice that a JSONB column
            # changed. An in-place `metadata["x"] = 1` would not be saved.
            document.meta_data = metadata
            document.status = "completed"
            document.error_message = None

            # WEBHOOK "document.processed" (off unless WEBHOOKS_ENABLED):
            # staged in this same transaction, sent only after the commit.
            await _emit_document_event(
                db, document, "document.processed",
                {"chunk_count": len(chunk_objects), "processing_seconds": elapsed},
            )

            # One commit writes the chunks, the vectors and the status change
            # together as a single transaction. Either all of it lands or none
            # of it does -- there is no state where chunks exist but the
            # document still reads "processing".
            await db.commit()

            logger.info(
                f"5/5 DONE: {document.filename} -> "
                f"{len(chunk_objects)} chunks in {elapsed}s. Now searchable."
            )
            return "completed"

        except Exception as exc:
            return await _handle_failure(
                db,
                claimed_id,
                exc,
                started_at,
                final_attempt,
                scanned_by=progress.get("malware_scan"),
            )


async def _emit_document_event(db, document: Document, event_type: str, extra: dict) -> None:
    """
    Stage a document webhook (services/webhooks.py). A no-op unless
    WEBHOOKS_ENABLED; never raises -- a webhook problem must not change what
    happens to the document itself.
    """
    if not settings.WEBHOOKS_ENABLED:
        return
    try:
        from app.models.project import Project
        from app.services import webhooks

        project = await db.get(Project, document.project_id)
        if project is None:
            return
        await webhooks.emit(
            db,
            project.org_id,
            event_type,
            {
                "document_id": str(document.id),
                "project_id": str(document.project_id),
                "filename": document.filename,
                "status": document.status,
                **extra,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not stage document webhook", extra={"error": repr(exc)})


async def _handle_failure(
    db,
    document_id,
    exc: Exception,
    started_at: float,
    final_attempt: bool,
    scanned_by: Optional[str] = None,
) -> str:
    """
    FAILURE HANDLING -- record what went wrong, and decide whether to retry.

    Any failure ends with the reason ON THE DOCUMENT, so the UI can show the
    user a real explanation instead of leaving it stuck on "processing".

    `scanned_by` is set when the malware scan PASSED before the failure; it
    is recorded as meta_data["malware_scan"], exactly as a success records
    it. A failure before the scan finished (ClamAV unreachable, the file
    missing) records nothing -- and while scanning is on, the preview
    endpoint will not serve a failed file without that record.
    """
    transient = is_transient(exc)
    retrying = transient and not final_attempt
    message = describe_failure(exc)

    # The traceback rides on the same structured line (exc_info), instead of
    # being printed separately where nothing ties it to this document.
    logger.log(
        logging.WARNING if retrying else logging.ERROR,
        "Transient ingestion failure; will retry" if retrying else "Ingestion failed",
        extra={"document_id": str(document_id), "error": message},
        exc_info=exc,
    )

    try:
        # The session may be in a broken state after an error mid-write --
        # half the chunks flushed, say -- so throw all of that away first.
        await db.rollback()

        # Re-read rather than reuse the object: a rollback expires every
        # loaded attribute, and the row may have changed underneath us.
        document = await db.get(Document, document_id, populate_existing=True)
        if document is None:
            return "skipped"

        error_metadata = dict(document.meta_data) if document.meta_data else {}
        # Kept in meta_data too: frontends written before `error_message`
        # existed read the reason from here.
        error_metadata["error"] = message
        error_metadata["failed_at_seconds"] = round(time.perf_counter() - started_at, 2)
        if scanned_by is not None:
            error_metadata["malware_scan"] = scanned_by

        if isinstance(exc, MalwareDetected):
            # QUARANTINE: the bytes are deleted from storage so the preview
            # endpoint can never serve them, and the flag tells the UI why.
            error_metadata["quarantined"] = True
            error_metadata["malware_signature"] = exc.signature
            try:
                get_storage(document.storage_backend).delete(document.file_path)
            except Exception as delete_error:  # pragma: no cover - best effort
                logger.warning(f"Could not delete quarantined file: {delete_error}")

        document.meta_data = error_metadata
        document.error_message = message
        if retrying:
            # Back in the queue's hands. "pending" is what lets the retry
            # claim it again; the claim time is cleared so recovery does not
            # mistake the wait for a dead worker.
            document.status = "pending"
            document.processing_started_at = None
        else:
            document.status = "failed"
            # Only the FINAL failure is an event -- a retry is not news.
            await _emit_document_event(db, document, "document.failed", {"error": message})
        await db.commit()
    except Exception as record_error:
        # Could not even record the failure -- most likely the database is the
        # thing that is down. A transient retry is still worth scheduling; if
        # nothing else, recovery will find the row stuck in "processing".
        logger.warning(f"Could not record the failure: {record_error}")
        if transient and not final_attempt:
            raise TransientIngestionError(message) from exc
        raise

    if retrying:
        raise TransientIngestionError(message) from exc
    return "failed"


async def mark_failed(document_id: str, message: str) -> None:
    """Set a document to failed with `message` -- used when a task times out."""
    async with SessionLocal() as db:
        document = await db.get(Document, uuid.UUID(str(document_id)))
        if document is None:
            return
        metadata = dict(document.meta_data) if document.meta_data else {}
        metadata["error"] = message
        document.meta_data = metadata
        document.error_message = message
        document.status = "failed"
        await db.commit()


# ======================================================================
# INLINE MODE: retries without Celery
# ======================================================================


async def run_inline_ingestion(document_id: str, *, force: bool = False) -> str:
    """
    The pipeline with the same retry policy Celery applies, for
    INGESTION_MODE=inline (no worker, no Redis -- the free demo).

    Runs as a background task inside the API process, so the waits between
    attempts are `asyncio.sleep`: they cost nothing while they wait.
    """
    max_retries = settings.INGESTION_MAX_RETRIES
    for attempt in range(max_retries + 1):
        try:
            return await async_process_document(
                document_id, final_attempt=attempt >= max_retries, force=force and attempt == 0
            )
        except TransientIngestionError:
            await asyncio.sleep(retry_delay_seconds(attempt))
    return "failed"  # pragma: no cover - the final attempt never raises transient


# Strong references to inline jobs started outside a request (the startup
# recovery). asyncio only keeps WEAK references to tasks, so without this set
# a running job could be garbage-collected half way through.
_inline_jobs: set = set()


def dispatch(document_id: str) -> None:
    """
    Queue a document for processing in whichever way INGESTION_MODE says.

    Must be called from inside a running event loop in inline mode (the API's
    own), which is where recovery calls it from.
    """
    if settings.INGESTION_MODE == "celery":
        process_document_task.delay(str(document_id))
    else:
        job = asyncio.get_running_loop().create_task(run_inline_ingestion(str(document_id)))
        _inline_jobs.add(job)
        job.add_done_callback(_inline_jobs.discard)


# ======================================================================
# RECOVERY: finding the documents nobody is working on
# ======================================================================


async def recover_stuck_documents(*, now: Optional[datetime.datetime] = None, requeue: bool = True) -> dict:
    """
    Re-queue documents that have been "processing" or "pending" for longer
    than INGESTION_STALE_AFTER_MINUTES, and fail those out of attempts.

    How a document gets stuck, and why each case ends up here:

        the worker was OOM-killed or the machine rebooted mid-job
            -> "processing" forever; nothing will ever update it
        INGESTION_MODE=inline and the API restarted mid-job
            -> the same: the background task died with the process
        Redis lost the queue (restart without persistence, a flush)
            -> "pending" forever; the message it was waiting for is gone

    Safe to run often and from several places at once: re-queuing a document
    that is in fact still queued only produces a duplicate message, and the
    claim makes the duplicate skip.

    Returns {"requeued": [ids], "failed": [ids]}.
    """
    cutoff = _stale_cutoff(now)
    requeued: list[str] = []
    failed: list[str] = []

    async with SessionLocal() as db:
        stuck = await db.execute(
            select(Document).where(
                or_(
                    and_(
                        Document.status == "processing",
                        or_(
                            Document.processing_started_at < cutoff,
                            and_(
                                Document.processing_started_at.is_(None),
                                Document.updated_at < cutoff,
                            ),
                        ),
                    ),
                    and_(Document.status == "pending", Document.updated_at < cutoff),
                )
            )
        )
        for document in stuck.scalars().all():
            # `attempts` counts claims, so the first run plus every retry.
            if document.attempts > settings.INGESTION_MAX_RETRIES:
                message = (
                    f"Processing did not finish after {document.attempts} attempts "
                    "and was abandoned."
                )
                metadata = dict(document.meta_data) if document.meta_data else {}
                metadata["error"] = message
                document.meta_data = metadata
                document.error_message = message
                document.status = "failed"
                failed.append(str(document.id))
            else:
                document.status = "pending"
                document.processing_started_at = None
                requeued.append(str(document.id))
        await db.commit()

    # Queued only AFTER the commit, so the job cannot start before the row
    # says "pending" and find nothing to claim.
    if requeue:
        for document_id in requeued:
            try:
                dispatch(document_id)
            except Exception as exc:
                # The queue is unreachable -- most likely Redis is restarting
                # alongside the API. The row already says "pending", so a
                # later sweep queues it; one failure must not stop the rest
                # from being queued, or abort the API's startup.
                logger.warning(f"Could not queue {document_id}; a later sweep will retry: {exc}")

    if requeued or failed:
        logger.warning(f"Re-queued {len(requeued)} and failed {len(failed)} stuck document(s).")
    return {"requeued": requeued, "failed": failed}


# ----------------------------------------------------------------------
# THE CELERY TASKS THEMSELVES
# ----------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.ingestion.process_document_task",
    # ---- retries for TRANSIENT failures only ----
    autoretry_for=(TransientIngestionError,),
    max_retries=settings.INGESTION_MAX_RETRIES,
    # A number here is the FIRST wait in seconds; Celery doubles it per retry.
    retry_backoff=settings.INGESTION_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,  # spread retries out so a recovered API is not stampeded
    # ---- surviving a dead worker ----
    acks_late=True,
    reject_on_worker_lost=True,
    # ---- a stuck job must not hold a worker forever ----
    soft_time_limit=settings.INGESTION_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.INGESTION_TIME_LIMIT_SECONDS,
)
def process_document_task(self, document_id: str, force: bool = False) -> str:
    """
    The job that Celery workers actually run.

    `@celery_app.task` registers this function with Celery. The upload endpoint
    then triggers it with:

        process_document_task.delay(str(document.id))

    `.delay()` does NOT run the function. It serialises the arguments, pushes a
    message onto the Redis queue, and returns instantly. A worker process --
    possibly on an entirely different machine -- picks the message up and runs
    this function there.

    The explicit `name=` keeps the task's identity stable. Without it Celery
    derives a name from the module path, so moving this file would strand any
    jobs already sitting in the queue under the old name.

    THE DECORATOR OPTIONS, IN PLAIN WORDS
        autoretry_for / retry_backoff   a TransientIngestionError reschedules
                                        the task with growing waits
        acks_late                       the message is removed from the queue
                                        only when the task FINISHES, not when
                                        it starts -- a crash leaves it queued
        reject_on_worker_lost           ...including when the worker process is
                                        killed outright
        soft_time_limit / time_limit    give up on a job that runs too long
                                        (needs the prefork pool: not --pool=solo)

    `asyncio.run()` is the bridge between Celery's synchronous world and our
    asynchronous database code: it creates a fresh event loop, runs the async
    pipeline to completion inside it, and then closes it.
    """
    final_attempt = self.request.retries >= self.max_retries
    try:
        return asyncio.run(
            async_process_document(document_id, final_attempt=final_attempt, force=force)
        )
    except SoftTimeLimitExceeded as exc:
        # The time limit fired somewhere the pipeline's own handler could not
        # catch it. Record it in a fresh event loop -- the old one is gone.
        asyncio.run(mark_failed(document_id, describe_failure(exc)))
        return "failed"


@celery_app.task(name="app.tasks.ingestion.recover_stuck_documents_task")
def recover_stuck_documents_task() -> dict:
    """
    The periodic sweep, scheduled by Celery beat (see core/celery_app.py).

    Beat is a separate small process that only SENDS this task on a timer:

        celery -A app.core.celery_app.celery_app beat --loglevel=info

    or run it inside one worker with `worker -B` (fine for a single worker;
    with several, run exactly one beat, or every worker sends the sweep).
    """
    return asyncio.run(recover_stuck_documents())
