"""Describes the shape of project data crossing the API boundary."""
import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    """The one field a human actually chooses."""

    name: str = Field(..., min_length=1, max_length=255)


class ProjectCreate(ProjectBase):
    """Body for POST /projects/."""

    pass


class ProjectUpdate(ProjectBase):
    """Body for PATCH /projects/{id} -- renaming a project."""

    pass


class ProjectOut(ProjectBase):
    """A project as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID

    org_id: uuid.UUID

    created_at: datetime.datetime
    updated_at: datetime.datetime


class ProjectDetail(ProjectOut):
    """GET /projects/{id} -- one project, with a summary of what is in it."""

    document_count: int = 0
    ready_count: int = 0


class EmbeddingPoint(BaseModel):
    """One chunk in the 3D embedding map: where it sits and what it is."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    snippet: str
    x: float
    y: float
    z: float


class EmbeddingMap(BaseModel):
    """GET /projects/{id}/embedding-map -- a 3D PCA projection of the chunks."""

    points: list[EmbeddingPoint]
    total: int
    truncated: bool
    explained_variance: list[float]
