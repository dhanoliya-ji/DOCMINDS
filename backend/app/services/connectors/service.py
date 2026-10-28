"""Everything between the API and the providers."""
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
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import roles
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.connector import (
    ACTIVE,
    DISCONNECTED,
    ERROR,
    IMPORT_COMPLETED,
    IMPORT_QUEUED,
    IMPORT_RUNNING,
    ITEM_ALREADY_IMPORTED,
    ITEM_DOWNLOADING,
    ITEM_DUPLICATE,
    ITEM_FAILED,
    ITEM_IMPORTED,
    ITEM_PENDING,
    ITEM_SKIPPED,
    ITEM_UNFINISHED,
    SOURCE_DELETED,
    SYNC_ERROR,
    SYNCED,
    SYNCING,
    ConnectorConnection,
    ConnectorImport,
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
    """Run `fn(provider, tokens)` with a valid access token."""
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
        logger.warning(
            "Could not queue imported document; the recovery sweep will retry",
            extra={"document_id": str(document_id), "error": repr(exc)},
        )


def _entry(item_id: str) -> Dict[str, Any]:
    return {"item_id": item_id, "name": None, "status": ITEM_PENDING, "document_id": None, "detail": None}


async def create_import(
    db: AsyncSession,
    *,
    connection: ConnectorConnection,
    project: Project,
    user: User,
    item_ids: List[str],
    auto_sync: bool,
    request: Optional[Request] = None,
) -> ConnectorImport:
    """Record the job (status "queued"); the caller commits and then dispatches it."""
    job = ConnectorImport(
        org_id=user.org_id,
        connection_id=connection.id,
        project_id=project.id,
        user_id=user.id,
        provider=connection.provider,
        auto_sync=auto_sync,
        status=IMPORT_QUEUED,
        items=[_entry(item_id) for item_id in item_ids],
    )
    db.add(job)
    await db.flush()
    await audit.record(
        db, "connector.import", org_id=user.org_id, user_id=user.id, request=request,
        details={"import_id": job.id, "connection_id": connection.id, "project_id": project.id,
                 "provider": connection.provider, "items": len(item_ids)},
    )
    return job


def dispatch_import(import_id: uuid.UUID) -> None:
    """Start the job in whichever way INGESTION_MODE says -- the same split as"""
    from app.tasks import connectors as connector_tasks

    try:
        if settings.INGESTION_MODE == "celery":
            connector_tasks.run_import_task.delay(str(import_id))
        else:
            connector_tasks.inline_import_queue.submit(str(import_id))
    except Exception as exc:
        logger.warning(
            "Could not start the import; the recovery will retry",
            extra={"import_id": str(import_id), "error": repr(exc)},
        )


def _claimable(cutoff: datetime.datetime):
    """Queued, or running with a heartbeat older than `cutoff` (orphaned)."""
    return or_(
        ConnectorImport.status == IMPORT_QUEUED,
        and_(
            ConnectorImport.status == IMPORT_RUNNING,
            or_(ConnectorImport.heartbeat_at.is_(None), ConnectorImport.heartbeat_at < cutoff),
        ),
    )


def _stale_cutoff(now: datetime.datetime) -> datetime.datetime:
    return now - datetime.timedelta(minutes=settings.CONNECTOR_IMPORT_STALE_MINUTES)


async def claim_import(db: AsyncSession, import_id: uuid.UUID, *, now: Optional[datetime.datetime] = None) -> bool:
    """Take the job for this worker; False if another one has it or it is finished."""
    now = now or utcnow()
    result = await db.execute(
        update(ConnectorImport)
        .where(ConnectorImport.id == import_id, _claimable(_stale_cutoff(now)))
        .values(status=IMPORT_RUNNING, heartbeat_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


class _ImportAborted(Exception):
    """Stop the whole job: the connection or project went away."""


async def _import_one(
    db: AsyncSession,
    *,
    job: ConnectorImport,
    connection: ConnectorConnection,
    entry: Dict[str, Any],
    allowance,
    storage,
    save: Callable[[], Awaitable[None]],
) -> None:
    """Import one picked item, recording each step in `entry`. The upload"""
    project_id = job.project_id
    still_connected = (
        await db.execute(select(ConnectorConnection.id).where(ConnectorConnection.id == connection.id))
    ).scalar_one_or_none()
    if still_connected is None:
        raise _ImportAborted("The connection was removed during the import.")

    item = await call(db, connection, lambda p, t, i=entry["item_id"]: p.get_item(t, i))
    entry["name"] = item.name[:512]
    if not item.importable:
        entry.update(status=ITEM_SKIPPED, detail=f"'{item.name}' is a folder or a type that cannot be imported.")
        return
    linked = (
        await db.execute(
            select(ConnectorItem.document_id).where(
                ConnectorItem.project_id == project_id,
                ConnectorItem.provider == connection.provider,
                ConnectorItem.external_id == item.id,
            )
        )
    ).scalar_one_or_none()
    if linked:
        entry.update(status=ITEM_ALREADY_IMPORTED, document_id=str(linked),
                     detail=f"'{item.name}' is already in this project; use Sync now to update it.")
        return

    entry["status"] = ITEM_DOWNLOADING
    await save()

    path: Optional[str] = None
    stored_key: Optional[str] = None
    try:
        path, checked = await _fetch_to_temp(db, connection, item)
        duplicate = (
            await db.execute(
                select(Document.id).where(
                    Document.project_id == project_id, Document.duplicate_hash == checked.sha256
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            entry.update(status=ITEM_DUPLICATE, document_id=str(duplicate),
                         detail=f"'{checked.filename}' is identical to a document already in this project.")
            return

        allowance.admit(checked.size)
        version = (
            await db.execute(
                select(func.max(Document.version)).where(
                    Document.project_id == project_id, Document.filename == checked.filename
                )
            )
        ).scalar() or 0

        stored_key = new_key(project_id, checked.extension)
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
            project_id=project_id,
        )
        db.add(document)
        await db.flush()
        now = utcnow()
        db.add(
            ConnectorItem(
                document_id=document.id,
                connection_id=connection.id,
                org_id=job.org_id,
                project_id=project_id,
                provider=connection.provider,
                external_id=item.id,
                external_name=item.name[:512],
                revision=item.revision,
                web_url=item.web_url,
                auto_sync=job.auto_sync,
                sync_status=SYNCING,
                last_synced_at=now,
                last_checked_at=now,
            )
        )
        await audit.record(
            db, "document.upload", org_id=job.org_id, user_id=job.user_id,
            details={
                "document_id": document.id,
                "project_id": project_id,
                "filename": checked.filename,
                "file_size": checked.size,
                "version": version + 1,
                "source": connection.provider,
                "external_id": item.id,
                "import_id": job.id,
            },
        )
        entry.update(status=ITEM_IMPORTED, document_id=str(document.id), detail=checked.filename)
        await save()  # commits the document, its link and this entry together
        stored_key = None  # now owned by the committed row
        _queue(document.id)
    finally:
        if stored_key:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(storage.delete, stored_key)
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)


def _fail_unfinished(items: List[Dict[str, Any]], message: str) -> None:
    for entry in items:
        if entry["status"] in ITEM_UNFINISHED:
            entry.update(status=ITEM_FAILED, detail=message)


async def run_import(import_id, session_factory=None) -> str:
    """Work one import job to the end. Returns "completed", or "busy" when"""
    import_id = uuid.UUID(str(import_id))
    factory = session_factory or SessionLocal
    async with factory() as db:
        if not await claim_import(db, import_id):
            return "busy"
        job = await db.get(ConnectorImport, import_id)
        if job is None:  # pragma: no cover - deleted between claim and read
            return "busy"
        items = [dict(entry) for entry in job.items or []]

        async def save() -> None:
            job.items = [dict(entry) for entry in items]
            job.heartbeat_at = utcnow()
            await db.commit()

        connection = await db.get(ConnectorConnection, job.connection_id) if job.connection_id else None
        project = await db.get(Project, job.project_id)
        try:
            if connection is None or project is None:
                raise _ImportAborted("The connection or project was removed before the import ran.")
            allowance = await usage.upload_allowance(db, job.org_id)
            storage = get_storage()
            for entry in items:
                if entry["status"] not in ITEM_UNFINISHED:
                    continue  # finished before an interruption
                try:
                    await _import_one(db, job=job, connection=connection, entry=entry,
                                      allowance=allowance, storage=storage, save=save)
                except usage.QuotaExceeded as exc:
                    entry.update(status=ITEM_FAILED, detail=exc.message)
                    _fail_unfinished(items, exc.message)
                    break
                except ConnectorError as exc:
                    entry.update(status=ITEM_FAILED, detail=exc.message)
                await save()
        except _ImportAborted as exc:
            _fail_unfinished(items, str(exc))
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await db.rollback()
            with contextlib.suppress(Exception):
                await asyncio.shield(_requeue(factory, import_id, items))
            raise
        except Exception as exc:
            logger.error("Import job failed", extra={"import_id": str(import_id), "error": repr(exc)})
            with contextlib.suppress(Exception):
                await db.rollback()
            await db.refresh(job)
            _fail_unfinished(items, "The import failed unexpectedly; try again.")
        job.items = [dict(entry) for entry in items]
        job.status = IMPORT_COMPLETED
        job.heartbeat_at = job.finished_at = utcnow()
        await db.commit()
    return "completed"


async def _requeue(factory, import_id: uuid.UUID, items: List[Dict[str, Any]]) -> None:
    for entry in items:
        if entry["status"] == ITEM_DOWNLOADING:
            entry["status"] = ITEM_PENDING
    async with factory() as db:
        await db.execute(
            update(ConnectorImport)
            .where(ConnectorImport.id == import_id)
            .values(status=IMPORT_QUEUED, heartbeat_at=None, items=items)
            .execution_options(synchronize_session=False)
        )
        await db.commit()


async def resume_imports(session_factory=None, *, now: Optional[datetime.datetime] = None) -> int:
    """Send again every unfinished job nobody is working on. Returns how many."""
    factory = session_factory or SessionLocal
    now = now or utcnow()
    async with factory() as db:
        ids = (
            await db.execute(
                select(ConnectorImport.id)
                .where(_claimable(_stale_cutoff(now)))
                .order_by(ConnectorImport.created_at)
                .limit(100)
            )
        ).scalars().all()
    for import_id in ids:
        dispatch_import(import_id)
    if ids:
        logger.info("Resumed cloud imports", extra={"count": len(ids)})
    return len(ids)


async def import_finished(import_id, session_factory=None) -> bool:
    """True when the job is completed or gone (nothing left to retry)."""
    async with (session_factory or SessionLocal)() as db:
        job = await db.get(ConnectorImport, uuid.UUID(str(import_id)))
        return job is None or job.status == IMPORT_COMPLETED


def import_view(job: ConnectorImport) -> Dict[str, Any]:
    """One import job as the API reports it."""
    items = list(job.items or [])
    counts: Dict[str, int] = {}
    for entry in items:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "id": job.id,
        "connection_id": job.connection_id,
        "project_id": job.project_id,
        "provider": job.provider,
        "status": job.status,
        "auto_sync": job.auto_sync,
        "items": items,
        "counts": counts,
        "created_at": _aware(job.created_at),
        "finished_at": _aware(job.finished_at),
    }


async def revoke(connection: ConnectorConnection) -> Dict[str, Any]:
    """Ask the provider to revoke DocMinds' grant, when it has an API for it."""
    provider = get_provider(connection.provider)
    manage_url = getattr(provider, "manage_access_url", None) if provider else None
    if provider is None:
        return {"revoked": False, "manage_url": None, "detail": None}
    try:
        tokens = TokenSet.from_dict(decrypt_tokens(connection.encrypted_tokens))
        revoked = await provider.revoke(tokens)
    except Exception as exc:
        logger.warning(
            "Revoking the connection at the provider failed; the local disconnect stands",
            extra={"provider": connection.provider, "connection_id": str(connection.id), "error": repr(exc)},
        )
        return {
            "revoked": False,
            "manage_url": manage_url,
            "detail": f"{provider.name} could not be reached to revoke access; remove DocMinds there by hand.",
        }
    if revoked:
        return {"revoked": True, "manage_url": None, "detail": None}
    return {
        "revoked": False,
        "manage_url": manage_url,
        "detail": f"{provider.name} has no API to revoke access; remove DocMinds in your {provider.name} account.",
    }


def effective_status(link: ConnectorItem, document: Optional[Document]) -> str:
    """The status to SHOW. "syncing" is written when a new revision is queued;"""
    if link.sync_status == SYNCING and document is not None:
        if document.status == "completed":
            return SYNCED
        if document.status == "failed":
            return SYNC_ERROR
    return link.sync_status


async def sync_link(db: AsyncSession, link: ConnectorItem, *, force: bool = False,
                    user_id: Optional[uuid.UUID] = None) -> str:
    """Check one imported document against its source. Returns the outcome:"""
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
            link.sync_status = effective_status(link, document) if link.sync_status == SYNCING else SYNCED
            link.last_error = None
            await db.commit()
            return "unchanged"

        path, checked = await _fetch_to_temp(db, connection, item)
        if checked.sha256 == document.duplicate_hash:
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
        with contextlib.suppress(Exception):
            await asyncio.to_thread(get_storage(old_backend).delete, old_key)
        _queue(document.id)
        return "updated"
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
    """The periodic sweep: every auto-sync link not checked for a whole"""
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
