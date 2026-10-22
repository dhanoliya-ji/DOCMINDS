"""
models/__init__.py
==================
WHAT THIS FILE DOES
-------------------
Imports every database model in one place.

WHY THAT IS NECESSARY, NOT JUST TIDY
------------------------------------
A SQLAlchemy model only registers itself with `Base.metadata` when its module
is actually IMPORTED. Python does not scan folders looking for classes -- code
that is never imported simply does not exist as far as the program is concerned.

Two things depend on that registry being complete:

  1. ALEMBIC MIGRATIONS. `alembic revision --autogenerate` compares
     `Base.metadata` against the real database to work out what changed. A
     model that was never imported is invisible to it, so Alembic would
     conclude its table should be DROPPED -- or would never create it at all.

  2. RELATIONSHIP RESOLUTION. Relationships refer to other models by NAME, as
     strings: `relationship("Document", ...)`. SQLAlchemy resolves those names
     later, once everything is loaded. If `Document` was never imported, that
     lookup fails at runtime with "expression 'Document' failed to locate a
     name".

So this file is what makes the whole model layer coherent. Importing
`app.models` anywhere pulls in every table at once.

THE ORDER MATTERS
-----------------
Models are listed roughly parent-first (Organization, then User, then Project,
then Document...). Not strictly required -- string-based relationships are
resolved lazily -- but it mirrors the real ownership hierarchy and makes the
schema readable at a glance.
"""

from app.db.base_class import Base
from app.models.api_key import APIKey
from app.models.audit import AuditLog
from app.models.chat import ChatMessage, ChatSession, Feedback
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.models.invite import Invite
from app.models.organization import Organization
from app.models.project import Project
from app.models.refresh_token import RefreshToken
from app.models.revoked_token import RevokedToken
from app.models.user import User
from app.models.user_token import UserToken

# `__all__` declares this module's public surface. It controls what
# `from app.models import *` brings in, and documents the intended exports for
# anyone reading. Linters also use it to know these imports are deliberate
# re-exports rather than unused leftovers.
__all__ = [
    "Base",
    # ---- Tenancy ----
    "Organization",
    "User",
    "Project",
    # ---- Documents and the search index ----
    "Document",
    "Chunk",
    "ChunkEmbedding",
    # ---- Conversations ----
    "ChatSession",
    "ChatMessage",
    "Feedback",
    # ---- Access and accountability ----
    "APIKey",
    "AuditLog",
    # ---- Sessions, invites and emailed links ----
    "Invite",
    "RefreshToken",
    "RevokedToken",
    "UserToken",
]
