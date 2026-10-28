"""Gives the rest of the backend ONE way to keep and fetch the original uploaded files, whatever is actually..."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid
from typing import Iterator, Optional

from app.core.config import settings

CHUNK_SIZE = 1024 * 1024


class StorageError(Exception):
    """Base class for everything that can go wrong in this module."""


class StorageObjectNotFound(StorageError):
    """The key is valid but nothing is stored under it (any more)."""


class StorageUnavailableError(StorageError):
    """The store could not be reached: a network failure, a timeout, a 5xx."""


def new_key(project_id: uuid.UUID | str, extension: str) -> str:
    """Invent a storage key for a new upload."""
    extension = extension.lower().lstrip(".")
    return f"{project_id}/{uuid.uuid4().hex}.{extension}"


class Storage:
    """What every backend provides. Subclasses implement each method."""

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
        """A path on THIS machine holding the object's bytes, for as long as the"""
        raise NotImplementedError
        yield  # pragma: no cover  (makes this a generator for the decorator)


class LocalStorage(Storage):
    """Files in a directory on this machine's disk."""

    name = "local"

    def __init__(self, root: Optional[str] = None):
        self.root = os.path.abspath(root or settings.local_storage_dir)
        os.makedirs(self.root, exist_ok=True)

    def path_for(self, key: str) -> str:
        """Turn a key into an absolute path, refusing any that escape the root."""
        if os.path.isabs(key):
            return key
        candidate = os.path.abspath(os.path.join(self.root, key))
        if os.path.commonpath([candidate, self.root]) != self.root:
            raise StorageError(f"Refusing a storage key outside the storage root: {key!r}")
        return candidate

    def save(self, key: str, source_path: str) -> None:
        destination = self.path_for(key)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
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
        yield path


class S3Storage(Storage):
    """Objects in an S3-compatible bucket."""

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

        config_options: dict = {
            "retries": {"max_attempts": 5, "mode": "standard"},
            "connect_timeout": 5,
            "read_timeout": 60,
        }
        kwargs: dict = {}
        if settings.S3_REGION:
            kwargs["region_name"] = settings.S3_REGION
        if settings.S3_ENDPOINT_URL:
            endpoint = settings.S3_ENDPOINT_URL.strip().rstrip("/")
            if "://" not in endpoint:
                endpoint = f"https://{endpoint}"
            kwargs["endpoint_url"] = endpoint
            config_options["request_checksum_calculation"] = "when_required"
            config_options["response_checksum_validation"] = "when_required"
        if settings.S3_ACCESS_KEY_ID:
            kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        return boto3.client("s3", config=Config(**config_options), **kwargs)

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    @staticmethod
    def _translate(exc: Exception, key: str) -> StorageError:
        inner = exc
        while not getattr(inner, "response", None) and (inner.__cause__ or inner.__context__):
            inner = inner.__cause__ or inner.__context__
        response = getattr(inner, "response", None) or {}
        if not response and type(exc).__name__ in {"NoCredentialsError", "PartialCredentialsError"}:
            return StorageError(f"S3 credentials are missing or incomplete for {key!r}")
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return StorageObjectNotFound(key)
        if status is not None and status < 500 and code not in {"SlowDown", "RequestTimeout", "Throttling"}:
            return StorageError(f"S3 refused the request for {key!r}: {code or status}")
        return StorageUnavailableError(f"S3 is unavailable for {key!r}: {exc}")

    def save(self, key: str, source_path: str) -> None:
        try:
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


_instances: dict[str, Storage] = {}


def get_storage(name: Optional[str] = None) -> Storage:
    """The backend called `name`, or the configured default when `name` is None."""
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
