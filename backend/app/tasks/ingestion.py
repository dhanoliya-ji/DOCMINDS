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
import concurrent.futures
import contextlib
import datetime
import functools
import logging
import time
import uuid
from typing import Optional

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import and_, delete, exists, func, insert, or_, select, update

from app.core.celery_app import celery_app
from app.core import observability
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.services import malware
from app.services.chunker import StreamingChunker
from app.services.embedding import EmbeddingRateLimited, EmbeddingService, wait_listener
from app.services.extractor import DocumentExtractor, DocumentTooLarge
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


# ======================================================================
# STAGES 1-3 ON ONE THREAD: fetch, scan, extract and chunk, a batch at a time
# ======================================================================


class _ExtractionJob:
    """
    The blocking half of stages 1-3, driven one batch at a time.

    Every method runs on ONE dedicated thread (see `_run_on`): PyMuPDF is not
    built to be used from several threads, and the page generator inside
    must always be resumed from the thread that started it.

        open()        local copy (an S3 download, for that backend), malware
                      scan, and the segment generator -- nothing read yet
        next_batch()  the next few pages (or row batches) read, OCR'd where
                      needed, and CHUNKED; returns the chunks and lets the
                      pages go
        close()       closes the PDF and deletes the temporary copy

    Between batches the pipeline writes the chunks to the database and
    commits, so memory holds one batch of pages at a time -- never the whole
    document's text.
    """

    def __init__(self, storage_name: str, key: str, file_type: str, chunker: StreamingChunker):
        self.storage_name = storage_name
        self.key = key
        self.file_type = file_type
        self.chunker = chunker
        # Filled by the extractor: page_count, title, ocr_applied...
        self.info: dict = {}
        self.segments_read = 0
        self.ocr_used = False
        self.has_text = False
        self.word_count = 0
        self.char_count = 0
        # The first few thousand characters, for language detection.
        self._sample: list = []
        self._sample_len = 0
        self._stack = contextlib.ExitStack()
        self._segments = None

    def open(self, progress: dict):
        storage = get_storage(self.storage_name)
        local_path = self._stack.enter_context(storage.local_copy(self.key))
        verdict = malware.scan_file(local_path)
        if not verdict.clean:
            raise MalwareDetected(verdict.signature or "unknown")
        # Recorded the moment the scan passes: if a LATER stage fails, the
        # document is still on record as scanned clean -- which is what the
        # preview endpoint checks before it will serve the file back.
        progress["malware_scan"] = verdict.scanner
        self._segments = DocumentExtractor.iter_segments(local_path, self.file_type, self.info)
        return verdict

    def next_batch(self, max_segments: int) -> tuple:
        """(chunks, segments read) for up to `max_segments` more; 0 read = the end."""
        chunks: list = []
        taken = 0
        for segment in self._segments:
            taken += 1
            self.segments_read += 1
            text = segment.text or ""
            if text.strip():
                self.has_text = True
            self.ocr_used = self.ocr_used or segment.ocr
            self.word_count += len(text.split())
            self.char_count += len(text)
            if self._sample_len < 5000:
                piece = text[: 5000 - self._sample_len]
                self._sample.append(piece)
                self._sample_len += len(piece)
            chunks.extend(self.chunker.feed(text, segment.page_number, segment.meta))
            if self.chunker.next_index > settings.INGESTION_MAX_CHUNKS:
                raise DocumentTooLarge(
                    f"This document produces more than {settings.INGESTION_MAX_CHUNKS:,} chunks "
                    "(INGESTION_MAX_CHUNKS). Split it into smaller files, choose a larger "
                    "chunk size, or raise the limit."
                )
            if taken >= max_segments:
                break
        return chunks, taken

    def language(self) -> str:
        return DocumentExtractor.detect_language("".join(self._sample))

    def close(self) -> None:
        if self._segments is not None:
            # Runs the generator's `finally`: the PDF is closed.
            self._segments.close()
        self._stack.close()


# Pages (or row batches) read per round trip to the extraction thread. Ten
# pages of text is well under a megabyte, and the commit after each round is
# what makes the progress bar move.
_SEGMENTS_PER_BATCH = 10


async def _run_on(executor, function, *args):
    """Run a blocking call on the given executor (the job's own thread)."""
    return await asyncio.get_running_loop().run_in_executor(executor, functools.partial(function, *args))


def _chunking_signature(strategy: str, size: int, overlap: int) -> dict:
    """
    What the stored chunks were made with. A resumed run reuses them only if
    this matches exactly -- a re-index with a different chunk size must
    re-chunk, not resume.
    """
    return {"strategy": strategy, "size": size, "overlap": overlap, "version": 2}


def _embed_texts(texts: list, listener) -> list:
    """
    One embedding call, on a worker thread, with the wait listener installed.

    `EmbeddingService.get_embeddings(texts)` is called with the texts ONLY,
    so any provider (and any test double) works unchanged; the listener
    reaches the Gemini retry loop through a ContextVar.
    """
    token = wait_listener.set(listener)
    try:
        return EmbeddingService.get_embeddings(texts)
    finally:
        wait_listener.reset(token)


async def _record_progress(document_id, progress: dict) -> None:
    """
    Write meta_data["progress"] (and the heartbeat) from OUTSIDE the main
    session -- used while an embedding call is waiting on a rate limit, when
    the pipeline itself is blocked inside that call.
    """
    try:
        async with SessionLocal() as db:
            document = await db.get(Document, document_id)
            if document is None or document.status != "processing":
                return
            metadata = dict(document.meta_data or {})
            metadata["progress"] = progress
            document.meta_data = metadata
            document.processing_started_at = _utcnow()
            await db.commit()
    except Exception as exc:  # pragma: no cover - a status line is never worth a failure
        logger.debug(f"Could not record progress: {exc}")


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

    Returns what happened: "completed", "failed", "paused" (the embedding
    rate limit outlasted the retry budget -- it resumes later), "retrying"
    (only via the exception) or "skipped" (someone else has it, or it is
    already done).

    LARGE DOCUMENTS: TWO RESUMABLE PHASES
    -------------------------------------
        A. extract + chunk   pages are read, chunked and INSERTED a batch at
                             a time, committed as they go. When the last page
                             is in, meta_data["chunking_complete"] is set.
        B. embed             chunks with no vector for the CURRENT model are
                             fetched INGESTION_EMBED_BATCH_SIZE at a time,
                             embedded, saved and committed -- batch by batch.

    A rerun (a retry, a recovery after a restart, a resume after a rate-limit
    pause, or Re-index on a failed document) finds phase A complete and goes
    straight to phase B, which naturally skips every chunk already embedded.
    So a 1,200-chunk document stopped at chunk 900 continues from 901: no
    second download, no second OCR, no second bill from the embedding API.

    Nothing is searchable until the very end: retrieval only reads
    "completed" documents, and the status flips in the final commit.
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
        # failure handler (see `_ExtractionJob.open`).
        progress: dict = {}
        # Where the run has got to, for the pause and failure handlers.
        state = {"stage": "starting", "embedded": 0, "embedded_at_start": 0, "total": 0}

        try:
            # The chunking settings were chosen by the user at upload time and
            # stashed in meta_data. If they are absent we fall back to the
            # global defaults from .env.
            metadata = dict(document.meta_data or {})
            chunk_strategy = metadata.get("chunk_strategy") or settings.DEFAULT_CHUNK_STRATEGY
            chunk_size = int(metadata.get("chunk_size") or settings.DEFAULT_CHUNK_SIZE)
            chunk_overlap = int(metadata.get("chunk_overlap") or settings.DEFAULT_CHUNK_OVERLAP)
            signature = _chunking_signature(chunk_strategy, chunk_size, chunk_overlap)
            # Record WHICH model produced the vectors. Vectors from different
            # models are not comparable, so this column is what identifies
            # stale rows if the model is ever changed.
            model_name = EmbeddingService.current_model_name()

            # Resume only an UNFINISHED document whose chunks were made with
            # these exact settings. A completed one being re-indexed is
            # rebuilt from the file: the point of re-indexing it is usually
            # that something about extraction has changed (OCR installed).
            resume = (
                metadata.get("chunking") == signature
                and metadata.get("chunking_complete") is True
                and (metadata.get("progress") or {}).get("stage") != "completed"
            )

            if not resume:
                metadata = await _extract_and_chunk(
                    db, document, metadata, signature, progress, state,
                    chunk_strategy, chunk_size, chunk_overlap,
                )
            else:
                logger.info("1-2/5 Chunks from an earlier run are complete; resuming at embedding.")

            # ==========================================================
            # STAGE 5-6: EMBED, a batch at a time, skipping finished chunks
            # ==========================================================
            # THIS IS THE STEP THAT MAKES SEMANTIC SEARCH POSSIBLE. Each chunk
            # becomes a vector describing its meaning. Later, a user's
            # question becomes a vector too, and Postgres finds the chunks
            # whose vectors point in the most similar direction.
            state["stage"] = "embedding"
            await _embed_pending_chunks(db, document, metadata, model_name, state)

            # ==========================================================
            # STAGE 7: Record what happened, for the UI and for debugging
            # ==========================================================
            elapsed = round(time.perf_counter() - started_at, 2)
            document = await db.get(Document, claimed_id, populate_existing=True)
            metadata = dict(document.meta_data or {})
            # A success after a retry or a pause: the earlier problem no
            # longer describes this document.
            for stale_key in ("error", "failed_at_seconds", "idle_pauses"):
                metadata.pop(stale_key, None)
            metadata.update(
                {
                    # --- how it was chunked ---
                    "chunk_count": state["total"],
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    # --- how it was embedded ---
                    "embedding_model": model_name,
                    "embedding_provider": settings.EMBEDDING_PROVIDER,
                    "embedding_dimension": settings.EMBEDDING_DIMENSION,
                    # --- performance ---
                    "processing_seconds": elapsed,
                    "attempts": document.attempts,
                    "progress": {"stage": "completed", "embedded": state["total"], "total": state["total"]},
                }
            )
            # Reassigning the whole dictionary (rather than mutating it in
            # place) is required for SQLAlchemy to notice that a JSONB column
            # changed. An in-place `metadata["x"] = 1` would not be saved.
            document.meta_data = metadata
            document.status = "completed"
            document.error_message = None
            document.resume_after = None

            # WEBHOOK "document.processed" (off unless WEBHOOKS_ENABLED):
            # staged in this same transaction, sent only after the commit.
            await _emit_document_event(
                db, document, "document.processed",
                {"chunk_count": state["total"], "processing_seconds": elapsed},
            )

            # The status flips in this one commit. Until it lands, search
            # cannot see a single chunk of this document.
            await db.commit()

            logger.info(
                f"5/5 DONE: {document.filename} -> "
                f"{state['total']} chunks in {elapsed}s. Now searchable."
            )
            return "completed"

        except EmbeddingRateLimited as exc:
            # Not a failure: the quota will come back. Keep everything and
            # come back later.
            return await _pause(db, claimed_id, str(exc), exc.retry_after, state)

        except SoftTimeLimitExceeded as exc:
            # Celery's time limit, mid-embedding, on a huge document: the
            # chunks so far are safe, so pause and continue in a fresh task
            # instead of failing work that was going fine.
            if state["stage"] == "embedding":
                return await _pause(
                    db, claimed_id, "Paused at the worker's time limit.", 0, state,
                    made_progress=True, immediate=True,
                )
            return await _handle_failure(
                db, claimed_id, exc, started_at, final_attempt, scanned_by=progress.get("malware_scan")
            )

        except asyncio.CancelledError:
            # The API is shutting down (a deploy, the free host going to
            # sleep) with this job half done. Record it as paused and due
            # NOW, so the next start resumes it at once instead of waiting
            # INGESTION_STALE_AFTER_MINUTES to notice it was abandoned.
            await asyncio.shield(
                _pause(
                    db, claimed_id, "Interrupted by a server restart.", 0, state,
                    made_progress=True, immediate=True,
                )
            )
            raise

        except Exception as exc:
            return await _handle_failure(
                db,
                claimed_id,
                exc,
                started_at,
                final_attempt,
                scanned_by=progress.get("malware_scan"),
            )


async def _extract_and_chunk(
    db, document, metadata, signature, progress, state, chunk_strategy, chunk_size, chunk_overlap
) -> dict:
    """
    PHASE A -- stages 1 to 4: fetch, scan, extract, chunk, save the text.

    Streams: a few pages are read and chunked on the job's thread, their
    chunks inserted and committed here, and the next few pages read --
    so neither the document's text nor its chunks are ever all in memory.
    Returns the document's updated meta_data.
    """
    state["stage"] = "extracting"
    # ==========================================================
    # STAGE 4 (first): clear any previous chunks
    # ==========================================================
    # A document can legitimately be processed more than once -- after a
    # failure in extraction, after changing the chunk settings, on re-index.
    # Deleting first makes re-running SAFE rather than doubling the data.
    # One bulk DELETE; the database cascades it to the embeddings, so
    # nothing is loaded into memory to do it.
    await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    for stale_key in ("chunking_complete", "chunk_count"):
        metadata.pop(stale_key, None)
    metadata["chunking"] = signature
    metadata["progress"] = {"stage": "extracting", "pages_done": 0}
    document.meta_data = dict(metadata)
    await db.commit()

    logger.info(
        f"1/5 Fetching, scanning and extracting text "
        f"(type: {document.file_type}, storage: {document.storage_backend})..."
    )
    # The heavy steps are blocking code, so they run on a thread. With
    # INGESTION_MODE=inline this job runs inside the API's event loop, and
    # without the thread one large PDF would freeze every other request.
    chunker = StreamingChunker(chunk_strategy, chunk_size, chunk_overlap)
    job = _ExtractionJob(document.storage_backend, document.file_path, document.file_type, chunker)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="docminds-extract")
    total_chunks = 0
    try:
        verdict = await _run_on(executor, job.open, progress)
        logger.info(
            f"2/5 Chunking as it reads (strategy: {chunk_strategy}, "
            f"size: {chunk_size} tokens, overlap: {chunk_overlap})..."
        )
        while True:
            chunks, read = await _run_on(executor, job.next_batch, _SEGMENTS_PER_BATCH)
            if read == 0:
                break
            if chunks:
                # One INSERT for the whole batch. The ids are generated HERE
                # in Python, as before, rather than by the database.
                await db.execute(
                    insert(Chunk),
                    [
                        {
                            "id": uuid.uuid4(),
                            "document_id": document.id,
                            "content": item["content"],
                            "chunk_index": item["chunk_index"],
                            "page_number": item["page_number"],
                            "meta_data": item["meta_data"],
                        }
                        for item in chunks
                    ],
                )
                total_chunks += len(chunks)
            # Progress for the UI, and the heartbeat that tells recovery this
            # run is alive however long the document takes.
            metadata["progress"] = {
                "stage": "extracting",
                "pages_done": job.segments_read,
                "pages_total": job.info.get("page_count"),
                "chunks": total_chunks,
            }
            document.meta_data = dict(metadata)
            document.processing_started_at = _utcnow()
            await db.commit()
            del chunks
    finally:
        try:
            await _run_on(executor, job.close)
        finally:
            executor.shutdown(wait=False)

    if not job.has_text:
        raise ValueError(
            "No text could be extracted. The file may be empty, "
            "corrupted, or an image with no readable text."
        )
    if total_chunks == 0:
        raise ValueError("Chunking produced no chunks from the extracted text.")

    language = await asyncio.to_thread(job.language)
    logger.info(
        f"Extracted {job.char_count:,} characters into {total_chunks} chunks "
        f"(language: {language}, OCR used: {job.ocr_used})"
    )

    # Phase A is complete and durable: from here a rerun resumes at
    # embedding. The extraction facts are saved now, because a resumed run
    # never sees the file again.
    metadata.update(job.info)
    metadata.update(
        {
            # --- content facts ---
            "language": language,
            "needs_ocr": bool(job.ocr_used or job.info.get("needs_ocr")),
            "word_count": job.word_count,
            "char_count": job.char_count,
            "chunk_count": total_chunks,
            # --- safety ---
            "malware_scan": verdict.scanner,
            # --- resumability ---
            "chunking": signature,
            "chunking_complete": True,
            "progress": {"stage": "embedding", "embedded": 0, "total": total_chunks},
        }
    )
    document.meta_data = dict(metadata)
    document.processing_started_at = _utcnow()
    await db.commit()
    return metadata


async def _embed_pending_chunks(db, document, metadata, model_name, state) -> None:
    """
    PHASE B -- stages 5 and 6: embed every chunk that has no vector yet for
    the current model, INGESTION_EMBED_BATCH_SIZE at a time, committing each
    batch. Safe to run any number of times; it only ever does what is left.
    """
    document_id = document.id
    chunk_ids = select(Chunk.id).where(Chunk.document_id == document_id)

    # Vectors from ANOTHER model are useless to search (it only compares the
    # current model's) -- drop them rather than keep them forever.
    await db.execute(
        delete(ChunkEmbedding).where(
            ChunkEmbedding.chunk_id.in_(chunk_ids), ChunkEmbedding.model_name != model_name
        )
    )
    has_vector = exists().where(
        ChunkEmbedding.chunk_id == Chunk.id, ChunkEmbedding.model_name == model_name
    )
    total = (await db.execute(select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id))).scalar_one()
    done = (
        await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id, has_vector)
        )
    ).scalar_one()
    state.update(total=total, embedded=done, embedded_at_start=done)
    # End the read transaction: nothing may sit "idle in transaction" while
    # an embedding call waits minutes on a rate limit.
    await db.commit()

    logger.info(
        f"3/5 Embedding {total - done} of {total} chunks via '{settings.EMBEDDING_PROVIDER}' "
        f"(model: {model_name}; {done} already done)..."
    )

    loop = asyncio.get_running_loop()

    def listener(seconds: float, reason: str) -> None:
        """Called on the embedding thread when the provider makes us wait."""
        update = {
            "stage": "waiting",
            "reason": reason,
            "embedded": state["embedded"],
            "total": state["total"],
            "until": (_utcnow() + datetime.timedelta(seconds=seconds)).isoformat(),
        }
        future = asyncio.run_coroutine_threadsafe(_record_progress(document_id, update), loop)
        try:
            future.result(timeout=10)
        except Exception:  # pragma: no cover - best effort
            pass

    batch_size = max(1, settings.INGESTION_EMBED_BATCH_SIZE)
    while True:
        rows = (
            await db.execute(
                select(Chunk.id, Chunk.content)
                .where(Chunk.document_id == document_id, ~has_vector)
                .order_by(Chunk.chunk_index)
                .limit(batch_size)
            )
        ).all()
        await db.commit()
        if not rows:
            break

        texts = [row.content for row in rows]
        vectors = await asyncio.to_thread(_embed_texts, texts, listener)

        # Sanity check. If the counts disagree, pairing chunk[i] with
        # vector[i] would attach the wrong meaning to the wrong text --
        # a silent corruption that would be very hard to diagnose later.
        if len(vectors) != len(rows):
            raise ValueError(
                f"Embedding count mismatch: got {len(vectors)} vectors for {len(rows)} chunks."
            )
        await db.execute(
            insert(ChunkEmbedding),
            [
                {"id": uuid.uuid4(), "chunk_id": row.id, "embedding": vector, "model_name": model_name}
                for row, vector in zip(rows, vectors, strict=True)
            ],
        )
        state["embedded"] += len(rows)

        # The batch, the progress line and the heartbeat land together.
        fresh = await db.get(Document, document_id, populate_existing=True)
        progress_meta = dict(fresh.meta_data or {})
        progress_meta["progress"] = {"stage": "embedding", "embedded": state["embedded"], "total": total}
        fresh.meta_data = progress_meta
        fresh.processing_started_at = _utcnow()
        await db.commit()
        logger.info(f"4/5 Embedded {state['embedded']}/{total} chunks.")
        del rows, texts, vectors


async def _pause(
    db,
    document_id,
    reason: str,
    retry_after: Optional[float],
    state: dict,
    *,
    made_progress: Optional[bool] = None,
    immediate: bool = False,
) -> str:
    """
    PAUSE: the document waits for its quota instead of failing.

    Everything committed so far stays -- every chunk, every vector. The
    document goes back to "pending" with `resume_after` set, and the
    recovery sweep (Celery beat, or the API's own sweep in inline mode)
    dispatches it again once that time has passed.

    How long: what the provider asked for, or INGESTION_PAUSE_SECONDS --
    doubling each time a resume makes NO progress at all, up to
    INGESTION_PAUSE_MAX_SECONDS. A resume that embeds even one chunk resets
    the count, and resets `attempts` too: a document that keeps moving
    forward is never given up on. Only INGESTION_MAX_IDLE_PAUSES resumes in a
    row without a single chunk embedded fail it -- by then the quota has
    been empty for about a day.
    """
    if made_progress is None:
        made_progress = state.get("embedded", 0) > state.get("embedded_at_start", 0)
    try:
        await db.rollback()
        document = await db.get(Document, document_id, populate_existing=True)
        if document is None:
            return "skipped"
        metadata = dict(document.meta_data or {})
        idle = 0 if made_progress else int(metadata.get("idle_pauses", 0)) + 1
        embedded, total = state.get("embedded", 0), state.get("total", 0)

        if idle > settings.INGESTION_MAX_IDLE_PAUSES:
            message = (
                f"The embedding provider kept refusing requests ({reason}) and no progress "
                f"was made after {idle - 1} pauses. {embedded} of {total} chunks are embedded; "
                "Re-index resumes from there once the quota is available again."
            )
            metadata["error"] = message
            metadata["progress"] = {"stage": "failed", "embedded": embedded, "total": total}
            document.meta_data = metadata
            document.error_message = message
            document.status = "failed"
            document.resume_after = None
            await _emit_document_event(db, document, "document.failed", {"error": message})
            await db.commit()
            return "failed"

        if immediate:
            delay = 0.0
        else:
            backoff = min(
                settings.INGESTION_PAUSE_MAX_SECONDS,
                settings.INGESTION_PAUSE_SECONDS * 2 ** max(0, idle - 1),
            )
            delay = max(float(retry_after or 0.0), float(backoff))
        resume_at = _utcnow() + datetime.timedelta(seconds=delay)
        message = (
            f"Waiting for the embedding rate limit: {embedded} of {total} chunks done. "
            f"Resumes automatically at {resume_at.strftime('%H:%M')} UTC."
            if delay > 0
            else f"{reason} {embedded} of {total} chunks done; resuming."
        )
        metadata["idle_pauses"] = idle
        metadata["progress"] = {
            "stage": "paused",
            "embedded": embedded,
            "total": total,
            "resume_at": resume_at.isoformat(),
            "reason": reason[:300],
        }
        document.meta_data = metadata
        document.status = "pending"
        document.processing_started_at = None
        document.resume_after = resume_at
        document.error_message = message
        if made_progress:
            document.attempts = 0
        await db.commit()
        logger.warning(f"Paused document {document_id}: {message}")
        return "paused"
    except Exception as exc:
        # Could not record the pause (the database is what is down). The row
        # stays "processing" and the stale-claim recovery picks it up.
        logger.warning(f"Could not record the pause: {exc}")
        return "paused"


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

        # Keep the progress counts ("340 of 1,200 embedded") but say it stopped,
        # so the UI can tell the user Re-index will continue from there.
        if isinstance(error_metadata.get("progress"), dict):
            error_metadata["progress"] = {
                **error_metadata["progress"],
                "stage": "retrying" if retrying else "failed",
            }
        document.meta_data = error_metadata
        document.error_message = message
        document.resume_after = None
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

    Runs on the API's own event loop (see InlineQueue), so the waits between
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


class InlineQueue:
    """
    An in-process job queue for INGESTION_MODE=inline: the API's own worker.

    WHY NOT FastAPI's BackgroundTasks
    ---------------------------------
    A background task runs as part of the request that created it. Every
    upload got its OWN task, so five uploads meant five large PDFs being
    extracted at once -- five times the memory on a 512 MB host -- and each
    job's life was tied to the request machinery that spawned it.

    Instead the upload endpoint drops the document id on this queue and
    returns 202 at once. INGESTION_INLINE_CONCURRENCY worker tasks (1 by
    default) take ids off it one at a time, on the application's event loop,
    entirely independent of the request that queued them.

        upload  -> submit(id) -> 202
                        |
                [ queue: id3, id2 ]  -> worker -> run_inline_ingestion(id1)

    A document already queued or running is not queued twice (recovery and a
    re-index can both ask for the same one). The workers are created lazily
    on the running loop, so the same object works in the API and in tests,
    where every test has its own loop. The lifespan in main.py stops them on
    shutdown; a job interrupted there records itself as paused-and-due (see
    `async_process_document`), and the startup recovery continues it.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._workers: list = []
        self._active: set = set()

    def _ensure_started(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._workers:
            return
        self._loop = loop
        self._queue = asyncio.Queue()
        self._active = set()
        self._workers = [
            loop.create_task(self._work(), name=f"docminds-inline-ingestion-{n}")
            for n in range(max(1, settings.INGESTION_INLINE_CONCURRENCY))
        ]

    def submit(self, document_id: str, *, force: bool = False) -> bool:
        """Queue a document; False if it is already queued or running."""
        self._ensure_started()
        document_id = str(document_id)
        if document_id in self._active:
            return False
        self._active.add(document_id)
        self._queue.put_nowait((document_id, force))
        return True

    async def _work(self) -> None:
        while True:
            document_id, force = await self._queue.get()
            try:
                await run_inline_ingestion(document_id, force=force)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - the pipeline records its own failures
                logger.error(f"Inline ingestion crashed for {document_id}: {exc!r}")
            finally:
                self._active.discard(document_id)
                self._queue.task_done()

    async def join(self) -> None:
        """Wait until every queued document has been processed (tests, shutdown)."""
        if self._queue is not None and self._loop is asyncio.get_running_loop():
            await self._queue.join()

    def pending(self) -> int:
        return len(self._active)

    async def stop(self) -> None:
        """Cancel the workers (the API is shutting down)."""
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)


# One queue per process.
inline_queue = InlineQueue()


async def wait_for_inline_jobs() -> None:
    """Block until the inline queue is idle. Used by tests after an upload."""
    await inline_queue.join()


def dispatch(document_id: str) -> None:
    """
    Queue a document for processing in whichever way INGESTION_MODE says.

    Must be called from inside a running event loop in inline mode (the API's
    own), which is where the endpoints and recovery call it from.
    """
    if settings.INGESTION_MODE == "celery":
        process_document_task.delay(str(document_id))
    else:
        inline_queue.submit(str(document_id))


async def submit_inline(document_id: str) -> None:
    """`dispatch` for inline mode as a coroutine, so FastAPI runs it on the
    event loop (a plain function background task would run on a thread)."""
    inline_queue.submit(str(document_id))


async def inline_sweeper() -> None:
    """
    The recovery sweep, on a timer, for INGESTION_MODE=inline.

    With Celery, beat runs `recover_stuck_documents` every few minutes.
    Inline mode has no beat, so the API runs this loop itself (started by the
    lifespan in main.py). Every INGESTION_INLINE_SWEEP_SECONDS it resumes
    paused documents whose time has come and re-queues orphaned ones.
    """
    while True:
        await asyncio.sleep(max(5, settings.INGESTION_INLINE_SWEEP_SECONDS))
        try:
            await recover_stuck_documents()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Inline recovery sweep failed; trying again later: {exc!r}")


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

    It also RESUMES paused documents (status "pending" with `resume_after`
    in the past): a rate-limit pause, or a job the API's shutdown
    interrupted. Those are re-queued as soon as they are due, whatever their
    age -- and a paused document NOT yet due is left alone.

    Returns {"requeued": [ids], "failed": [ids], "resumed": [ids]}
    ("resumed" is the subset of "requeued" that was paused).
    """
    current = now or _utcnow()
    cutoff = _stale_cutoff(current)
    requeued: list[str] = []
    failed: list[str] = []
    resumed: list[str] = []

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
                    # A lost queue message -- but not a PAUSED document
                    # still waiting for its time to come.
                    and_(
                        Document.status == "pending",
                        Document.updated_at < cutoff,
                        or_(Document.resume_after.is_(None), Document.resume_after <= current),
                    ),
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
                document.resume_after = None
                requeued.append(str(document.id))

        # PAUSED documents whose time has come (a rate-limit pause, or a job
        # interrupted by a restart). Their attempts were reset when they
        # paused, so they are never "out of attempts" here.
        due = await db.execute(
            select(Document).where(
                Document.status == "pending",
                Document.resume_after.is_not(None),
                Document.resume_after <= current,
            )
        )
        for document in due.scalars().all():
            document.resume_after = None
            if str(document.id) not in requeued:
                requeued.append(str(document.id))
                resumed.append(str(document.id))
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
        logger.warning(
            f"Re-queued {len(requeued)} ({len(resumed)} resumed after a pause) "
            f"and failed {len(failed)} stuck document(s)."
        )
    return {"requeued": requeued, "failed": failed, "resumed": resumed}


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
