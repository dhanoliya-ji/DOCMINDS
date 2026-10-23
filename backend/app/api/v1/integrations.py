"""
integrations.py
===============
WHAT THIS FILE DOES
-------------------
Cloud imports (services/connectors/): connect a provider, browse it, import
files or pages into a project, and keep them in sync.

    GET    /integrations/providers                      which providers this
                                                        deployment offers
    POST   /integrations/{provider}/authorize           -> the provider's
                                                        consent URL
    GET    /integrations/callback/{provider}            where the provider
                                                        sends the browser back
    GET    /integrations/connections                    the organization's
                                                        connections
    DELETE /integrations/connections/{id}               disconnect (imported
                                                        documents stay), and
                                                        revoke at the provider
                                                        where it has an API
    GET    /integrations/connections/{id}/items         the file/page picker
    POST   /integrations/connections/{id}/import        import into a project,
                                                        in the BACKGROUND: 202
                                                        and a job to poll
    GET    /integrations/imports?connection_id=         recent import jobs
    GET    /integrations/imports/{import_id}            one job, per-item
                                                        progress
    POST   /integrations/connections/{id}/sync          "Sync now", every
                                                        document it imported
    GET    /integrations/documents?project_id=          imported documents and
                                                        their sync status
    PATCH  /integrations/documents/{document_id}        auto-sync on / off
    POST   /integrations/documents/{document_id}/sync   "Sync now", one

WHO MAY
-------
Editors and admins: importing changes what the whole organization searches,
exactly like an upload, so viewers get 403. Every route answers 404 while
CONNECTORS_ENABLED is off. Connecting, disconnecting, importing and syncing
are audited (connector.connect, connector.disconnect, connector.import,
document.upload per imported file, document.sync).

IMPORTS RUN IN THE BACKGROUND
-----------------------------
Downloading a dozen files can take minutes -- far longer than a request
should be held open (or than a proxy allows). The import endpoint only
checks the request (connection, project, item count and id format), records
a job and answers 202; the download, the upload rules, storage and the
ingestion dispatch run in the background (service.run_import: a Celery task,
or the in-process queue with INGESTION_MODE=inline). The page polls the job:
each item goes pending -> downloading -> imported (or duplicate,
already_imported, skipped, failed with the reason).

DISCONNECTING REVOKES WHERE IT CAN
----------------------------------
The connection and its tokens are deleted locally FIRST and committed;
then the provider is asked to revoke the grant (Google, Notion). Microsoft
and Atlassian have no API for that, so the reply carries `manage_url`, the
provider's own page for removing the app. A revocation failure is logged and
reported, never turned into a failed disconnect.

THE CALLBACK
------------
It is the one route without a bearer token -- a browser redirect carries
none -- so it authenticates the signed `state` instead (service.read_state)
and then sends the browser on to the frontend's /settings/integrations page
with the outcome in the query string. Errors are shown there, never as a
raw JSON page on the API's domain.
"""

import uuid
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.models.connector import DISCONNECTED, ConnectorConnection, ConnectorImport, ConnectorItem
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.schemas.integrations import (
    AuthorizeOut,
    ConnectionOut,
    DisconnectOut,
    ImportJobOut,
    ImportRequest,
    LinkedDocumentOut,
    LinkUpdate,
    ProviderOut,
    RemoteItemPage,
    SyncResult,
)
from app.services import audit
from app.services.connectors import PROVIDERS, get_provider
from app.services.connectors import service
from app.services.connectors.base import check_id
from app.services.connectors.http import ConnectorAuthError, ConnectorError, ConnectorUnavailable


def _require_enabled() -> None:
    """404 for every route while the feature is switched off."""
    if not settings.CONNECTORS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(dependencies=[Depends(_require_enabled)])


def _http_error(exc: ConnectorError) -> HTTPException:
    """A provider failure as a reply the frontend can show."""
    if isinstance(exc, ConnectorAuthError):
        code = status.HTTP_409_CONFLICT  # not 401: OUR session is fine
    elif isinstance(exc, ConnectorUnavailable):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.status_code == 400:
        code = status.HTTP_400_BAD_REQUEST
    elif exc.status_code in (403, 404, 410):
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=code, detail=exc.message)


def _configured_provider(provider_id: str):
    provider = get_provider(provider_id)
    if provider is None or not provider.configured():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown or unconfigured provider.")
    return provider


async def _connection(db: AsyncSession, user: User, connection_id: uuid.UUID) -> ConnectorConnection:
    connection = await db.get(ConnectorConnection, connection_id)
    # Another organization's connection is "not found", never "forbidden":
    # its existence is none of this caller's business.
    if connection is None or connection.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    return connection


async def _link(db: AsyncSession, user: User, document_id: uuid.UUID) -> ConnectorItem:
    link = (
        await db.execute(select(ConnectorItem).where(ConnectorItem.document_id == document_id))
    ).scalar_one_or_none()
    if link is None or link.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This document was not imported from a cloud source.")
    return link


# ======================================================================
# PROVIDERS AND CONNECTING
# ======================================================================


@router.get("/providers", response_model=List[ProviderOut])
async def list_providers(current_user: User = Depends(deps.require_editor)):
    """Every provider DocMinds supports, and whether this deployment has its keys."""
    return [{"id": p.id, "name": p.name, "configured": p.configured()} for p in PROVIDERS.values()]


@router.post("/{provider_id}/authorize", response_model=AuthorizeOut)
async def authorize(provider_id: str, request: Request, current_user: User = Depends(deps.require_editor)):
    """The URL to send the browser to, where the person grants read access."""
    provider = _configured_provider(provider_id)
    state = service.make_state(current_user, provider.id)
    return {"authorization_url": provider.authorize_url(state, service.redirect_uri(request, provider.id))}


def _back_to_frontend(**params: str) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.frontend_url}/settings/integrations?{urlencode(params)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/callback/{provider_id}", include_in_schema=False)
async def oauth_callback(
    provider_id: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(deps.get_db),
):
    provider = get_provider(provider_id)
    if provider is None or not provider.configured():
        return _back_to_frontend(error="This provider is not configured.")
    if error or not code:
        # The person pressed "Cancel" on the consent screen, or the provider refused.
        return _back_to_frontend(error="The connection was cancelled or refused by the provider.")
    try:
        claims = service.read_state(state or "", provider.id)
        user = await service.state_user(db, claims)
        tokens, label = await provider.exchange_code(code, service.redirect_uri(request, provider.id))
        connection = await service.save_connection(db, user=user, provider=provider, tokens=tokens, label=label)
        await audit.record(
            db, "connector.connect", org_id=user.org_id, user_id=user.id, request=request,
            details={"provider": provider.id, "account": label, "connection_id": connection.id},
        )
        await db.commit()
    except ConnectorError as exc:
        return _back_to_frontend(error=exc.message)
    return _back_to_frontend(connected=provider.id)


@router.get("/connections", response_model=List[ConnectionOut])
async def list_connections(
    db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_editor)
):
    rows = await db.execute(
        select(ConnectorConnection)
        .where(ConnectorConnection.org_id == current_user.org_id)
        .order_by(ConnectorConnection.created_at)
    )
    return rows.scalars().all()


@router.delete("/connections/{connection_id}", response_model=DisconnectOut)
async def disconnect(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """
    Remove the connection and its tokens. Documents it imported stay, but
    stop syncing ("disconnected"). Then, best effort, revoke DocMinds'
    access at the provider: Google and Notion have an API for it; for
    Microsoft and Confluence (no such API) `manage_url` links to the page
    where the person removes the app. A failed revocation never fails the
    disconnect -- it is logged, and reported here.
    """
    connection = await _connection(db, current_user, connection_id)
    links = await db.execute(select(ConnectorItem).where(ConnectorItem.connection_id == connection.id))
    for link in links.scalars().all():
        link.connection_id = None
        link.sync_status = DISCONNECTED
    await audit.record(
        db, "connector.disconnect", org_id=current_user.org_id, user_id=current_user.id, request=request,
        details={"provider": connection.provider, "account": connection.account_label, "connection_id": connection.id},
    )
    # Keep what revocation needs; the row (and its ciphertext) is deleted now.
    held = ConnectorConnection(
        id=connection.id, provider=connection.provider, encrypted_tokens=connection.encrypted_tokens
    )
    await db.delete(connection)
    await db.commit()
    return await service.revoke(held)


# ======================================================================
# THE PICKER AND IMPORT
# ======================================================================


@router.get("/connections/{connection_id}/items", response_model=RemoteItemPage)
async def browse(
    connection_id: uuid.UUID,
    parent: Optional[str] = Query(None, max_length=512, description="A folder, space or page id; empty = the top level."),
    q: Optional[str] = Query(None, max_length=200, description="Search by name instead of browsing."),
    cursor: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    connection = await _connection(db, current_user, connection_id)
    query = (q or "").strip() or None
    try:
        items, next_cursor = await service.call(
            db, connection, lambda p, t: p.list_items(t, parent=parent or None, query=query, cursor=cursor)
        )
    except ConnectorError as exc:
        raise _http_error(exc) from exc
    return {"items": [item.as_dict() for item in items], "next_cursor": next_cursor}


@router.post(
    "/connections/{connection_id}/import",
    response_model=ImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    # As heavy as an upload (download, then extraction and embedding).
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def import_items(
    connection_id: uuid.UUID,
    payload: ImportRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """
    Import the picked items into a project, in the background. Answers 202
    with the job at once; poll GET /integrations/imports/{id} for each
    item's progress. Imported items are queued for ingestion like an upload
    (document status "pending") and remember their source for sync.
    """
    connection = await _connection(db, current_user, connection_id)
    project = await db.get(Project, payload.project_id)
    if project is None or project.org_id != current_user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The specified project was not found.")
    if len(payload.item_ids) > settings.MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {settings.MAX_FILES_PER_UPLOAD} items can be imported at once.",
        )
    item_ids = list(dict.fromkeys(payload.item_ids))  # duplicates in one request: once
    try:
        for item_id in item_ids:
            check_id(item_id)
    except ConnectorError as exc:
        raise _http_error(exc) from exc
    job = await service.create_import(
        db, connection=connection, project=project, user=current_user,
        item_ids=item_ids, auto_sync=payload.auto_sync, request=request,
    )
    await db.commit()
    service.dispatch_import(job.id)  # after the commit: the worker must see the row
    return service.import_view(job)


@router.get("/imports", response_model=List[ImportJobOut])
async def list_imports(
    connection_id: Optional[uuid.UUID] = None,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """The organization's most recent import jobs, newest first."""
    statement = (
        select(ConnectorImport)
        .where(ConnectorImport.org_id == current_user.org_id)
        .order_by(ConnectorImport.created_at.desc())
        .limit(limit)
    )
    if connection_id is not None:
        statement = statement.where(ConnectorImport.connection_id == connection_id)
    rows = await db.execute(statement)
    return [service.import_view(job) for job in rows.scalars().all()]


@router.get("/imports/{import_id}", response_model=ImportJobOut)
async def get_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """One import job: its status and every item's progress."""
    job = await db.get(ConnectorImport, import_id)
    if job is None or job.org_id != current_user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")
    return service.import_view(job)


# ======================================================================
# IMPORTED DOCUMENTS AND SYNC
# ======================================================================


@router.get("/documents", response_model=List[LinkedDocumentOut])
async def list_linked_documents(
    project_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    statement = (
        select(ConnectorItem, Document)
        .join(Document, Document.id == ConnectorItem.document_id)
        .where(ConnectorItem.org_id == current_user.org_id)
        .order_by(ConnectorItem.created_at.desc())
    )
    if project_id is not None:
        statement = statement.where(ConnectorItem.project_id == project_id)
    rows = await db.execute(statement)
    return [service.link_view(link, document) for link, document in rows.all()]


@router.patch("/documents/{document_id}", response_model=LinkedDocumentOut)
async def update_link(
    document_id: uuid.UUID,
    payload: LinkUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    link = await _link(db, current_user, document_id)
    link.auto_sync = payload.auto_sync
    await db.commit()
    return service.link_view(link, await db.get(Document, link.document_id))


async def _sync_one(db: AsyncSession, link: ConnectorItem, user: User) -> dict:
    outcome = await service.sync_link(db, link, user_id=user.id)
    document = await db.get(Document, link.document_id)
    return {
        "document_id": link.document_id,
        "outcome": outcome,
        "sync_status": service.effective_status(link, document),
        "last_error": link.last_error,
    }


@router.post(
    "/documents/{document_id}/sync",
    response_model=SyncResult,
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def sync_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """Check this document's source now, and re-import it if it changed."""
    return await _sync_one(db, await _link(db, current_user, document_id), current_user)


@router.post(
    "/connections/{connection_id}/sync",
    response_model=List[SyncResult],
    dependencies=[Depends(deps.rate_limit_by_user("upload"))],
)
async def sync_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_editor),
):
    """ "Sync now" for every document this connection imported (at most CONNECTOR_SYNC_BATCH_SIZE)."""
    connection = await _connection(db, current_user, connection_id)
    links = (
        await db.execute(
            select(ConnectorItem)
            .where(ConnectorItem.connection_id == connection.id)
            .limit(settings.CONNECTOR_SYNC_BATCH_SIZE)
        )
    ).scalars().all()
    return [await _sync_one(db, link, current_user) for link in links]
