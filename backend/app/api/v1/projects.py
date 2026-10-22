"""
projects.py
===========
WHAT THIS FILE DOES
-------------------
Endpoints for managing projects -- the document collections that also act as
search boundaries.

    GET    /projects/       list the projects in MY organization
    POST   /projects/       create one  (editor or above: Manager or Admin)
    GET    /projects/{id}   one project, with document counts
    PATCH  /projects/{id}   rename it   (editor+, audited)
    DELETE /projects/{id}   delete it, its documents, their chunks, their
                            stored files and every conversation about it
                            (editor+, audited)

THE TENANT ISOLATION PATTERN, IN ITS SIMPLEST FORM
--------------------------------------------------
This file is the clearest illustration of how isolation works throughout the
application. Look at the list query:

        .where(Project.org_id == current_user.org_id)

`current_user` comes from the verified JWT, so `org_id` is a value the server
established at login. It is never read from the request. A client cannot ask
for another organization's projects, because there is no parameter through
which to ask -- the filter is not optional and not client-controlled.

Every read path in this codebase follows that same shape, right down to the
vector search in services/retrieval.py.
"""

import asyncio
import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectDetail, ProjectOut, ProjectUpdate
from app.services import audit
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    List every project belonging to the caller's organization.

    `Depends(get_current_active_user)` does three jobs before this function
    runs: it verifies the token, loads the user, and confirms the account is
    still enabled. If any of those fail the request never reaches this code.
    """
    statement = select(Project).where(Project.org_id == current_user.org_id)
    result = await db.execute(statement)

    # `.scalars()` unwraps each row into the Project object itself. Without it,
    # SQLAlchemy hands back one-element Row tuples.
    return result.scalars().all()


@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    # ---- ROLE-BASED ACCESS CONTROL ----
    # `require_editor` runs the normal authentication chain and then requires
    # the editor role (Manager) or anything ranked above it (Admin). A viewer
    # (Employee) -- or a viewer-scoped API key -- gets 403 before the body
    # executes. See core/roles.py for the ranking.
    current_user: User = Depends(deps.require_editor),
):
    """
    Create a project. Restricted to Admin and Manager roles.

    The restriction exists because a project is a search boundary: letting
    everyone create them would fragment an organization's documents into
    collections nobody else knows to search.
    """
    db_project = Project(
        name=project_in.name,
        # >>> Taken from the verified token, NOT from the request body. <<<
        # `ProjectCreate` has no org_id field at all, so a client physically
        # cannot plant a project inside another organization.
        org_id=current_user.org_id,
    )

    db.add(db_project)
    await db.flush()  # assigns db_project.id for the audit entry
    await audit.record(
        db, "project.create", org_id=current_user.org_id, user_id=current_user.id,
        request=request, details={"project_id": db_project.id, "name": db_project.name},
    )
    await db.commit()
    # Reload so the database-generated timestamps are populated before we
    # return the object.
    await db.refresh(db_project)

    return db_project


# ======================================================================
# ONE PROJECT: read, rename, delete
# ======================================================================


async def get_org_project(db: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    """
    Load a project, but only from the caller's own organization.

    Another tenant's project gets the same 404 as one that does not exist:
    a 403 would confirm that the id is real.
    """
    project = await db.get(Project, project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """One project, with how many documents it holds and how many are ready."""
    project = await get_org_project(db, project_id, current_user)

    # Two counts in one query: every document, and the completed ones.
    # count() skips NULLs, and the CASE is NULL for anything not completed.
    total, ready = (
        await db.execute(
            select(
                func.count(Document.id),
                func.count(case((Document.status == "completed", 1))),
            ).where(Document.project_id == project.id)
        )
    ).one()
    return ProjectDetail.model_validate(project).model_copy(
        update={"document_count": int(total or 0), "ready_count": int(ready or 0)}
    )


@router.patch("/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    # Same rule as creating one: projects are managed by editors and admins.
    current_user: User = Depends(deps.require_editor),
):
    """Rename a project. Its documents and conversations are untouched."""
    project = await get_org_project(db, project_id, current_user)
    old_name, new_name = project.name, payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="The name cannot be blank.")
    project.name = new_name
    await audit.record(
        db, "project.rename", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={"project_id": project.id, "old_name": old_name, "new_name": new_name},
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """
    Delete a project and EVERYTHING in it.

    WHAT GOES, AND WHO REMOVES IT
        documents, chunks, embeddings,   the database, through ON DELETE
        chat sessions and messages       CASCADE on every foreign key -- one
                                         DELETE of the project row
        the stored files                 this endpoint, AFTER the commit

    WHY THE FILES GO AFTER THE COMMIT
    If the transaction failed after the files were already deleted, the
    database would still list documents whose bytes are gone. Deleting them
    after the commit can at worst leave an orphaned file -- invisible to
    every user, and harmless -- never a visible document with no content.
    """
    project = await get_org_project(db, project_id, current_user)

    # Collected BEFORE the delete: once the rows are gone, so is the record
    # of where their bytes live.
    stored = (
        await db.execute(
            select(Document.storage_backend, Document.file_path).where(
                Document.project_id == project.id
            )
        )
    ).all()

    await audit.record(
        db, "project.delete", org_id=current_user.org_id, user_id=current_user.id,
        request=request,
        details={"project_id": project.id, "name": project.name, "documents": len(stored)},
    )
    # A bulk DELETE statement rather than db.delete(project): the ORM version
    # would first load every document and chunk into memory just to delete
    # them one by one, when the database's cascade does it in one statement.
    await db.execute(delete(Project).where(Project.id == project.id))
    await db.commit()

    for backend, key in stored:
        await delete_stored_file(backend, key)
    return None


async def delete_stored_file(backend: str, key: str) -> None:
    """Best-effort removal of one stored file; a failure is logged, not raised."""
    try:
        await asyncio.to_thread(get_storage(backend).delete, key)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(
            "Could not delete a stored file", extra={"storage_key": key, "error": repr(exc)}
        )
