"""Request and response shapes for the webhook admin endpoints response -- `WebhookCreated`, returned at..."""
import datetime
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class WebhookCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    event_types: List[str] = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class WebhookUpdate(BaseModel):
    """Every field optional: send only what changes."""

    url: Optional[str] = Field(None, min_length=1, max_length=2048)
    event_types: Optional[List[str]] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    description: Optional[str] = None
    event_types: List[str]
    is_active: bool
    created_at: Optional[datetime.datetime] = None


class WebhookCreated(WebhookOut):
    secret: str


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status: str
    attempts: int
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    next_attempt_at: Optional[datetime.datetime] = None
    delivered_at: Optional[datetime.datetime] = None
    created_at: Optional[datetime.datetime] = None
    payload: Dict[str, Any]


class WebhookEventType(BaseModel):
    name: str
    description: str
