"""
storage.py
==========
WHAT THIS FILE DOES
-------------------
Gives the rest of the backend ONE way to keep and fetch the original uploaded
files, whatever is actually holding them:

    LocalStorage   a directory on this machine (the default, backend/uploads)
    S3Storage      any S3-compatible object store: AWS S3, Cloudflare R2,
                   MinIO, Backblaze B2 ...

The upload endpoint, the ingestion worker and the preview endpoint all go
through this interface and never touch `open()` or boto3 directly. Switching
from disk to S3 is then a settings change (STORAGE_BACKEND=s3), not a code
change.

WHY THIS MATTERS
----------------
A cloud container's disk is usually EPHEMERAL: a restart or redeploy wipes it.
With local storage, every original file vanishes on every deploy -- search keeps
working (the text and vectors are in Postgres), but the preview button and any
future re-indexing are broken. An object store survives restarts, can keep old
versions of every object, and can be backed up independently of the server.

KEYS, NOT FILENAMES
-------------------
Every file is stored under a key the SERVER invents:

    <project_id>/<random uuid>.<extension>

The name the user uploaded is never part of it. A client-supplied name is
attacker-controlled text: "../../app/main.py" is a path traversal, "CON" is a
reserved device on Windows, and two users' "report.pdf" would collide. The
original name is kept in the database for display, where it is harmless.

WHICH BACKEND HOLDS WHICH FILE
------------------------------
Each Document row records the backend its file was saved to (the
`storage_backend` column). A deployment that moves from local to S3 therefore
keeps reading its old files from disk while new uploads go to the bucket --
nothing has to be migrated on the day of the switch.

WHY THE METHODS ARE SYNCHRONOUS
-------------------------------
boto3 is a blocking library and so is ordinary file I/O. Callers inside the
async API wrap these calls in `asyncio.to_thread(...)`, which keeps the event
loop free for other requests while a 50 MB upload is copied.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid
from typing import Iterator, Optional

from app.core.config import settings

# How much is read or written at once when copying. Big enough to be fast,
# small enough that a 50 MB file never sits in memory in one piece.
CHUNK_SIZE = 1024 * 1024


# ======================================================================
# ERRORS
# ======================================================================


class StorageError(Exception):
    """Base class for everything that can go wrong in this module."""


class StorageObjectNotFound(StorageError):
    """The key is valid but nothing is stored under it (any more)."""


class StorageUnavailableError(StorageError):
    """
    The store could not be reached: a network failure, a timeout, a 5xx.

    Kept separate from "not found" because the ingestion task RETRIES this one
    -- the object is probably fine and the network will come back -- while a
    missing object will still be missing on the next attempt.
    """


def new_key(project_id: uuid.UUID | str, extension: str) -> str:
    """
    Invent a storage key for a new upload.

    `extension` must already be validated against the upload allowlist; it is
    the only part of the key that came from the client at all.
    """
    extension = extension.lower().lstrip(".")
    return f"{project_id}/{uuid.uuid4().hex}.{extension}"


# ======================================================================
# THE INTERFACE
# ======================================================================


class Storage:
    """What every backend provides. Subclasses implement each method."""

    #: Stored on the Document row so the right backend reads it back later.
    name: str = ""

    def save(self, key: str, source_path: str) -> None:
        """Copy the local file at `source_path` into the store under `key`."""
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        """Remove the object. Deleting something already gone is not an error."""
        raise NotImplementedError

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        """Yield the object's bytes in chunks -- used to stream a preview."""
        raise NotImplementedError

    @contextlib.contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        """
        A path on THIS machine holding the object's bytes, for as long as the
        `with` block lasts.

        The extractors (PyMuPDF, python-docx, ...) all want a file path, not a
        stream. Local storage simply hands back the real path; S3 downloads to
        a temporary file and deletes it afterwards.
        """
        raise NotImplementedError
        yield  # pragma: no cover  (makes this a generator for the decorator)


# ======================================================================
# LOCAL DISK
# ======================================================================


class LocalStorage(Storage):
    """Files in a directory on this machine's disk."""

    name = "local"

    def __init__(self, root: Optional[str] = None):
        self.root = os.path.abspath(root or settings.local_storage_dir)
        os.makedirs(self.root, exist_ok=True)

    def path_for(self, key: str) -> str:
        """
        Turn a key into an absolute path, refusing any that escape the root.

        Documents uploaded before this module existed stored an ABSOLUTE path
        in `file_path`; those are passed through unchanged so old rows keep
        working. They were written by the server itself, never by a client.
        A RELATIVE key is joined to the root and checked: a key containing
        "../" that climbed out of the directory is a bug or an attack, and
        either way it must not be read.
        """
        if os.path.isabs(key):
            return key
        candidate = os.path.abspath(os.path.join(self.root, key))
        if os.path.commonpath([candidate, self.root]) != self.root:
            raise StorageError(f"Refusing a storage key outside the storage root: {key!r}")
        return candidate

    def save(self, key: str, source_path: str) -> None:
        destination = self.path_for(key)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        # Copy, not move: the caller owns `source_path` and cleans it up.
        shutil.copyfile(source_path, destination)

    def exists(self, key: str) -> bool:
        return os.path.isfile(self.path_for(key))

    def delete(self, key: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.path_for(key))

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        path = self.path_for(key)
        if not os.path.isfile(path):
            raise StorageObjectNotFound(key)

        def generate() -> Iterator[bytes]:
            with open(path, "rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk

        return generate()

    @contextlib.contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        path = self.path_for(key)
        if not os.path.isfile(path):
            raise StorageObjectNotFound(key)
        # Already local: no copy needed, and nothing to clean up.
        yield path


# ======================================================================
# S3 AND COMPATIBLES
# ======================================================================


class S3Storage(Storage):
    """
    Objects in an S3-compatible bucket.

    boto3 is imported LAZILY, inside `_make_client`, so a deployment that never
    sets STORAGE_BACKEND=s3 does not need boto3 installed at all, and importing
    this module never fails because of it.

    `client` can be passed in directly; the tests use that to substitute a
    fake, so no bucket or credentials are needed to test this class.
    """

    name = "s3"

    def __init__(self, client=None, bucket: Optional[str] = None, prefix: Optional[str] = None):
        self.bucket = bucket or settings.S3_BUCKET
        self.prefix = settings.S3_KEY_PREFIX if prefix is None else prefix
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    @staticmethod
    def _make_client():
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise StorageError(
                "STORAGE_BACKEND=s3 needs the boto3 package: pip install boto3"
            ) from exc

        kwargs = {
            # boto3's own retries, with backoff, for throttling and 5xx. Our
            # ingestion retries sit on top of these for longer outages.
            "config": Config(retries={"max_attempts": 5, "mode": "standard"}),
        }
        # Every one of these is optional; an empty setting means "let boto3
        # work it out" (IAM role, AWS_* variables, ~/.aws/config).
        if settings.S3_REGION:
            kwargs["region_name"] = settings.S3_REGION
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        if settings.S3_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        return boto3.client("s3", **kwargs)

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    # ---- Error translation --------------------------------------------
    # boto3 raises botocore's own exception types. They are mapped onto ours
    # here so that no caller ever has to import botocore to tell "missing"
    # from "unreachable".
    @staticmethod
    def _translate(exc: Exception, key: str) -> StorageError:
        response = getattr(exc, "response", None) or {}
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return StorageObjectNotFound(key)
        if status is not None and status < 500 and code not in {"SlowDown", "RequestTimeout", "Throttling"}:
            # 403 AccessDenied, 400 bad request ... a configuration problem
            # that waiting will not fix.
            return StorageError(f"S3 refused the request for {key!r}: {code or status}")
        # No HTTP response at all (connection refused, DNS, timeout) or a 5xx
        # or throttling reply: worth trying again.
        return StorageUnavailableError(f"S3 is unavailable for {key!r}: {exc}")

    def save(self, key: str, source_path: str) -> None:
        try:
            # `upload_file` streams from disk and switches to a multipart
            # upload for large files on its own.
            self.client.upload_file(
                source_path,
                self.bucket,
                self._object_key(key),
                ExtraArgs={"ContentType": "application/octet-stream"},
            )
        except Exception as exc:
            raise self._translate(exc, key) from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except Exception as exc:
            translated = self._translate(exc, key)
            if isinstance(translated, StorageObjectNotFound):
                return False
            raise translated from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))
        except Exception as exc:
            translated = self._translate(exc, key)
            if not isinstance(translated, StorageObjectNotFound):
                raise translated from exc

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))
        except Exception as exc:
            raise self._translate(exc, key) from exc

        body = response["Body"]

        def generate() -> Iterator[bytes]:
            try:
                while chunk := body.read(CHUNK_SIZE):
                    yield chunk
            finally:
                body.close()

        return generate()

    @contextlib.contextmanager
    def local_copy(self, key: str) -> Iterator[str]:
        extension = os.path.splitext(key)[1]
        handle, path = tempfile.mkstemp(prefix="docminds-s3-", suffix=extension)
        os.close(handle)
        try:
            try:
                self.client.download_file(self.bucket, self._object_key(key), path)
            except Exception as exc:
                raise self._translate(exc, key) from exc
            yield path
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)


# ======================================================================
# CHOOSING A BACKEND
# ======================================================================

# One instance per backend per process. S3 clients are expensive to create
# (credential lookup, connection pool) and safe to share between threads.
_instances: dict[str, Storage] = {}


def get_storage(name: Optional[str] = None) -> Storage:
    """
    The backend called `name`, or the configured default when `name` is None.

    Pass a Document's `storage_backend` to read that document's file; pass
    nothing to choose where a NEW upload goes.
    """
    name = name or settings.STORAGE_BACKEND
    if name not in _instances:
        if name == "local":
            _instances[name] = LocalStorage()
        elif name == "s3":
            _instances[name] = S3Storage()
        else:
            raise StorageError(f"Unknown storage backend: {name!r}")
    return _instances[name]


def reset_storage_cache() -> None:
    """Forget cached backends -- for tests that change the settings."""
    _instances.clear()
