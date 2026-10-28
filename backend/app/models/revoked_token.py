"""Defines the REVOKED_TOKENS table -- the "denylist" of ACCESS tokens that were signed out before they expired."""
import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base_class import Base


class RevokedToken(Base):
    """A signed-out access token. Table name: "revoked_tokens"."""

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
