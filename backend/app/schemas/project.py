"""
project.py  (schemas)
=====================
Describes the shape of project data crossing the API boundary.

A project is a document collection AND a search boundary -- a question asked
inside one project never retrieves chunks from another. See models/project.py.

THE PATTERN USED THROUGHOUT THIS PROJECT
----------------------------------------
    ...Base    fields common to several schemas, written once
    ...Create  what a client may SEND when creating
    ...Update  what a client may SEND when editing
    ...Out     what the API RETURNS

Splitting create from out is what stops a client inventing server-controlled
values. Notice `ProjectCreate` accepts only a name: `id`, `org_id` and the
timestamps are all decided by the server. A client cannot post an `org_id` and
plant a project inside somebody else's organization, because the field is not
part of the schema at all -- the endpoint takes it from the caller's verified
JWT instead.
"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    """The one field a human actually chooses."""

    # Bounded so an empty or absurdly long name is a 422 at the door rather
    # than a database error (the column is String(255)).
    name: str = Field(..., min_length=1, max_length=255)


class ProjectCreate(ProjectBase):
    """
    Body for POST /projects/.

    Empty because it needs nothing beyond the inherited `name`. It exists as a
    distinct class anyway, so that creation-only fields can be added later
    without touching the response shape -- and so the generated API docs name
    the request body meaningfully.
    """

    pass


class ProjectUpdate(ProjectBase):
    """Body for PATCH /projects/{id} -- renaming a project."""

    pass


class ProjectOut(ProjectBase):
    """A project as returned by the API."""

    # Build directly from the SQLAlchemy row by reading its attributes.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID

    # Returned so a client can confirm which tenant owns the project. Safe to
    # expose, because a user only ever receives projects from their OWN
    # organization -- the endpoint filters on `current_user.org_id`.
    org_id: uuid.UUID

    created_at: datetime.datetime
    updated_at: datetime.datetime


class ProjectDetail(ProjectOut):
    """
    GET /projects/{id} -- one project, with a summary of what is in it.

    The counts save the workspace page a second request just to print
    "12 documents, 9 ready".
    """

    document_count: int = 0
    ready_count: int = 0


class EmbeddingPoint(BaseModel):
    """One chunk in the 3D embedding map: where it sits and what it is."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    # The first ~160 characters, for the hover label.
    snippet: str
    x: float
    y: float
    z: float


class EmbeddingMap(BaseModel):
    """GET /projects/{id}/embedding-map -- a 3D PCA projection of the chunks."""

    points: list[EmbeddingPoint]
    # Chunks with an embedding in the project; above `points` when capped.
    total: int
    truncated: bool
    # Share of the variance each of the three axes keeps (0..1).
    explained_variance: list[float]
