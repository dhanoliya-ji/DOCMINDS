"""Endpoints for managing projects -- the document collections that also act as search boundaries."""
import asyncio
import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.project import (
    EmbeddingMap,
    EmbeddingPoint,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
)
from app.services import audit, embedding_map
from app.services.embedding import EmbeddingService
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List every project belonging to the caller's organization."""
    statement = select(Project).where(Project.org_id == current_user.org_id)
    result = await db.execute(statement)

    return result.scalars().all()


@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Create a project. Restricted to Admin and Manager roles."""
    db_project = Project(
        name=project_in.name,
        org_id=current_user.org_id,
    )

    db.add(db_project)
    await db.flush()  # assigns db_project.id for the audit entry
    await audit.record(
        db, "project.create", org_id=current_user.org_id, user_id=current_user.id,
        request=request, details={"project_id": db_project.id, "name": db_project.name},
    )
    await db.commit()
    await db.refresh(db_project)

    return db_project


async def get_org_project(db: AsyncSession, project_id: uuid.UUID, user: User) -> Project:
    """Load a project, but only from the caller's own organization."""
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


@router.get("/{project_id}/embedding-map", response_model=EmbeddingMap)
async def get_embedding_map(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_viewer),
):
    """Every chunk of the project as a point in 3D, for the search page's cloud."""
    project = await get_org_project(db, project_id, current_user)
    model_name = EmbeddingService.current_model_name()
    in_project = (
        select(ChunkEmbedding.id)
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.project_id == project.id, ChunkEmbedding.model_name == model_name)
    )
    total = (await db.execute(select(func.count()).select_from(in_project.subquery()))).scalar_one()

    rows = (
        await db.execute(
            select(Chunk.id, Chunk.document_id, Document.filename, Chunk.content, ChunkEmbedding.embedding)
            .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.project_id == project.id, ChunkEmbedding.model_name == model_name)
            .order_by(Document.created_at, Document.id, Chunk.chunk_index, ChunkEmbedding.id)
            .limit(embedding_map.MAX_POINTS)
        )
    ).all()

    coords, explained = await asyncio.to_thread(embedding_map.project_3d, [row[4] for row in rows])
    points = [
        EmbeddingPoint(
            chunk_id=row[0], document_id=row[1], filename=row[2],
            snippet=" ".join((row[3] or "").split())[:160],
            x=round(float(xyz[0]), 4), y=round(float(xyz[1]), 4), z=round(float(xyz[2]), 4),
        )
        for row, xyz in zip(rows, coords, strict=True)
    ]
    return EmbeddingMap(
        points=points, total=int(total or 0), truncated=int(total or 0) > len(points),
        explained_variance=explained,
    )


@router.patch("/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
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
    """Delete a project and EVERYTHING in it."""
    project = await get_org_project(db, project_id, current_user)

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
