"""
projects.py
===========
WHAT THIS FILE DOES
-------------------
Endpoints for managing projects -- the document collections that also act as
search boundaries.

    GET  /projects/   list the projects in MY organization
    POST /projects/   create one  (editor or above: Manager or Admin)

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

from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.project import Project
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectOut

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
    await db.commit()
    # Reload so the database-generated timestamps are populated before we
    # return the object.
    await db.refresh(db_project)

    return db_project
