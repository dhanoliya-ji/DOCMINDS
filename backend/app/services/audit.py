"""
audit.py  (service)
===================
WHAT THIS FILE DOES
-------------------
One function, `record`, that every endpoint calls to add a row to the audit
log (models/audit.py): who did what, to what, from where, and when.

    await audit.record(
        db, "document.upload",
        org_id=user.org_id, user_id=user.id, request=request,
        details={"filename": "q3.pdf", "document_id": "..."},
    )

THE ACTIONS WRITTEN TODAY
-------------------------
    user.signup  user.login  user.login_failed  user.logout
    user.email_verified  password.reset_requested  password.reset
    invite.create  invite.revoke  invite.accept
    member.role_change  member.deactivate  member.reactivate  member.remove
    document.upload  document.delete  document.rename  document.reindex
    project.create  project.rename  project.delete
    api_key.create  api_key.revoke
    connector.connect  connector.disconnect  connector.import  document.sync
    billing.checkout  billing.portal  billing.plan_change
    share.create  share.revoke

WEBHOOKS
--------
With WEBHOOKS_ENABLED, `record` also forwards the actions listed in
services/webhooks.py AUDIT_EVENT_MAP to the organization's webhooks, so no
endpoint has to know webhooks exist.

The "noun.verb" naming keeps related events together, so the audit endpoint's
`action` filter can ask for exactly one, and a prefix like "member." groups a
family of them.

WHY IT DOES NOT COMMIT
----------------------
`record` only ADDS the row to the session. The endpoint's own `commit()` then
saves it together with the change it describes, in ONE transaction. That
gives the property an audit trail needs most: there is no state where the
document was deleted but the log says nothing, nor where the log claims a
deletion that was rolled back.
"""

import uuid
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip as resolve_client_ip
from app.core.config import settings
from app.models.audit import AuditLog


def client_ip(request: Optional[Request]) -> Optional[str]:
    """
    The caller's IP address.

    Behind a reverse proxy (Render, a load balancer) the TCP peer is the
    proxy, not the user. X-Forwarded-For is NOT read blindly -- any client can
    send that header, so trusting it would let an attacker write whatever IP
    they liked into the audit trail. It is read only as far as the operator
    has declared proxies trustworthy (TRUSTED_PROXY_COUNT, CLIENT_IP_HEADER);
    see core/client_ip.py, which the rate limiter and the access log share so
    all three always agree on who a request came from.
    """
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
        # JSONB cannot store a UUID object; stringify anything that is not a
        # plain JSON type so callers can pass ids straight through.
        details={
            key: (value if isinstance(value, (str, int, float, bool, type(None), list, dict)) else str(value))
            for key, value in (details or {}).items()
        },
    )
    db.add(entry)

    # WEBHOOKS: many audit actions are also events an organization may want
    # pushed to its own systems ("invite.accept" -> "member.joined"). Staged
    # in the same transaction and sent only after it commits -- see
    # services/webhooks.py. Imported here, not at the top, because webhooks
    # is optional and this keeps audit importable on its own.
    if settings.WEBHOOKS_ENABLED:
        from app.services import webhooks

        await webhooks.emit_for_audit(
            db, action, org_id=org_id, user_id=user_id, details=entry.details
        )
    return entry
