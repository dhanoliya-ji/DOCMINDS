"""
test_storage.py
===============
WHAT THIS FILE TESTS
--------------------
services/storage.py -- the one interface for keeping original files:

    LocalStorage   against pytest's tmp_path
    S3Storage      against a FAKE boto3 client, so no bucket, credentials or
                   network are needed. What is under test is OUR code: the
                   key prefixing, the temp-file cleanup, and above all the
                   translation of botocore errors into "missing" (permanent)
                   versus "unavailable" (retry).

TIER 1: no database, no network.
"""

import os
import uuid

import pytest

from app.core.config import Settings, settings
from app.services import storage as storage_module
from app.services.storage import (
    LocalStorage,
    S3Storage,
    StorageError,
    StorageObjectNotFound,
    StorageUnavailableError,
    get_storage,
    new_key,
)


def source_file(tmp_path, data: bytes = b"hello world") -> str:
    path = tmp_path / "source.bin"
    path.write_bytes(data)
    return str(path)


# ======================================================================
# Keys
# ======================================================================


class TestKeys:
    def test_the_key_never_contains_the_client_filename(self):
        project = uuid.uuid4()
        key = new_key(project, "PDF")
        folder, name = key.split("/")
        assert folder == str(project)
        assert name.endswith(".pdf")
        # 32 hex characters of uuid, then the extension: nothing else.
        assert len(name) == len("x" * 32 + ".pdf")

    def test_keys_are_unique(self):
        project = uuid.uuid4()
        assert new_key(project, "txt") != new_key(project, "txt")


# ======================================================================
# Local disk
# ======================================================================


class TestLocalStorage:
    def test_round_trip(self, tmp_path):
        store = LocalStorage(str(tmp_path / "store"))
        store.save("p/a.txt", source_file(tmp_path, b"abc" * 1000))

        assert store.exists("p/a.txt")
        assert b"".join(store.iter_bytes("p/a.txt")) == b"abc" * 1000
        with store.local_copy("p/a.txt") as path:
            assert open(path, "rb").read() == b"abc" * 1000

        store.delete("p/a.txt")
        assert not store.exists("p/a.txt")

    def test_deleting_twice_is_not_an_error(self, tmp_path):
        LocalStorage(str(tmp_path)).delete("never/existed.txt")

    def test_a_missing_object_is_not_found(self, tmp_path):
        store = LocalStorage(str(tmp_path))
        with pytest.raises(StorageObjectNotFound):
            store.iter_bytes("missing.txt")
        with pytest.raises(StorageObjectNotFound):
            with store.local_copy("missing.txt"):
                pass

    @pytest.mark.parametrize("key", ["../outside.txt", "p/../../outside.txt"])
    def test_a_key_cannot_escape_the_root(self, tmp_path, key):
        store = LocalStorage(str(tmp_path / "root"))
        with pytest.raises(StorageError):
            store.path_for(key)

    def test_a_legacy_absolute_path_is_still_readable(self, tmp_path):
        """Rows from before storage keys hold the absolute path on disk."""
        legacy = source_file(tmp_path, b"old upload")
        store = LocalStorage(str(tmp_path / "root"))
        assert store.exists(legacy)
        with store.local_copy(legacy) as path:
            assert path == legacy


# ======================================================================
# S3, against a fake client
# ======================================================================


class ClientError(Exception):
    """Shaped like botocore.exceptions.ClientError: it carries `response`."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class EndpointConnectionError(Exception):
    """Like botocore's: no HTTP response at all."""


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_with: Exception | None = None

    def _maybe_fail(self):
        if self.fail_with:
            raise self.fail_with

    def upload_file(self, path, bucket, key, ExtraArgs=None):  # noqa: N803 -- boto3's spelling
        self._maybe_fail()
        self.objects[(bucket, key)] = open(path, "rb").read()

    def head_object(self, Bucket, Key):  # noqa: N803
        self._maybe_fail()
        if (Bucket, Key) not in self.objects:
            raise ClientError("404", 404)
        return {}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self._maybe_fail()
        self.objects.pop((Bucket, Key), None)

    def get_object(self, Bucket, Key):  # noqa: N803
        self._maybe_fail()
        if (Bucket, Key) not in self.objects:
            raise ClientError("NoSuchKey", 404)
        self.last_body = FakeBody(self.objects[(Bucket, Key)])
        return {"Body": self.last_body}

    def download_file(self, bucket, key, path):
        self._maybe_fail()
        if (bucket, key) not in self.objects:
            raise ClientError("404", 404)
        with open(path, "wb") as handle:
            handle.write(self.objects[(bucket, key)])


@pytest.fixture
def s3():
    client = FakeS3()
    return client, S3Storage(client=client, bucket="docs", prefix="docminds/")


class TestS3Storage:
    def test_round_trip_with_the_prefix(self, tmp_path, s3):
        client, store = s3
        store.save("p/a.pdf", source_file(tmp_path, b"%PDF-1.4 data"))

        assert ("docs", "docminds/p/a.pdf") in client.objects
        assert store.exists("p/a.pdf")
        assert b"".join(store.iter_bytes("p/a.pdf")) == b"%PDF-1.4 data"
        assert client.last_body.closed

        store.delete("p/a.pdf")
        assert not store.exists("p/a.pdf")

    def test_the_local_copy_is_deleted_afterwards(self, tmp_path, s3):
        _, store = s3
        store.save("p/a.txt", source_file(tmp_path, b"text"))
        with store.local_copy("p/a.txt") as path:
            assert open(path, "rb").read() == b"text"
            assert path.endswith(".txt")
        assert not os.path.exists(path)

    def test_missing_is_not_found(self, s3):
        _, store = s3
        with pytest.raises(StorageObjectNotFound):
            store.iter_bytes("nope.txt")
        with pytest.raises(StorageObjectNotFound):
            with store.local_copy("nope.txt"):
                pass

    @pytest.mark.parametrize(
        "error",
        [
            EndpointConnectionError("could not connect"),
            ClientError("InternalError", 500),
            ClientError("SlowDown", 503),
        ],
    )
    def test_outages_are_unavailable_so_they_are_retried(self, tmp_path, s3, error):
        client, store = s3
        client.fail_with = error
        with pytest.raises(StorageUnavailableError):
            store.save("p/a.txt", source_file(tmp_path))

    def test_access_denied_is_a_plain_error_not_a_retry(self, tmp_path, s3):
        client, store = s3
        client.fail_with = ClientError("AccessDenied", 403)
        with pytest.raises(StorageError) as caught:
            store.save("p/a.txt", source_file(tmp_path))
        assert not isinstance(caught.value, StorageUnavailableError)

    def test_deleting_a_missing_object_is_fine(self, s3):
        _, store = s3
        store.delete("never/there.txt")


# ======================================================================
# Choosing a backend
# ======================================================================


class TestGetStorage:
    @pytest.fixture(autouse=True)
    def fresh_cache(self):
        storage_module.reset_storage_cache()
        yield
        storage_module.reset_storage_cache()

    def test_local_is_the_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
        store = get_storage()
        assert isinstance(store, LocalStorage)
        assert store.root == str(tmp_path)

    def test_a_named_backend_wins_over_the_setting(self, monkeypatch):
        """A document written to S3 is read from S3 after a switch back."""
        monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
        monkeypatch.setattr(settings, "S3_BUCKET", "b")
        assert isinstance(get_storage("s3"), S3Storage)

    def test_creating_an_s3_backend_needs_no_network(self, monkeypatch):
        """boto3 is only touched on first use, so construction is free."""
        monkeypatch.setattr(settings, "S3_BUCKET", "b")
        store = get_storage("s3")
        assert store._client is None

    def test_an_unknown_backend_is_an_error(self):
        with pytest.raises(StorageError):
            get_storage("ftp")

    def test_s3_without_a_bucket_refuses_to_start(self):
        with pytest.raises(ValueError, match="S3_BUCKET"):
            Settings(STORAGE_BACKEND="s3", S3_BUCKET="")

    def test_the_default_local_dir_is_backend_uploads(self, monkeypatch):
        monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", "")
        assert settings.local_storage_dir.replace("\\", "/").endswith("backend/uploads")


# ======================================================================
# The real boto3 client's configuration (built, never used: no network)
# ======================================================================


class TestS3ClientConfiguration:
    """
    Creating a boto3 client makes no network call, so the configuration
    `_make_client` produces can be inspected directly.
    """

    @pytest.fixture
    def s3_settings(self, monkeypatch):
        pytest.importorskip("boto3")
        monkeypatch.setattr(settings, "S3_BUCKET", "docs")
        monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "test-key")
        monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "test-secret")
        return monkeypatch

    def test_an_s3_compatible_endpoint_only_sends_required_checksums(self, s3_settings):
        """
        boto3 1.36+ adds a CRC checksum to every upload by default, which
        several S3-compatible stores reject. A custom endpoint means "not
        AWS", so the old behaviour is restored there.
        """
        s3_settings.setattr(settings, "S3_ENDPOINT_URL", "http://localhost:9000")
        s3_settings.setattr(settings, "S3_REGION", "auto")

        client = S3Storage._make_client()

        assert client.meta.endpoint_url == "http://localhost:9000"
        assert client.meta.region_name == "auto"
        assert client.meta.config.request_checksum_calculation == "when_required"
        assert client.meta.config.response_checksum_validation == "when_required"

    def test_aws_keeps_boto3s_defaults(self, s3_settings):
        s3_settings.setattr(settings, "S3_ENDPOINT_URL", "")
        s3_settings.setattr(settings, "S3_REGION", "eu-west-1")

        client = S3Storage._make_client()

        assert "amazonaws.com" in client.meta.endpoint_url
        assert client.meta.config.request_checksum_calculation == "when_supported"

    def test_boto3_retries_throttling_itself(self, s3_settings):
        s3_settings.setattr(settings, "S3_ENDPOINT_URL", "")
        s3_settings.setattr(settings, "S3_REGION", "us-east-1")
        client = S3Storage._make_client()
        assert client.meta.config.retries["mode"] == "standard"
