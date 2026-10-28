"""This is the document processing pipeline -- everything that happens to a file AFTER it is uploaded and..."""
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
from app.models import Chunk, ChunkEmbedding
from app.models import Document
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


class TransientIngestionError(Exception):
    """Raised OUT of the pipeline when a failure is worth retrying."""


class MalwareDetected(Exception):
    """The scanner found something. Permanent, and the file is quarantined."""

    def __init__(self, signature: str):
        super().__init__(f"Rejected by the malware scan: {signature}")
        self.signature = signature


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
    """Would doing the same thing again, a little later, plausibly succeed?"""
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
    return text if len(text) <= 1000 else text[:997] + "..."


def retry_delay_seconds(retries_so_far: int) -> int:
    """Exponential backoff: the base, then double it each time -- 10, 20, 40 ..."""
    return min(
        settings.INGESTION_RETRY_BACKOFF_SECONDS * 2**retries_so_far,
        settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS,
    )


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stale_cutoff(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    return (now or _utcnow()) - datetime.timedelta(minutes=settings.INGESTION_STALE_AFTER_MINUTES)


async def claim_document(db, document_id, *, force: bool = False) -> Optional[Document]:
    """Atomically move a document to "processing" and return it -- or return"""
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
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    if result.rowcount != 1:
        return None
    db.expire_all()
    return await db.get(Document, document_id)


class _ExtractionJob:
    """The blocking half of stages 1-3, driven one batch at a time."""

    def __init__(self, storage_name: str, key: str, file_type: str, chunker: StreamingChunker):
        self.storage_name = storage_name
        self.key = key
        self.file_type = file_type
        self.chunker = chunker
        self.info: dict = {}
        self.segments_read = 0
        self.ocr_used = False
        self.has_text = False
        self.word_count = 0
        self.char_count = 0
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
            self._segments.close()
        self._stack.close()


_SEGMENTS_PER_BATCH = 10


async def _run_on(executor, function, *args):
    """Run a blocking call on the given executor (the job's own thread)."""
    return await asyncio.get_running_loop().run_in_executor(executor, functools.partial(function, *args))


def _chunking_signature(strategy: str, size: int, overlap: int) -> dict:
    """What the stored chunks were made with. A resumed run reuses them only if"""
    return {"strategy": strategy, "size": size, "overlap": overlap, "version": 2}


def _embed_texts(texts: list, listener) -> list:
    """One embedding call, on a worker thread, with the wait listener installed."""
    token = wait_listener.set(listener)
    try:
        return EmbeddingService.get_embeddings(texts)
    finally:
        wait_listener.reset(token)


async def _record_progress(document_id, progress: dict) -> None:
    """Write meta_data["progress"] (and the heartbeat) from OUTSIDE the main"""
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


async def async_process_document(
    document_id: str, *, final_attempt: bool = True, force: bool = False
) -> str:
    """Run the full ingestion pipeline for one document."""
    started_at = time.perf_counter()

    async with SessionLocal() as db:
        document = await claim_document(db, document_id, force=force)

        if document is None:
            logger.warning(f"Document '{document_id}' is not claimable. Skipping.")
            return "skipped"

        claimed_id = document.id
        observability.bind(document_id=document.id, project_id=document.project_id)

        logger.info(
            f"=== Starting pipeline for: {document.filename} "
            f"(attempt {document.attempts}) ==="
        )

        progress: dict = {}
        state = {"stage": "starting", "embedded": 0, "embedded_at_start": 0, "total": 0}

        try:
            metadata = dict(document.meta_data or {})
            chunk_strategy = metadata.get("chunk_strategy") or settings.DEFAULT_CHUNK_STRATEGY
            chunk_size = int(metadata.get("chunk_size") or settings.DEFAULT_CHUNK_SIZE)
            chunk_overlap = int(metadata.get("chunk_overlap") or settings.DEFAULT_CHUNK_OVERLAP)
            signature = _chunking_signature(chunk_strategy, chunk_size, chunk_overlap)
            model_name = EmbeddingService.current_model_name()

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

            state["stage"] = "embedding"
            await _embed_pending_chunks(db, document, metadata, model_name, state)

            elapsed = round(time.perf_counter() - started_at, 2)
            document = await db.get(Document, claimed_id, populate_existing=True)
            metadata = dict(document.meta_data or {})
            for stale_key in ("error", "failed_at_seconds", "idle_pauses"):
                metadata.pop(stale_key, None)
            metadata.update(
                {
                    "chunk_count": state["total"],
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "embedding_model": model_name,
                    "embedding_provider": settings.EMBEDDING_PROVIDER,
                    "embedding_dimension": settings.EMBEDDING_DIMENSION,
                    "processing_seconds": elapsed,
                    "attempts": document.attempts,
                    "progress": {"stage": "completed", "embedded": state["total"], "total": state["total"]},
                }
            )
            document.meta_data = metadata
            document.status = "completed"
            document.error_message = None
            document.resume_after = None

            await _emit_document_event(
                db, document, "document.processed",
                {"chunk_count": state["total"], "processing_seconds": elapsed},
            )

            await db.commit()

            logger.info(
                f"5/5 DONE: {document.filename} -> "
                f"{state['total']} chunks in {elapsed}s. Now searchable."
            )
            return "completed"

        except EmbeddingRateLimited as exc:
            return await _pause(db, claimed_id, str(exc), exc.retry_after, state)

        except SoftTimeLimitExceeded as exc:
            if state["stage"] == "embedding":
                return await _pause(
                    db, claimed_id, "Paused at the worker's time limit.", 0, state,
                    made_progress=True, immediate=True,
                )
            return await _handle_failure(
                db, claimed_id, exc, started_at, final_attempt, scanned_by=progress.get("malware_scan")
            )

        except asyncio.CancelledError:
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
    """PHASE A -- stages 1 to 4: fetch, scan, extract, chunk, save the text."""
    state["stage"] = "extracting"
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

    metadata.update(job.info)
    metadata.update(
        {
            "language": language,
            "needs_ocr": bool(job.ocr_used or job.info.get("needs_ocr")),
            "word_count": job.word_count,
            "char_count": job.char_count,
            "chunk_count": total_chunks,
            "malware_scan": verdict.scanner,
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
    """PHASE B -- stages 5 and 6: embed every chunk that has no vector yet for"""
    document_id = document.id
    chunk_ids = select(Chunk.id).where(Chunk.document_id == document_id)

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
    """PAUSE: the document waits for its quota instead of failing."""
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
        logger.warning(f"Could not record the pause: {exc}")
        return "paused"


async def _emit_document_event(db, document: Document, event_type: str, extra: dict) -> None:
    """WEBHOOKS_ENABLED; never raises -- a webhook problem must not change what"""
    if not settings.WEBHOOKS_ENABLED:
        return
    try:
        from app.models import Project
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
    """FAILURE HANDLING -- record what went wrong, and decide whether to retry."""
    transient = is_transient(exc)
    retrying = transient and not final_attempt
    message = describe_failure(exc)

    logger.log(
        logging.WARNING if retrying else logging.ERROR,
        "Transient ingestion failure; will retry" if retrying else "Ingestion failed",
        extra={"document_id": str(document_id), "error": message},
        exc_info=exc,
    )

    try:
        await db.rollback()

        document = await db.get(Document, document_id, populate_existing=True)
        if document is None:
            return "skipped"

        error_metadata = dict(document.meta_data) if document.meta_data else {}
        error_metadata["error"] = message
        error_metadata["failed_at_seconds"] = round(time.perf_counter() - started_at, 2)
        if scanned_by is not None:
            error_metadata["malware_scan"] = scanned_by

        if isinstance(exc, MalwareDetected):
            error_metadata["quarantined"] = True
            error_metadata["malware_signature"] = exc.signature
            try:
                get_storage(document.storage_backend).delete(document.file_path)
            except Exception as delete_error:  # pragma: no cover - best effort
                logger.warning(f"Could not delete quarantined file: {delete_error}")

        if isinstance(error_metadata.get("progress"), dict):
            error_metadata["progress"] = {
                **error_metadata["progress"],
                "stage": "retrying" if retrying else "failed",
            }
        document.meta_data = error_metadata
        document.error_message = message
        document.resume_after = None
        if retrying:
            document.status = "pending"
            document.processing_started_at = None
        else:
            document.status = "failed"
            await _emit_document_event(db, document, "document.failed", {"error": message})
        await db.commit()
    except Exception as record_error:
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


async def run_inline_ingestion(document_id: str, *, force: bool = False) -> str:
    """The pipeline with the same retry policy Celery applies, for"""
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
    """An in-process job queue for INGESTION_MODE=inline: the API's own worker."""

    def __init__(self, runner=None, *, name: str = "ingestion", concurrency: Optional[int] = None) -> None:
        self._runner = runner
        self._name = name
        self._concurrency = concurrency
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
        concurrency = self._concurrency or settings.INGESTION_INLINE_CONCURRENCY
        self._workers = [
            loop.create_task(self._work(), name=f"docminds-inline-{self._name}-{n}")
            for n in range(max(1, concurrency))
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
                await (self._runner or run_inline_ingestion)(document_id, force=force)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - the pipeline records its own failures
                logger.error(f"Inline {self._name} job crashed for {document_id}: {exc!r}")
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


inline_queue = InlineQueue()


async def wait_for_inline_jobs() -> None:
    """Block until the inline queue is idle. Used by tests after an upload."""
    await inline_queue.join()


def dispatch(document_id: str) -> None:
    """Queue a document for processing in whichever way INGESTION_MODE says."""
    if settings.INGESTION_MODE == "celery":
        process_document_task.delay(str(document_id))
    else:
        inline_queue.submit(str(document_id))


async def submit_inline(document_id: str) -> None:
    """`dispatch` for inline mode as a coroutine, so FastAPI runs it on the"""
    inline_queue.submit(str(document_id))


async def inline_sweeper() -> None:
    """The recovery sweep, on a timer, for INGESTION_MODE=inline."""
    while True:
        await asyncio.sleep(max(5, settings.INGESTION_INLINE_SWEEP_SECONDS))
        try:
            await recover_stuck_documents()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Inline recovery sweep failed; trying again later: {exc!r}")


async def recover_stuck_documents(*, now: Optional[datetime.datetime] = None, requeue: bool = True) -> dict:
    """Re-queue documents that have been "processing" or "pending" for longer"""
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
                    and_(
                        Document.status == "pending",
                        Document.updated_at < cutoff,
                        or_(Document.resume_after.is_(None), Document.resume_after <= current),
                    ),
                )
            )
        )
        for document in stuck.scalars().all():
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

    if requeue:
        for document_id in requeued:
            try:
                dispatch(document_id)
            except Exception as exc:
                logger.warning(f"Could not queue {document_id}; a later sweep will retry: {exc}")

    if requeued or failed:
        logger.warning(
            f"Re-queued {len(requeued)} ({len(resumed)} resumed after a pause) "
            f"and failed {len(failed)} stuck document(s)."
        )
    return {"requeued": requeued, "failed": failed, "resumed": resumed}


@celery_app.task(
    bind=True,
    name="app.tasks.ingestion.process_document_task",
    autoretry_for=(TransientIngestionError,),
    max_retries=settings.INGESTION_MAX_RETRIES,
    retry_backoff=settings.INGESTION_RETRY_BACKOFF_SECONDS,
    retry_backoff_max=settings.INGESTION_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,  # spread retries out so a recovered API is not stampeded
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.INGESTION_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.INGESTION_TIME_LIMIT_SECONDS,
)
def process_document_task(self, document_id: str, force: bool = False) -> str:
    """The job that Celery workers actually run."""
    final_attempt = self.request.retries >= self.max_retries
    try:
        return asyncio.run(
            async_process_document(document_id, final_attempt=final_attempt, force=force)
        )
    except SoftTimeLimitExceeded as exc:
        asyncio.run(mark_failed(document_id, describe_failure(exc)))
        return "failed"


@celery_app.task(name="app.tasks.ingestion.recover_stuck_documents_task")
def recover_stuck_documents_task() -> dict:
    """The periodic sweep, scheduled by Celery beat (see core/celery_app.py)."""
    return asyncio.run(recover_stuck_documents())
