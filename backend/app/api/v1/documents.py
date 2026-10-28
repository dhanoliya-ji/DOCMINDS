"""Endpoints for getting files into the system and back out again."""
import asyncio
import logging
import mimetypes
import os
import uuid
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models import Chunk
from app.models import Document
from app.models import Project
from app.models import User
from app.schemas import ChunkContextOut, DocumentOut, DocumentReindex, DocumentUpdate
from app.core.config import settings
from app.services import audit, uploads, usage
from app.services.storage import (
    LocalStorage,
    Storage,
    StorageError,
    StorageObjectNotFound,
    StorageUnavailableError,
    get_storage,
    new_key,
)
from app.tasks import ingestion


def _storage_http_error(exc: StorageError) -> HTTPException:
    """Turn a storage failure into a reply, and log the REAL reason."""
    logger.error("Storage operation failed: %s", exc)
    if isinstance(exc, StorageUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is temporarily unavailable. Please try again.",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "File storage refused the request. This is a server configuration "
            "problem (storage credentials, bucket name or permissions); the "
            "server log has the details."
        ),
    )


logger = logging.getLogger(__name__)

router = APIRouter()


_SERVE_AS_TEXT = {"html", "htm"}


def _preview_media_type(document: Document) -> str:
    """The Content-Type a preview is served with."""
    if document.file_type in _SERVE_AS_TEXT:
        return "text/plain; charset=utf-8"
    if document.file_type not in uploads.ALLOWED_EXTENSIONS:
        return "application/octet-stream"
    return mimetypes.guess_type(document.filename)[0] or "application/octet-stream"


def _not_yet_cleared_by_scan(document: Document) -> bool:
    """True when malware scanning is on and this file has not passed it."""
    if settings.MALWARE_SCAN == "off":
        return False
    if (document.meta_data or {}).get("malware_scan") not in (None, "off"):
        return False
    return document.status != "completed"


@router.post(
    "/upload",
    response_model=List[DocumentOut],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def upload_documents(
    background_tasks: BackgroundTasks,
    request: Request,
    project_id: uuid.UUID = Form(...),
    files: List[UploadFile] = File(...),
    chunk_strategy: Literal["fixed_size", "sentence", "markdown"] = Form("fixed_size"),
    chunk_size: int = Form(500, ge=50, le=4000),
    chunk_overlap: int = Form(50, ge=0, le=1000),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Accept one or more files and queue them for background processing."""
    if chunk_overlap >= chunk_size:
        raise HTTPException(
            status_code=422,
            detail="chunk_overlap must be smaller than chunk_size.",
        )

    statement = select(Project).where(Project.id == project_id)
    result = await db.execute(statement)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The specified project was not found.",
        )

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

        allowance = await usage.upload_allowance(db, current_user.org_id)

        storage = get_storage()
        processed_docs = []
        skipped_duplicates = []
        seen_hashes: set[str] = set()

        for item in spooled:
            statement_dup = select(Document).where(
                Document.project_id == project_id,
                Document.duplicate_hash == item.sha256,
            )
            result_dup = await db.execute(statement_dup)

            if item.sha256 in seen_hashes or result_dup.scalar_one_or_none():
                logger.info("Skipping duplicate file", extra={"upload_filename": item.filename})
                skipped_duplicates.append(item.filename)
                continue
            seen_hashes.add(item.sha256)

            allowance.admit(item.size)

            statement_version = select(func.max(Document.version)).where(
                Document.project_id == project_id,
                Document.filename == item.filename,
            )
            result_version = await db.execute(statement_version)
            max_version = result_version.scalar() or 0
            new_version = max_version + 1

            key = new_key(project_id, item.extension)
            try:
                await asyncio.to_thread(storage.save, key, item.path)
            except StorageError as exc:
                raise _storage_http_error(exc) from exc
            stored.append((storage, key))

            document = Document(
                filename=item.filename,
                file_type=item.extension,
                file_size=item.size,
                file_path=key,
                storage_backend=storage.name,
                status="pending",
                duplicate_hash=item.sha256,
                version=new_version,
                meta_data={
                    "original_filename": item.filename,
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                },
                project_id=project_id,
            )
            db.add(document)
            await db.flush()

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

    for document in processed_docs:
        _queue_for_processing(document.id, background_tasks)

    return processed_docs


def _queue_for_processing(document_id, background_tasks: BackgroundTasks) -> None:
    """Hand a committed document to the ingestion pipeline, never raising."""
    if settings.INGESTION_MODE != "celery":
        background_tasks.add_task(ingestion.submit_inline, str(document_id))
        return
    try:
        ingestion.dispatch(str(document_id))
    except Exception as exc:
        logger.warning(
            "Could not queue document; the recovery sweep will retry",
            extra={"document_id": str(document_id), "error": repr(exc)},
        )


@router.get("/", response_model=List[DocumentOut])
async def list_documents(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List a project's documents, newest first."""
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
    """Download or view the ORIGINAL uploaded file."""
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

    if (document.meta_data or {}).get("quarantined"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This file was quarantined by the malware scan and cannot be downloaded.",
        )

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
        "X-Content-Type-Options": "nosniff",
    }

    if isinstance(storage, LocalStorage):
        path = storage.path_for(document.file_path)
        if not os.path.isfile(path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File has been deleted from server storage.",
            )
        return FileResponse(
            path, filename=document.filename, media_type=media_type, headers=headers
        )

    try:
        chunks = await asyncio.to_thread(storage.iter_bytes, document.file_path)
    except StorageObjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has been deleted from server storage.",
        ) from exc
    except StorageError as exc:
        raise _storage_http_error(exc) from exc

    headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(document.filename)}"
    )
    return StreamingResponse(chunks, media_type=media_type, headers=headers)


async def get_org_document(db: AsyncSession, document_id: uuid.UUID, user: User) -> Document:
    """Load a document, but only from the caller's own organization."""
    row = (
        await db.execute(
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Document.id == document_id, Project.org_id == user.org_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return row


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """One document's details and processing status."""
    return await get_org_document(db, document_id, current_user)


@router.patch("/{document_id}", response_model=DocumentOut)
async def rename_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Rename a document (editor+, audited)."""
    document = await get_org_document(db, document_id, current_user)
    name = uploads.sanitize_filename(payload.filename)
    if not name:
        raise HTTPException(status_code=422, detail="That name has no usable characters.")
    if uploads.extension_of(name) != document.file_type:
        name = f"{name}.{document.file_type}"

    old_name = document.filename
    document.filename = name
    await audit.record(
        db, "document.rename", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={"document_id": document.id, "old_name": old_name, "new_name": name},
    )
    await db.commit()
    await db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Delete a document: its row, its chunks and vectors, and its stored file."""
    document = await get_org_document(db, document_id, current_user)
    backend, key = document.storage_backend, document.file_path

    await audit.record(
        db, "document.delete", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={
            "document_id": document.id,
            "project_id": document.project_id,
            "filename": document.filename,
            "file_size": document.file_size,
        },
    )
    await db.execute(delete(Document).where(Document.id == document.id))
    await db.commit()

    from app.api.v1.projects import delete_stored_file

    await delete_stored_file(backend, key)
    return None


@router.post(
    "/{document_id}/reindex",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def reindex_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    payload: Optional[DocumentReindex] = None,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Process a document again from its stored file (editor+, audited)."""
    document = await get_org_document(db, document_id, current_user)

    if (document.meta_data or {}).get("quarantined"):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This file was quarantined by the malware scan and cannot be processed.",
        )
    paused = document.status == "pending" and document.resume_after is not None
    if document.status in ("pending", "processing") and not paused:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document is already queued or being processed.",
        )

    meta = dict(document.meta_data or {})
    options = payload.model_dump(exclude_none=True) if payload else {}
    size = options.get("chunk_size", meta.get("chunk_size", settings.DEFAULT_CHUNK_SIZE))
    overlap = options.get("chunk_overlap", meta.get("chunk_overlap", settings.DEFAULT_CHUNK_OVERLAP))
    if overlap >= size:
        raise HTTPException(status_code=422, detail="chunk_overlap must be smaller than chunk_size.")
    meta.update(options)
    meta.pop("error", None)
    document.meta_data = meta
    document.status = "pending"
    document.error_message = None
    document.processing_started_at = None
    document.resume_after = None

    await audit.record(
        db, "document.reindex", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={"document_id": document.id, "filename": document.filename, **options},
    )
    await db.commit()
    await db.refresh(document)

    _queue_for_processing(document.id, background_tasks)
    return document


@router.get("/{document_id}/chunks", response_model=List[ChunkContextOut])
async def document_chunks(
    document_id: uuid.UUID,
    around: Optional[uuid.UUID] = Query(
        None, description="A chunk id: return it with its neighbours on either side."
    ),
    window: int = Query(2, ge=0, le=5, description="Neighbours on each side (0-5)."),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """A few consecutive chunks of a document's extracted text."""
    document = await get_org_document(db, document_id, current_user)

    centre = 0
    if around is not None:
        index = (
            await db.execute(
                select(Chunk.chunk_index).where(
                    Chunk.id == around, Chunk.document_id == document.id
                )
            )
        ).scalar_one_or_none()
        if index is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That passage no longer exists; the document may have been re-indexed.",
            )
        centre = index

    rows = (
        await db.execute(
            select(Chunk)
            .where(
                Chunk.document_id == document.id,
                Chunk.chunk_index >= centre - window,
                Chunk.chunk_index <= centre + (window if around is not None else 2 * window),
            )
            .order_by(Chunk.chunk_index)
        )
    ).scalars().all()
    return rows
