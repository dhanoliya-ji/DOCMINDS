"""Describes the shape of organization data crossing the API boundary."""
import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class OrganizationBase(BaseModel):
    """The company name."""

    name: str


class OrganizationCreate(OrganizationBase):
    """Body for creating an organization directly."""

    pass


class OrganizationUpdate(OrganizationBase):
    """Body for renaming an organization."""

    pass


class OrganizationOut(OrganizationBase):
    """An organization as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime
