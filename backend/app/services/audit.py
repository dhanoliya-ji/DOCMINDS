"""One function, `record`, that every endpoint calls to add a row to the audit"""
import uuid
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip as resolve_client_ip
from app.core.config import settings
from app.models.audit import AuditLog


def client_ip(request: Optional[Request]) -> Optional[str]:
    """The caller's IP address."""
    return resolve_client_ip(request)


async def record(
    db: AsyncSession,
    action: str,
    *,
    org_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    request: Optional[Request] = None,
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """Stage one audit entry in the current transaction (no commit)."""
    entry = AuditLog(
        action=action,
        org_id=org_id,
        user_id=user_id,
        ip_address=client_ip(request),
        details={
            key: (value if isinstance(value, (str, int, float, bool, type(None), list, dict)) else str(value))
            for key, value in (details or {}).items()
        },
    )
    db.add(entry)

    if settings.WEBHOOKS_ENABLED:
        from app.services import webhooks

        await webhooks.emit_for_audit(
            db, action, org_id=org_id, user_id=user_id, details=entry.details
        )
    return entry
