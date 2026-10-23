"""
connectors/service.py
=====================
WHAT THIS FILE DOES
-------------------
Everything between the API and the providers:

    make_state / read_state     the signed OAuth `state` parameter
    save_connection             store (or refresh) a connection, encrypted
    call                        run a provider call with a fresh token,
                                refreshing once on a 401
    import_items                picker selection -> Documents, queued for
                                ingestion exactly like an upload
    sync_link / sync_due        has the source changed? re-import + re-index

THE OAUTH STATE
---------------
The provider sends the browser back to /integrations/callback/<provider>
with no Authorization header -- the browser does not attach our bearer token
to a redirect. So the `state` WE sent out carries who started the connect: a
short-lived JWT (10 minutes) signed with SECRET_KEY, naming the user, the
organization and the provider, with its own token type so no other JWT the
app issues can be replayed here. The callback re-checks that the user still
exists, is active and still an editor of that organization.

IMPORTING USES THE UPLOAD PATH'S RULES
--------------------------------------
An imported file is held to exactly what an upload is: the extension
allowlist and content sniffing (services/uploads.py), MAX_UPLOAD_SIZE_BYTES,
de-duplication by SHA-256 within the project, versioning by name, the
document and storage quotas (services/usage.py), a server-chosen storage key
(services/storage.py), an audit entry, and then the SAME ingestion entry
point the upload endpoint uses (tasks/ingestion.py `dispatch`, after the
commit). Nothing about extraction, chunking or embedding is specific to
imports.

SYNC
----
A link is "checked" by asking the provider for the item's current revision
(one small metadata call). Only when it differs is the file downloaded; only
when the downloaded bytes hash differently is the stored file replaced and
the document re-indexed -- touching a Google Doc's sharing settings changes
its version but not its text, and must not cost an embedding run.

Two API processes (or beat firing twice) cannot sync the same link at once:
each claims it with one conditional UPDATE of `last_checked_at`, the same
trick tasks/ingestion.py uses to claim documents.
"""

import asyncio
import contextlib
import datetime
import hashlib
import logging
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

import jwt
from fastapi import Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import roles
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.connector import (
    ACTIVE,
    DISCONNECTED,
    ERROR,
    SOURCE_DELETED,
    SYNC_ERROR,
    SYNCED,
    SYNCING,
    ConnectorConnection,
    ConnectorItem,
)
from app.models.document import Document
from app.models.project import Project
from app.models.user import User
from app.services import audit, uploads, usage
from app.services.connectors import get_provider
from app.services.connectors.base import Provider, RemoteItem, TokenSet
from app.services.connectors.crypto import TokenDecryptError, decrypt_tokens, encrypt_tokens
from app.services.connectors.http import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorUnavailable,
    SourceNotFound,
)
from app.services.storage import StorageError, get_storage, new_key

logger = logging.getLogger(__name__)

T = TypeVar("T")

STATE_TYPE = "connector_state"
STATE_MINUTES = 10


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """SQLite hands back naive datetimes; treat them as the UTC they are."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


# ======================================================================
# 1. THE OAUTH STATE AND REDIRECT
# ======================================================================


def make_state(user: User, provider_id: str) -> str:
    now = utcnow()
    return jwt.encode(
        {
            "type": STATE_TYPE,
            "sub": str(user.id),
            "org": str(user.org_id),
            "provider": provider_id,
            "nonce": secrets.token_urlsafe(16),
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(minutes=STATE_MINUTES)).timestamp()),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def read_state(state: str, provider_id: str) -> Dict[str, Any]:
    """The state's claims, or ConnectorError if it is forged, expired or for another provider."""
    try:
        claims = jwt.decode(state or "", settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.InvalidTokenError:
        raise ConnectorError("The connection request expired or is invalid. Please try again.") from None
    if claims.get("type") != STATE_TYPE or claims.get("provider") != provider_id:
        raise ConnectorError("The connection request is invalid. Please try again.")
    return claims


def redirect_uri(request: Request, provider_id: str) -> str:
    path = f"{settings.API_V1_STR}/integrations/callback/{provider_id}"
    if settings.CONNECTOR_REDIRECT_BASE_URL:
        return settings.CONNECTOR_REDIRECT_BASE_URL.rstrip("/") + path
    return str(request.base_url).rstrip("/") + path


async def state_user(db: AsyncSession, claims: Dict[str, Any]) -> User:
    """The user who started the connect -- still active, still an editor, same org."""
    try:
        user = await db.get(User, uuid.UUID(claims["sub"]))
    except (KeyError, ValueError):
        user = None
    if (
        user is None
        or not user.is_active
        or str(user.org_id) != claims.get("org")
        or not roles.at_least(user.role, roles.EDITOR)
    ):
        raise ConnectorError("You no longer have permission to connect sources for this organization.")
    return user


# ======================================================================
# 2. CONNECTIONS AND TOKENS
# ======================================================================


async def save_connection(
    db: AsyncSession, *, user: User, provider: Provider, tokens: TokenSet, label: str
) -> ConnectorConnection:
    """Create the connection -- or, reconnecting the same account, renew it."""
    existing = (
        await db.execute(
            select(ConnectorConnection).where(
                ConnectorConnection.org_id == user.org_id,
                ConnectorConnection.provider == provider.id,
                ConnectorConnection.account_label == label,
            )
        )
    ).scalar_one_or_none()
    connection = existing or ConnectorConnection(
        org_id=user.org_id, provider=provider.id, account_label=label[:255], created_by=user.id
    )
    connection.encrypted_tokens = encrypt_tokens(tokens.to_dict())
    connection.status = ACTIVE
    connection.last_error = None
    if existing is None:
        db.add(connection)
    await db.flush()
    return connection


def _mark_broken(connection: ConnectorConnection, message: str) -> None:
    connection.status = ERROR
    connection.last_error = message


async def _refresh(db: AsyncSession, connection: ConnectorConnection, provider: Provider, tokens: TokenSet) -> TokenSet:
    try:
        fresh = await provider.refresh(tokens)
    except ConnectorUnavailable:
        raise
    except ConnectorError as exc:
        # invalid_grant (a revoked or rotated-away refresh token) arrives as
        # a 400: either way the person has to reconnect.
        _mark_broken(connection, exc.message)
        await db.commit()
        raise ConnectorAuthError("This connection has expired or was revoked. Reconnect it.") from exc
    connection.encrypted_tokens = encrypt_tokens(fresh.to_dict())
    connection.status = ACTIVE
    connection.last_error = None
    await db.commit()
    return fresh


async def call(
    db: AsyncSession,
    connection: ConnectorConnection,
    fn: Callable[[Provider, TokenSet], Awaitable[T]],
) -> T:
    """
    Run `fn(provider, tokens)` with a valid access token.

    Refreshes BEFORE the call when the token is about to expire, and once
    more AFTER a 401 (a token can be revoked early). A connection whose
    refresh fails is marked "error" so the page can ask for a reconnect.
    """
    provider = get_provider(connection.provider)
    if provider is None:
        raise ConnectorError("This provider is no longer supported.")
    try:
        tokens = TokenSet.from_dict(decrypt_tokens(connection.encrypted_tokens))
    except TokenDecryptError as exc:
        _mark_broken(connection, str(exc))
        await db.commit()
        raise ConnectorAuthError(str(exc)) from exc

    if tokens.needs_refresh() and tokens.refresh_token:
        tokens = await _refresh(db, connection, provider, tokens)
    try:
        return await fn(provider, tokens)
    except ConnectorAuthError:
        if not tokens.refresh_token:
            _mark_broken(connection, "The provider no longer accepts this connection.")
            await db.commit()
            raise
        tokens = await _refresh(db, connection, provider, tokens)
        return await fn(provider, tokens)


# ======================================================================
# 3. VALIDATING A FETCHED FILE (the upload rules, for a file on disk)
# ======================================================================


@dataclass
class LocalFile:
    filename: str
    extension: str
    size: int
    sha256: str


def validate_file(path: str, filename: str) -> LocalFile:
    """The upload endpoint's checks, for a file already on disk. Raises ConnectorError."""
    name = uploads.sanitize_filename(filename)
    if not name:
        raise ConnectorError("The file has no usable name.")
    extension = uploads.extension_of(name)
    if extension not in uploads.ALLOWED_EXTENSIONS:
        raise ConnectorError(f"'{name}': file type '.{extension or '?'}' is not supported.")
    size = os.path.getsize(path)
    if size == 0:
        raise ConnectorError(f"'{name}' is empty.")
    if size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise ConnectorError(
            f"'{name}' is larger than the {settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB upload limit."
        )
    problem = uploads.check_content(path, extension)
    if problem:
        raise ConnectorError(f"'{name}': {problem}")
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(uploads.CHUNK_SIZE):
            hasher.update(chunk)
    return LocalFile(filename=name, extension=extension, size=size, sha256=hasher.hexdigest())


async def _fetch_to_temp(db, connection, item: RemoteItem):
    """Download/export `item` to a temp file; returns (path, LocalFile). Caller removes the path."""
    handle, path = tempfile.mkstemp(prefix="docminds-import-")
    os.close(handle)
    try:
        fetched = await call(db, connection, lambda p, t: p.fetch(t, item, path))
        checked = await asyncio.to_thread(validate_file, fetched.path, fetched.filename)
        return path, checked
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise


def _queue(document_id: uuid.UUID) -> None:
    """The ingestion entry point the upload endpoint uses, after the commit."""
    from app.tasks import ingestion

    try:
        ingestion.dispatch(str(document_id))
    except Exception as exc:
        # Redis down: the row is "pending", and the recovery sweep re-queues it.
        logger.warning(
            "Could not queue imported document; the recovery sweep will retry",
            extra={"document_id": str(document_id), "error": repr(exc)},
        )


# ======================================================================
# 4. IMPORT
# ======================================================================


async def import_items(
    db: AsyncSession,
    *,
    connection: ConnectorConnection,
    project: Project,
    user: User,
    item_ids: List[str],
    auto_sync: bool,
    request: Optional[Request] = None,
) -> List[Dict[str, Any]]:
    """
    Import each picked item into `project`. Returns one result per id:
        {"item_id", "status": imported | duplicate | already_imported |
         skipped | error, "document_id", "detail"}

    Each item is committed on its own, so one failure (a file too large, a
    page that vanished) does not undo the others. A full quota stops the rest.
    """
    results: List[Dict[str, Any]] = []
    allowance = await usage.upload_allowance(db, user.org_id)
    storage = get_storage()

    for item_id in item_ids:
        result: Dict[str, Any] = {"item_id": item_id, "status": "error", "document_id": None, "detail": None}
        results.append(result)
        path: Optional[str] = None
        stored_key: Optional[str] = None
        try:
            item = await call(db, connection, lambda p, t, i=item_id: p.get_item(t, i))
            if not item.importable:
                result.update(status="skipped", detail=f"'{item.name}' is a folder or a type that cannot be imported.")
                continue
            linked = (
                await db.execute(
                    select(ConnectorItem.document_id).where(
                        ConnectorItem.project_id == project.id,
                        ConnectorItem.provider == connection.provider,
                        ConnectorItem.external_id == item.id,
                    )
                )
            ).scalar_one_or_none()
            if linked:
                result.update(status="already_imported", document_id=str(linked),
                              detail=f"'{item.name}' is already in this project; use Sync now to update it.")
                continue

            path, checked = await _fetch_to_temp(db, connection, item)
            duplicate = (
                await db.execute(
                    select(Document.id).where(
                        Document.project_id == project.id, Document.duplicate_hash == checked.sha256
                    )
                )
            ).scalar_one_or_none()
            if duplicate:
                result.update(status="duplicate", document_id=str(duplicate),
                              detail=f"'{checked.filename}' is identical to a document already in this project.")
                continue

            allowance.admit(checked.size)
            version = (
                await db.execute(
                    select(func.max(Document.version)).where(
                        Document.project_id == project.id, Document.filename == checked.filename
                    )
                )
            ).scalar() or 0

            stored_key = new_key(project.id, checked.extension)
            try:
                await asyncio.to_thread(storage.save, stored_key, path)
            except StorageError as exc:
                logger.error("Storage operation failed during import: %s", exc)
                stored_key = None
                raise ConnectorError("File storage is unavailable; the file was not imported.") from exc

            document = Document(
                filename=checked.filename,
                file_type=checked.extension,
                file_size=checked.size,
                file_path=stored_key,
                storage_backend=storage.name,
                status="pending",
                duplicate_hash=checked.sha256,
                version=version + 1,
                meta_data={
                    "original_filename": checked.filename,
                    "chunk_strategy": settings.DEFAULT_CHUNK_STRATEGY,
                    "chunk_size": settings.DEFAULT_CHUNK_SIZE,
                    "chunk_overlap": settings.DEFAULT_CHUNK_OVERLAP,
                    "source": {"provider": connection.provider, "external_id": item.id, "web_url": item.web_url},
                },
                project_id=project.id,
            )
            db.add(document)
            await db.flush()
            now = utcnow()
            db.add(
                ConnectorItem(
                    document_id=document.id,
                    connection_id=connection.id,
                    org_id=user.org_id,
                    project_id=project.id,
                    provider=connection.provider,
                    external_id=item.id,
                    external_name=item.name[:512],
                    revision=item.revision,
                    web_url=item.web_url,
                    auto_sync=auto_sync,
                    sync_status=SYNCING,
                    last_synced_at=now,
                    last_checked_at=now,
                )
            )
            # The same action an upload records, so the audit log and the
            # document.uploaded webhook cover imports with no extra code.
            await audit.record(
                db, "document.upload", org_id=user.org_id, user_id=user.id, request=request,
                details={
                    "document_id": document.id,
                    "project_id": project.id,
                    "filename": checked.filename,
                    "file_size": checked.size,
                    "version": version + 1,
                    "source": connection.provider,
                    "external_id": item.id,
                },
            )
            await db.commit()
            stored_key = None  # now owned by the committed row
            _queue(document.id)
            result.update(status="imported", document_id=str(document.id), detail=checked.filename)
        # Every refusal below happens BEFORE anything is added to the session,
        # so there is nothing to roll back (and a rollback would expire the
        # loaded rows this loop still reads).
        except usage.QuotaExceeded as exc:
            result.update(status="error", detail=exc.message)
            break
        except ConnectorError as exc:
            result.update(status="error", detail=exc.message)
        finally:
            if stored_key:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(storage.delete, stored_key)
            if path:
                with contextlib.suppress(OSError):
                    os.remove(path)
    return results


# ======================================================================
# 5. SYNC
# ======================================================================


def effective_status(link: ConnectorItem, document: Optional[Document]) -> str:
    """
    The status to SHOW. "syncing" is written when a new revision is queued;
    once ingestion finishes, the document's own status says how it went --
    so the sync needs no hook inside the ingestion pipeline.
    """
    if link.sync_status == SYNCING and document is not None:
        if document.status == "completed":
            return SYNCED
        if document.status == "failed":
            return SYNC_ERROR
    return link.sync_status


async def sync_link(db: AsyncSession, link: ConnectorItem, *, force: bool = False,
                    user_id: Optional[uuid.UUID] = None) -> str:
    """
    Check one imported document against its source. Returns the outcome:
    unchanged | updated | busy | source_deleted | disconnected | error.
    Commits its own changes.
    """
    now = utcnow()
    link.last_checked_at = now
    connection = await db.get(ConnectorConnection, link.connection_id) if link.connection_id else None
    if connection is None:
        link.sync_status = DISCONNECTED
        await db.commit()
        return "disconnected"
    document = await db.get(Document, link.document_id)
    if document is None:  # pragma: no cover - the CASCADE removes the link first
        return "error"
    if document.status in ("pending", "processing"):
        await db.commit()
        return "busy"

    path: Optional[str] = None
    new_stored: Optional[str] = None
    storage = get_storage()
    try:
        item = await call(db, connection, lambda p, t: p.get_item(t, link.external_id))
        if item.revision and item.revision == link.revision and not force:
            # A finished re-index keeps the outcome it had (a failed one stays
            # visible); anything else is simply current.
            link.sync_status = effective_status(link, document) if link.sync_status == SYNCING else SYNCED
            link.last_error = None
            await db.commit()
            return "unchanged"

        path, checked = await _fetch_to_temp(db, connection, item)
        if checked.sha256 == document.duplicate_hash:
            # A new revision with the same bytes (renamed, re-shared...).
            link.revision = item.revision
            link.external_name = item.name[:512]
            link.sync_status = SYNCED
            link.last_error = None
            await db.commit()
            return "unchanged"

        clash = (
            await db.execute(
                select(Document.id).where(
                    Document.project_id == document.project_id,
                    Document.duplicate_hash == checked.sha256,
                    Document.id != document.id,
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise ConnectorError("The new version is identical to another document in this project.")

        org_id = link.org_id
        allowance = await usage.upload_allowance(db, org_id)
        # This document is being REPLACED: its old size no longer counts.
        allowance.documents -= 1
        allowance.bytes_used -= document.file_size
        allowance.admit(checked.size)

        new_stored = new_key(document.project_id, checked.extension)
        try:
            await asyncio.to_thread(storage.save, new_stored, path)
        except StorageError as exc:
            new_stored = None
            raise ConnectorError("File storage is unavailable; the sync will be retried.") from exc

        old_backend, old_key = document.storage_backend, document.file_path
        document.file_path = new_stored
        document.storage_backend = storage.name
        document.file_size = checked.size
        document.file_type = checked.extension
        document.duplicate_hash = checked.sha256
        document.status = "pending"
        document.error_message = None
        document.processing_started_at = None
        document.attempts = 0
        meta = dict(document.meta_data or {})
        meta.pop("error", None)
        document.meta_data = meta

        link.revision = item.revision
        link.external_name = item.name[:512]
        link.web_url = item.web_url or link.web_url
        link.sync_status = SYNCING
        link.last_synced_at = now
        link.last_error = None
        await audit.record(
            db, "document.sync", org_id=org_id, user_id=user_id,
            details={"document_id": document.id, "filename": document.filename,
                     "source": link.provider, "revision": item.revision},
        )
        await db.commit()
        new_stored = None
        # The old file is no longer referenced by anything.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(get_storage(old_backend).delete, old_key)
        _queue(document.id)
        return "updated"
    # As in import_items: every refusal happens before the document is
    # touched, so these only record the outcome on the link.
    except SourceNotFound as exc:
        link.sync_status = SOURCE_DELETED
        link.last_error = exc.message
        await db.commit()
        return "source_deleted"
    except (ConnectorError, usage.QuotaExceeded) as exc:
        link.sync_status = SYNC_ERROR
        link.last_error = getattr(exc, "message", str(exc))
        await db.commit()
        return "error"
    finally:
        if new_stored:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(storage.delete, new_stored)
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)


async def claim_for_sync(db: AsyncSession, link_id: uuid.UUID, cutoff: datetime.datetime) -> bool:
    """Atomically take a link for this sweep; False if another process has it."""
    result = await db.execute(
        update(ConnectorItem)
        .where(
            ConnectorItem.id == link_id,
            or_(ConnectorItem.last_checked_at.is_(None), ConnectorItem.last_checked_at < cutoff),
        )
        .values(last_checked_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


async def sync_due(session_factory=None, *, now: Optional[datetime.datetime] = None) -> Dict[str, int]:
    """
    The periodic sweep: every auto-sync link not checked for a whole
    interval, oldest first, at most CONNECTOR_SYNC_BATCH_SIZE. Returns a
    count per outcome. Never raises for one bad link.
    """
    factory = session_factory or SessionLocal
    now = now or utcnow()
    cutoff = now - datetime.timedelta(minutes=settings.CONNECTOR_SYNC_INTERVAL_MINUTES)
    counts: Dict[str, int] = {}
    async with factory() as db:
        ids = (
            await db.execute(
                select(ConnectorItem.id)
                .where(
                    ConnectorItem.auto_sync.is_(True),
                    ConnectorItem.connection_id.is_not(None),
                    ConnectorItem.sync_status.not_in((SOURCE_DELETED, DISCONNECTED)),
                    or_(ConnectorItem.last_checked_at.is_(None), ConnectorItem.last_checked_at < cutoff),
                )
                .order_by(ConnectorItem.last_checked_at.asc().nulls_first())
                .limit(settings.CONNECTOR_SYNC_BATCH_SIZE)
            )
        ).scalars().all()
    for link_id in ids:
        async with factory() as db:
            try:
                if not await claim_for_sync(db, link_id, cutoff):
                    continue
                link = await db.get(ConnectorItem, link_id)
                if link is None:
                    continue
                connection = await db.get(ConnectorConnection, link.connection_id) if link.connection_id else None
                if connection is not None and connection.status == ERROR:
                    counts["skipped"] = counts.get("skipped", 0) + 1
                    continue
                outcome = await sync_link(db, link)
            except Exception as exc:  # one broken link must not stop the sweep
                logger.warning("Connector sync failed", extra={"link_id": str(link_id), "error": repr(exc)})
                outcome = "error"
            counts[outcome] = counts.get(outcome, 0) + 1
    if counts:
        logger.info("Connector sync sweep finished", extra={"outcomes": counts})
    return counts


def link_view(link: ConnectorItem, document: Optional[Document]) -> Dict[str, Any]:
    """One imported document as the API reports it."""
    return {
        "document_id": link.document_id,
        "project_id": link.project_id,
        "connection_id": link.connection_id,
        "provider": link.provider,
        "external_id": link.external_id,
        "external_name": link.external_name,
        "filename": document.filename if document else link.external_name,
        "document_status": document.status if document else None,
        "revision": link.revision,
        "web_url": link.web_url,
        "auto_sync": link.auto_sync,
        "sync_status": effective_status(link, document),
        "last_error": link.last_error,
        "last_synced_at": _aware(link.last_synced_at),
        "last_checked_at": _aware(link.last_checked_at),
    }
