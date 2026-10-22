"""
documents.py
============
WHAT THIS FILE DOES
-------------------
Endpoints for getting files into the system and back out again.

    POST /documents/upload              upload one or more files (editor+)
    GET  /documents/?project_id=...     list a project's documents + status
    GET  /documents/{id}/preview        download the original file

THE MOST IMPORTANT THING ABOUT THE UPLOAD ENDPOINT
--------------------------------------------------
It returns `202 Accepted`, not `201 Created`, and that status code is telling
the truth about what happened.

Processing a document takes seconds to minutes -- extraction, possibly OCR,
chunking, embedding. If that ran here, the browser would sit waiting and
eventually time out on a large scan.

So this endpoint does only the fast part:

    1. validate the project and the caller's access to it
    2. VALIDATE every file: size (413), type and content (415), safe name
    3. hash the bytes and check for a duplicate
    4. work out the version number
    5. store the file (local disk or S3) under a server-chosen key
    6. create a Document row with status "pending"
    7. commit, THEN drop a job id onto the Redis queue
    8. reply immediately

A Celery worker then does the real work (see tasks/ingestion.py), and the
frontend polls the list endpoint until `status` reads "completed".

202 means exactly that: "I have accepted this, but it is not finished yet."

TWO FEATURES WORTH UNDERSTANDING
--------------------------------
DEDUPLICATION -- every file is SHA-256 hashed. Because the hash is of the
CONTENT, a renamed copy is still recognised, so the expensive OCR and embedding
work is never repeated for a file the project already has.

VERSIONING -- uploading a file whose NAME already exists stores it as version
2, 3 and so on rather than overwriting. Nothing is silently lost.

WHAT THE UPLOAD REFUSES, AND WITH WHICH STATUS
----------------------------------------------
    400  no files' worth of usable names, an empty file, too many files
    413  a file over MAX_UPLOAD_SIZE_BYTES -- or a whole request over the
         ceiling, which middleware in main.py refuses before it is parsed
    415  an extension extractor.py cannot read, or bytes that do not match
         the extension (a renamed .exe called report.pdf)
    422  chunking options out of range (an unknown strategy, a chunk size
         outside 50-4000 tokens, an overlap not smaller than the size)
    503  the storage backend is unreachable

One bad file refuses the whole request, and nothing is stored: every file is
validated before any of them is written. See services/uploads.py for the
checks and services/storage.py for where the bytes go.

WHAT THE PREVIEW REFUSES
------------------------
    409  malware scanning is on and this file has not been cleared by it yet
         (still queued, or it failed before the scan could finish)
    410  the scan found malware; the file was deleted ("quarantined")
"""

import asyncio
import logging
import mimetypes
import os
import uuid
from typing import List, Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.document import DocumentOut
from app.core.config import settings
from app.services import audit, uploads, usage
from app.services.storage import (
    LocalStorage,
    Storage,
    StorageError,
    StorageObjectNotFound,
    get_storage,
    new_key,
)
from app.tasks.ingestion import process_document_task, run_inline_ingestion

logger = logging.getLogger(__name__)

router = APIRouter()


# File types a browser would RUN rather than display: HTML executes its
# scripts. The preview serves them as plain text instead -- the bytes are
# exactly the same, but nothing the uploader wrote can execute in the browser
# of whoever opens it, whether directly or through a blob URL in the frontend.
_SERVE_AS_TEXT = {"html", "htm"}


def _preview_media_type(document: Document) -> str:
    """The Content-Type a preview is served with."""
    if document.file_type in _SERVE_AS_TEXT:
        return "text/plain; charset=utf-8"
    if document.file_type not in uploads.ALLOWED_EXTENSIONS:
        # Only rows from before the upload allowlist existed can get here --
        # back then any extension was stored, .svg and .xhtml (which can carry
        # script) included. Opaque bytes, to be saved rather than rendered.
        return "application/octet-stream"
    return mimetypes.guess_type(document.filename)[0] or "application/octet-stream"


def _not_yet_cleared_by_scan(document: Document) -> bool:
    """
    True when malware scanning is on and this file has not passed it.

    The scan runs in the ingestion worker, AFTER the upload is stored. Until
    it has passed, the file must not be handed to anybody: otherwise the gap
    between upload and scan -- or a scan that never happened because ClamAV
    was down until the retries ran out -- would let one user pass another a
    malicious file through the preview link.

    The pipeline records meta_data["malware_scan"] (the scanner's name) once
    a scan passes, on success and on later failures alike. Completed
    documents are served regardless: they were processed before scanning was
    switched on, and blocking the whole existing library on the day ClamAV
    is enabled would help nobody.
    """
    if settings.MALWARE_SCAN == "off":
        return False
    if (document.meta_data or {}).get("malware_scan") not in (None, "off"):
        return False
    return document.status != "completed"


@router.post(
    "/upload",
    response_model=List[DocumentOut],
    status_code=status.HTTP_202_ACCEPTED,
    # At most RATE_LIMIT_UPLOAD upload requests per user (core/rate_limit.py):
    # extraction, OCR and embedding are the heaviest work the server does.
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def upload_documents(
    # Injected by FastAPI. Used only when INGESTION_MODE is "inline".
    background_tasks: BackgroundTasks,
    # The raw request, only so the audit entry can record the caller's IP.
    request: Request,
    # `Form(...)` and `File(...)` rather than a JSON body, because file uploads
    # use multipart/form-data -- JSON cannot carry binary data.
    project_id: uuid.UUID = Form(...),
    files: List[UploadFile] = File(...),
    # Chunking options, with sensible defaults so a simple upload needs nothing
    # extra. Stashed in meta_data and read later by the worker.
    #
    # Bounded HERE, at the door, so a bad value is a 422 the caller sees at
    # once -- not a document that sits in the queue and then fails in the
    # worker with "Unsupported chunking strategy". The size is in tokens:
    # under ~50 a chunk is a sentence fragment that embeds as noise, and over
    # a few thousand it exceeds what the embedding models read at all.
    chunk_strategy: Literal["fixed_size", "sentence", "markdown"] = Form("fixed_size"),
    chunk_size: int = Form(500, ge=50, le=4000),
    chunk_overlap: int = Form(50, ge=0, le=1000),
    db: AsyncSession = Depends(deps.get_db),
    # Uploading changes what everyone in the organization searches, so it
    # needs the editor role (Manager) or above. Viewers get 403.
    current_user: User = Depends(deps.require_editor),
):
    """
    Accept one or more files and queue them for background processing.

    Returns immediately with the created Document rows, each showing
    status "pending".
    """
    # The one rule about the chunking options that involves two of them, so
    # it cannot be written as a bound on either. An overlap as large as the
    # chunk would mean the window never moves forward.
    if chunk_overlap >= chunk_size:
        raise HTTPException(
            # A bare 422: Starlette renamed its constant for this status
            # (..._ENTITY -> ..._CONTENT), and the number means the same in both.
            status_code=422,
            detail="chunk_overlap must be smaller than chunk_size.",
        )

    # ------------------------------------------------------------------
    # 1. TENANT CHECK -- does this project exist, and is it MINE?
    # ------------------------------------------------------------------
    statement = select(Project).where(Project.id == project_id)
    result = await db.execute(statement)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The specified project was not found.",
        )

    # The second half of this check is what stops one organization uploading
    # into another's project -- and thereby reading its documents back out
    # through search.
    if project.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to upload to this project.",
        )

    if len(files) > settings.MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {settings.MAX_FILES_PER_UPLOAD} files can be uploaded at once.",
        )

    # ------------------------------------------------------------------
    # 2. VALIDATE EVERY FILE BEFORE STORING ANY OF THEM
    # ------------------------------------------------------------------
    # Each file is copied to a temporary file in 1 MB chunks, counted
    # (413 the moment it is too big), hashed, and content-sniffed (415 if
    # the bytes do not match the extension). See services/uploads.py.
    #
    # All files are checked FIRST, so one bad file in a batch of ten is
    # refused before anything has been stored or written to the database --
    # the request either succeeds as a whole or changes nothing.
    spooled: list[uploads.SpooledUpload] = []
    stored: list[tuple[Storage, str]] = []
    try:
        for file in files:
            try:
                spooled.append(
                    await uploads.spool_upload(file, settings.MAX_UPLOAD_SIZE_BYTES)
                )
            except uploads.UploadRejected as rejected:
                raise HTTPException(
                    status_code=rejected.status_code, detail=rejected.message
                ) from rejected

        # What the organization already holds against its document and
        # storage quotas (services/usage.py). Each accepted file is admitted
        # BEFORE it is stored; a file that does not fit raises QuotaExceeded
        # (402), and the cleanup below removes anything stored earlier in
        # this request -- so a refused upload changes nothing at all.
        allowance = await usage.upload_allowance(db, current_user.org_id)

        storage = get_storage()
        processed_docs = []
        skipped_duplicates = []
        # Hashes accepted earlier in THIS request. The database check below
        # cannot see them yet in every case, and two identical files in one
        # upload would otherwise collide on the (project, hash) unique index.
        seen_hashes: set[str] = set()

        for item in spooled:
            # --------------------------------------------------------------
            # 3. DEDUPLICATION
            # --------------------------------------------------------------
            # SHA-256 of the CONTENT, so a renamed copy is still detected.
            statement_dup = select(Document).where(
                Document.project_id == project_id,
                Document.duplicate_hash == item.sha256,
            )
            result_dup = await db.execute(statement_dup)

            if item.sha256 in seen_hashes or result_dup.scalar_one_or_none():
                # Already present in THIS project. Skip it rather than
                # repeating the expensive OCR and embedding work.
                logger.info("Skipping duplicate file", extra={"upload_filename": item.filename})
                skipped_duplicates.append(item.filename)
                continue
            seen_hashes.add(item.sha256)

            # Duplicates were skipped above, so they never count.
            allowance.admit(item.size)

            # --------------------------------------------------------------
            # 4. VERSIONING
            # --------------------------------------------------------------
            # Same name, different contents (the hash check above already
            # ruled out an identical file) means this is a revision. Find the
            # highest version so far and add one. The name compared is the
            # SANITIZED one, which is also the one stored.
            statement_version = select(func.max(Document.version)).where(
                Document.project_id == project_id,
                Document.filename == item.filename,
            )
            result_version = await db.execute(statement_version)
            # `or 0` because max() returns NULL when there are no matching
            # rows, which arrives in Python as None.
            max_version = result_version.scalar() or 0
            new_version = max_version + 1

            # --------------------------------------------------------------
            # 5. STORE THE BYTES
            # --------------------------------------------------------------
            # Under a key the server invents -- "<project>/<uuid>.<ext>" --
            # never the client's filename (see services/storage.py). On a
            # thread, because copying 50 MB or uploading it to S3 blocks.
            key = new_key(project_id, item.extension)
            try:
                await asyncio.to_thread(storage.save, key, item.path)
            except StorageError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="File storage is temporarily unavailable. Please try again.",
                ) from exc
            stored.append((storage, key))

            # --------------------------------------------------------------
            # 6. CREATE THE DATABASE RECORD
            # --------------------------------------------------------------
            document = Document(
                filename=item.filename,
                file_type=item.extension,
                file_size=item.size,
                file_path=key,
                storage_backend=storage.name,
                # The worker moves this through processing -> completed | failed.
                status="pending",
                duplicate_hash=item.sha256,
                version=new_version,
                meta_data={
                    "original_filename": item.filename,
                    # The worker reads these back when it chunks the document.
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                },
                project_id=project_id,
            )
            db.add(document)
            # `flush` assigns document.id without committing yet.
            await db.flush()

            # Staged in the same transaction as the Document row, so the log
            # can never claim an upload that was rolled back.
            await audit.record(
                db, "document.upload", org_id=current_user.org_id, user_id=current_user.id,
                request=request,
                details={
                    "document_id": document.id,
                    "project_id": project_id,
                    "filename": item.filename,
                    "file_size": item.size,
                    "version": new_version,
                },
            )

            processed_docs.append(document)

        # Every file was a duplicate, so there is nothing to report on.
        if not processed_docs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No files processed. All uploaded files were detected as "
                    f"duplicates: {', '.join(skipped_duplicates)}"
                ),
            )

        await db.commit()
    except BaseException:
        # Anything stored before the failure has no database row pointing at
        # it, and never will. Remove it rather than leave orphans behind.
        for used_storage, key in stored:
            try:
                await asyncio.to_thread(used_storage.delete, key)
            except Exception as cleanup_error:  # pragma: no cover - best effort
                logger.warning(
                    "Could not remove orphaned object",
                    extra={"storage_key": key, "error": repr(cleanup_error)},
                )
        raise
    finally:
        for item in spooled:
            item.discard()

    for document in processed_docs:
        await db.refresh(document)

    # ------------------------------------------------------------------
    # 7. QUEUE THE BACKGROUND JOBS -- after the commit
    # ------------------------------------------------------------------
    # After, not before: a worker that picked the job up before the commit
    # landed would find no row to claim and drop it.
    #
    # `.delay()` does NOT run the function. It serialises the argument,
    # pushes a message onto Redis and returns instantly. A worker process
    # picks it up and runs the real pipeline there.
    for document in processed_docs:
        if settings.INGESTION_MODE == "celery":
            try:
                process_document_task.delay(str(document.id))
            except Exception as exc:
                # Redis is down. The upload itself succeeded and is safely
                # stored as "pending"; the recovery sweep re-queues it once
                # the queue is back, so the user is not told it failed.
                logger.warning(
                    "Could not queue document; the recovery sweep will retry",
                    extra={"document_id": str(document.id), "error": repr(exc)},
                )
        else:
            # INGESTION_MODE=inline: runs after the response is sent, so the
            # upload still returns at once with status "pending" and the
            # frontend's polling works unchanged.
            background_tasks.add_task(run_inline_ingestion, str(document.id))

    return processed_docs


@router.get("/", response_model=List[DocumentOut])
async def list_documents(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    List a project's documents, newest first.

    THE FRONTEND POLLS THIS ENDPOINT. Because ingestion happens in a background
    worker, this is how the browser discovers that a document has finished --
    it re-fetches every few seconds until `status` reads "completed", then
    stops.

    The `meta_data` on each row is what populates the chunk counts, word counts,
    OCR flag and processing time shown in the document list.
    """
    statement = select(Project).where(Project.id == project_id)
    result = await db.execute(statement)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )
    if project.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project's documents.",
        )

    statement_docs = (
        select(Document)
        .where(Document.project_id == project_id)
        # Newest first, so a document just uploaded appears at the top where
        # the user is looking for it.
        .order_by(Document.created_at.desc())
    )
    result_docs = await db.execute(statement_docs)
    return result_docs.scalars().all()


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Download or view the ORIGINAL uploaded file.

    Useful for verifying a citation: read the AI's answer, then open the source
    document and confirm it for yourself.

    Note the access check goes through the document's PROJECT. A document has
    no `org_id` of its own -- ownership is always established by walking up the
    chain, exactly as the vector search does.
    """
    statement = select(Document).where(Document.id == document_id)
    result = await db.execute(statement)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    statement_project = select(Project).where(Project.id == document.project_id)
    result_project = await db.execute(statement_project)
    project = result_project.scalar_one_or_none()

    if not project or project.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to preview this document.",
        )

    # A file the malware scan rejected has had its bytes deleted; say so
    # plainly instead of a confusing "not found".
    if (document.meta_data or {}).get("quarantined"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This file was quarantined by the malware scan and cannot be downloaded.",
        )

    # With scanning on, nothing is served until the scan has cleared it.
    if _not_yet_cleared_by_scan(document):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This file has not passed the malware scan yet. Try again once "
                "it has finished processing."
            ),
        )

    storage = get_storage(document.storage_backend)
    media_type = _preview_media_type(document)
    headers = {
        # Never let a browser second-guess the type and render, say, an
        # uploaded .html file as a live page on this API's origin.
        "X-Content-Type-Options": "nosniff",
    }

    # LOCAL DISK: `FileResponse` streams the file rather than loading it all
    # into memory, supports HTTP range requests (which PDF viewers use to
    # fetch one page at a time), and `filename=` sets the name the browser
    # saves it under -- the original, not the storage key.
    if isinstance(storage, LocalStorage):
        path = storage.path_for(document.file_path)
        if not os.path.isfile(path):
            # The row can outlive the file -- most commonly on a cloud host
            # with an ephemeral disk, where uploads are wiped on restart.
            # Search still works in that situation, because the chunks and
            # vectors live in Postgres; only the original download is
            # unavailable, so we say so clearly.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File has been deleted from server storage.",
            )
        return FileResponse(
            path, filename=document.filename, media_type=media_type, headers=headers
        )

    # OBJECT STORAGE: streamed through the API in 1 MB pieces. Proxying,
    # rather than redirecting the browser to a pre-signed S3 URL, keeps the
    # bucket private and needs no CORS rules on it -- and the ownership check
    # above stays the only way in.
    try:
        chunks = await asyncio.to_thread(storage.iter_bytes, document.file_path)
    except StorageObjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has been deleted from server storage.",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is temporarily unavailable. Please try again.",
        ) from exc

    # RFC 6266 / 5987: `filename*` carries a UTF-8 name safely, whatever
    # characters it contains.
    headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(document.filename)}"
    )
    # A plain (synchronous) iterator: Starlette runs it on a worker thread,
    # so the blocking S3 reads never stall the event loop.
    return StreamingResponse(chunks, media_type=media_type, headers=headers)
