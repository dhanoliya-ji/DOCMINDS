"""
documents.py
============
WHAT THIS FILE DOES
-------------------
Endpoints for getting files into the system and back out again.

    POST /documents/upload              upload one or more files
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
    2. hash the bytes and check for a duplicate
    3. work out the version number
    4. write the file to disk
    5. create a Document row with status "pending"
    6. drop a job id onto the Redis queue
    7. reply immediately

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
"""

import hashlib
import os
import uuid
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.document import DocumentOut
from app.core.config import settings
from app.tasks.ingestion import async_process_document, process_document_task

router = APIRouter()

# Where uploaded files are written: backend/uploads/.
#
# Built by walking up from this file (api/v1/ -> api/ -> app/ -> backend/) and
# resolved to an absolute path, so it does not depend on which directory the
# server was launched from.
UPLOAD_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "uploads")
)
# `exist_ok=True` so a restart does not fail on an existing directory.
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post(
    "/upload",
    response_model=List[DocumentOut],
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_documents(
    # Injected by FastAPI. Used only when INGESTION_MODE is "inline".
    background_tasks: BackgroundTasks,
    # `Form(...)` and `File(...)` rather than a JSON body, because file uploads
    # use multipart/form-data -- JSON cannot carry binary data.
    project_id: uuid.UUID = Form(...),
    files: List[UploadFile] = File(...),
    # Chunking options, with sensible defaults so a simple upload needs nothing
    # extra. Stashed in meta_data and read later by the worker.
    chunk_strategy: str = Form("fixed_size"),
    chunk_size: int = Form(500),
    chunk_overlap: int = Form(50),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Accept one or more files and queue them for background processing.

    Returns immediately with the created Document rows, each showing
    status "pending".
    """
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

    processed_docs = []
    skipped_duplicates = []

    for file in files:
        # Read the whole file into memory. Fine for the 50 MB documents this
        # app targets; a system handling multi-gigabyte files would need to
        # stream to disk in chunks instead.
        content = await file.read()

        # ------------------------------------------------------------------
        # 2. DEDUPLICATION
        # ------------------------------------------------------------------
        # SHA-256 of the CONTENT, so a renamed copy is still detected.
        sha256_hash = hashlib.sha256(content).hexdigest()

        statement_dup = select(Document).where(
            Document.project_id == project_id,
            Document.duplicate_hash == sha256_hash,
        )
        result_dup = await db.execute(statement_dup)

        if result_dup.scalar_one_or_none():
            # Already present in THIS project. Skip it rather than repeating
            # the expensive OCR and embedding work.
            print(f"[Upload] Skipping duplicate file: {file.filename}")
            skipped_duplicates.append(file.filename)
            continue

        # ------------------------------------------------------------------
        # 3. VERSIONING
        # ------------------------------------------------------------------
        # Same name, different contents (the hash check above already ruled out
        # an identical file) means this is a revision. Find the highest version
        # so far and add one.
        statement_version = select(func.max(Document.version)).where(
            Document.project_id == project_id,
            Document.filename == file.filename,
        )
        result_version = await db.execute(statement_version)
        # `or 0` because max() returns NULL when there are no matching rows,
        # which arrives in Python as None.
        max_version = result_version.scalar() or 0
        new_version = max_version + 1

        # ------------------------------------------------------------------
        # 4. WRITE TO DISK
        # ------------------------------------------------------------------
        extension = os.path.splitext(file.filename)[1].lstrip(".").lower()

        # A uuid prefix so two users uploading "report.pdf" cannot overwrite
        # each other. The original name is preserved in the database column.
        unique_filename = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)

        with open(file_path, "wb") as handle:
            handle.write(content)

        # ------------------------------------------------------------------
        # 5. CREATE THE DATABASE RECORD
        # ------------------------------------------------------------------
        document = Document(
            filename=file.filename,
            file_type=extension,
            file_size=len(content),
            file_path=file_path,
            # The worker moves this through processing -> completed | failed.
            status="pending",
            duplicate_hash=sha256_hash,
            version=new_version,
            meta_data={
                "original_filename": file.filename,
                # The worker reads these back when it chunks the document.
                "chunk_strategy": chunk_strategy,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            },
            project_id=project_id,
        )
        db.add(document)
        # `flush` assigns document.id, which the Celery job needs, without
        # committing yet.
        await db.flush()

        # ------------------------------------------------------------------
        # 6. QUEUE THE BACKGROUND JOB
        # ------------------------------------------------------------------
        # `.delay()` does NOT run the function. It serialises the argument,
        # pushes a message onto Redis and returns instantly. A worker process
        # picks it up and runs the real pipeline there.
        #
        # INGESTION_MODE=inline has no worker: the job is scheduled further
        # down, once the commit has made the row visible to its own session.
        if settings.INGESTION_MODE == "celery":
            process_document_task.delay(str(document.id))

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
    for document in processed_docs:
        await db.refresh(document)

    if settings.INGESTION_MODE == "inline":
        # Runs after the response is sent, so the upload still returns at once
        # with status "pending" and the frontend's polling works unchanged.
        for document in processed_docs:
            background_tasks.add_task(async_process_document, str(document.id))

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

    # The row can outlive the file -- most commonly on a cloud host with an
    # ephemeral disk, where uploads are wiped on restart. Search still works in
    # that situation, because the chunks and vectors live in Postgres; only the
    # original download is unavailable, so we say so clearly.
    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has been deleted from server storage.",
        )

    # `FileResponse` streams the file rather than loading it all into memory,
    # and `filename=` sets the name the browser saves it under -- the original,
    # not our uuid-prefixed one on disk.
    return FileResponse(document.file_path, filename=document.filename)
