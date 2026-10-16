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

THE SEVEN STAGES OF THE PIPELINE
--------------------------------
      file on disk
          |
      1.  validate           does the file still exist?
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
import os
import time
import uuid

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.services.chunker import ChunkerService
from app.services.embedding import EmbeddingService
from app.services.extractor import DocumentExtractor


async def async_process_document(document_id: str) -> None:
    """
    Run the full ingestion pipeline for one document.

    This is where a raw file becomes searchable knowledge. It is `async`
    because every database operation is awaited.
    """
    # Measure total processing time, so we can store it as a metric and show
    # "processed in 4.2s" in the UI.
    started_at = time.perf_counter()

    # `async with` opens a database session and guarantees it is closed again
    # even if an exception is raised part-way through.
    async with SessionLocal() as db:

        # ==============================================================
        # STAGE 0: Load the document record
        # ==============================================================
        statement = select(Document).where(Document.id == document_id)
        result = await db.execute(statement)
        document = result.scalar_one_or_none()

        if not document:
            # The document was deleted between the upload and the worker
            # picking up the job. Nothing to do -- exit quietly rather than
            # crashing the worker.
            print(f"[Ingestion] Document '{document_id}' no longer exists. Skipping.")
            return

        print(f"[Ingestion] === Starting pipeline for: {document.filename} ===")

        # Mark it as in-progress and commit immediately, so the frontend's
        # polling sees "processing" right away rather than sitting on "pending"
        # for the whole duration.
        document.status = "processing"
        await db.commit()

        try:
            # ==========================================================
            # STAGE 1: Validate the file is still on disk
            # ==========================================================
            if not os.path.exists(document.file_path):
                # `raise` jumps to the `except` block at the bottom, which
                # records the failure on the document row.
                raise FileNotFoundError(
                    f"Uploaded file is missing from storage: {document.file_path}"
                )

            # ==========================================================
            # STAGE 2: EXTRACT -- turn the file into plain text
            # ==========================================================
            # extractor.py inspects the file type and dispatches to the right
            # library: PyMuPDF for PDF, python-docx for Word, and so on.
            # For a scanned PDF it detects the lack of a text layer and falls
            # back to OCR automatically.
            print(
                f"[Ingestion] 1/5 Extracting text "
                f"(type: {document.file_type})..."
            )
            # The three heavy steps (extraction/OCR, chunking, embedding) are
            # plain blocking code, so each runs on a thread. In the worker that
            # changes nothing; with INGESTION_MODE=inline this job runs inside
            # the API's event loop, and without the threads one large PDF would
            # freeze every other request until it finished.
            extraction = await asyncio.to_thread(
                DocumentExtractor.extract, document.file_path, document.file_type
            )

            if not extraction.text or not extraction.text.strip():
                raise ValueError(
                    "No text could be extracted. The file may be empty, "
                    "corrupted, or an image with no readable text."
                )

            print(
                f"[Ingestion]     Extracted {len(extraction.text):,} characters "
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

            print(
                f"[Ingestion] 2/5 Chunking (strategy: {chunk_strategy}, "
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

            print(f"[Ingestion]     Produced {len(chunks_data)} chunks.")

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
                print(f"[Ingestion]     Removing {len(old_chunks)} previous chunks.")
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
            print(
                f"[Ingestion] 3/5 Embedding {len(chunk_objects)} chunks "
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

            print(f"[Ingestion] 4/5 Saving vectors (model: {model_name})...")

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
                    # --- performance ---
                    "processing_seconds": elapsed,
                }
            )

            # Reassigning the whole dictionary (rather than mutating it in
            # place) is required for SQLAlchemy to notice that a JSONB column
            # changed. An in-place `metadata["x"] = 1` would not be saved.
            document.meta_data = metadata
            document.status = "completed"

            # One commit writes the chunks, the vectors and the status change
            # together as a single transaction. Either all of it lands or none
            # of it does -- there is no state where chunks exist but the
            # document still reads "processing".
            await db.commit()

            print(
                f"[Ingestion] 5/5 DONE: {document.filename} -> "
                f"{len(chunk_objects)} chunks in {elapsed}s. Now searchable."
            )

        except Exception as exc:
            # ==========================================================
            # FAILURE HANDLING
            # ==========================================================
            # Any failure marks the document as "failed" and records why, so
            # the UI can show the user a real reason instead of leaving the
            # document stuck on "processing" forever.
            print(f"[Ingestion] FAILED for {document.filename}: {exc}")

            import traceback
            traceback.print_exc()

            # The session may be in a broken state after an error mid-write,
            # so roll back before attempting to save the failure status.
            await db.rollback()

            error_metadata = dict(document.meta_data) if document.meta_data else {}
            error_metadata["error"] = str(exc)
            error_metadata["failed_at_seconds"] = round(
                time.perf_counter() - started_at, 2
            )

            document.meta_data = error_metadata
            document.status = "failed"
            await db.commit()


# ----------------------------------------------------------------------
# THE CELERY TASK ITSELF
# ----------------------------------------------------------------------


@celery_app.task(name="app.tasks.ingestion.process_document_task")
def process_document_task(document_id: str) -> None:
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

    `asyncio.run()` is the bridge between Celery's synchronous world and our
    asynchronous database code: it creates a fresh event loop, runs the async
    pipeline to completion inside it, and then closes it.
    """
    asyncio.run(async_process_document(document_id))
