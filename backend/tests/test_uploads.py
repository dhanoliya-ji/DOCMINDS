"""
test_uploads.py
===============
WHAT THIS FILE TESTS
--------------------
services/uploads.py -- everything checked about a file before it is accepted:

    the filename is made safe          sanitize_filename
    the extension is on the allowlist  ALLOWED_EXTENSIONS
    the size is counted while reading  spool_upload  -> 413
    the bytes match the extension      check_content -> 415

TIER 1: temporary files only. No database, no network.
"""

import io
import os
import zipfile

import pytest

from app.services import uploads
from app.services.extractor import SUPPORTED_EXTENSIONS
from app.services.uploads import (
    ALLOWED_EXTENSIONS,
    UploadRejected,
    check_content,
    sanitize_filename,
    spool_upload,
)


class FakeUpload:
    """The two things spool_upload uses from FastAPI's UploadFile."""

    def __init__(self, filename, data: bytes):
        self.filename = filename
        self._stream = io.BytesIO(data)
        self.reads = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return self._stream.read(size)


def ooxml(folder: str) -> bytes:
    """A minimal zip shaped like a .docx/.pptx/.xlsx."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{folder}document.xml", "<doc/>")
    return buffer.getvalue()


def plain_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "hello")
    return buffer.getvalue()


# ======================================================================
# Filenames
# ======================================================================


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("report.pdf", "report.pdf"),
            ("../../etc/passwd.txt", "passwd.txt"),
            ("C:\\Users\\me\\report.pdf", "report.pdf"),
            ("/var/www/../secret.md", "secret.md"),
            ("inv\x00oice\n.pdf", "invoice.pdf"),
            ("   .hidden.txt", "hidden.txt"),
            ("..", ""),
            ("Q3: plan?.docx", "Q3_ plan_.docx"),
            ("a   lot\tof   space.txt", "a lot of space.txt"),
            (None, ""),
        ],
    )
    def test_cleaning(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_a_right_to_left_override_is_removed(self):
        """'evil\u202efdp.exe' would DISPLAY as 'evilexe.pdf'."""
        assert sanitize_filename("evil\u202efdp.exe") == "evilfdp.exe"

    def test_a_long_name_is_cut_but_keeps_its_extension(self):
        cleaned = sanitize_filename("x" * 1000 + ".pdf")
        assert len(cleaned) <= uploads.MAX_FILENAME_LENGTH
        assert cleaned.endswith(".pdf")

    def test_unicode_is_normalised(self):
        """The same visible name must compare equal, or versioning breaks."""
        assert sanitize_filename("cafe\u0301.txt") == sanitize_filename("caf\u00e9.txt")


class TestAllowlist:
    def test_it_is_exactly_what_the_extractor_reads(self):
        assert ALLOWED_EXTENSIONS == SUPPORTED_EXTENSIONS

    @pytest.mark.parametrize("extension", ["exe", "js", "sh", "svg", "dll", ""])
    def test_dangerous_or_unknown_types_are_not_allowed(self, extension):
        assert extension not in ALLOWED_EXTENSIONS


# ======================================================================
# Content sniffing
# ======================================================================


class TestCheckContent:
    @pytest.mark.parametrize(
        "extension,data",
        [
            ("pdf", b"%PDF-1.7\n..."),
            ("pdf", b"\xef\xbb\xbf%PDF-1.4"),  # a BOM before the header
            ("png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20),
            ("jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20),
            ("jpeg", b"\xff\xd8\xff\xe1" + b"\x00" * 20),
            ("gif", b"GIF89a" + b"\x00" * 20),
            ("bmp", b"BM" + b"\x00" * 20),
            ("tiff", b"II*\x00" + b"\x00" * 20),
            ("tiff", b"MM\x00*" + b"\x00" * 20),
            ("xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20),
            ("docx", ooxml("word/")),
            ("pptx", ooxml("ppt/")),
            ("xlsx", ooxml("xl/")),
            ("zip", plain_zip()),
            ("txt", "plain text, with ünïcödé".encode()),
            ("md", b"# Title\n\nBody"),
            ("csv", b"a,b\n1,2\n"),
            ("html", b"<html><body>hi</body></html>"),
            ("eml", b"From: a@example.com\nSubject: hi\n\nbody"),
        ],
    )
    def test_matching_content_is_accepted(self, tmp_path, extension, data):
        path = tmp_path / f"file.{extension}"
        path.write_bytes(data)
        assert check_content(str(path), extension) is None

    @pytest.mark.parametrize(
        "extension,data",
        [
            ("pdf", b"MZ\x90\x00 this is really an exe"),
            ("pdf", b"just some text"),
            ("png", b"%PDF-1.4"),
            ("jpg", b"\x89PNG\r\n\x1a\n"),
            ("docx", ooxml("xl/")),  # an Excel file renamed .docx
            ("docx", plain_zip()),  # a zip, but not Office
            ("xlsx", b"not a zip at all"),
            ("zip", b"PK but truncated"),
            ("txt", b"%PDF-1.4 binary"),
            ("txt", b"MZ\x90\x00\x03\x00\x00\x00"),  # a Windows .exe
            ("csv", b"\x7fELF\x02\x01"),
            ("html", "<p>utf-16</p>".encode("utf-16")),
        ],
    )
    def test_mismatched_content_is_refused(self, tmp_path, extension, data):
        path = tmp_path / f"file.{extension}"
        path.write_bytes(data)
        assert check_content(str(path), extension)


# ======================================================================
# Spooling, with the size limit
# ======================================================================


class TestSpoolUpload:
    async def test_a_good_file_is_spooled_and_hashed(self):
        import hashlib

        data = b"Refunds within thirty days." * 100
        spooled = await spool_upload(FakeUpload("policy.txt", data), max_bytes=10_000)
        try:
            assert spooled.filename == "policy.txt"
            assert spooled.extension == "txt"
            assert spooled.size == len(data)
            assert spooled.sha256 == hashlib.sha256(data).hexdigest()
            with open(spooled.path, "rb") as handle:
                assert handle.read() == data
        finally:
            spooled.discard()
        assert not os.path.exists(spooled.path)

    async def test_the_name_is_sanitized(self):
        spooled = await spool_upload(FakeUpload("../../evil.txt", b"hello"), max_bytes=100)
        spooled.discard()
        assert spooled.filename == "evil.txt"

    async def test_over_the_limit_is_413_and_stops_reading(self, monkeypatch):
        monkeypatch.setattr(uploads, "CHUNK_SIZE", 10)
        upload = FakeUpload("big.txt", b"a" * 1000)

        with pytest.raises(UploadRejected) as caught:
            await spool_upload(upload, max_bytes=25)

        assert caught.value.status_code == 413
        # It stopped as soon as the limit was crossed -- the rest of the file
        # was never read.
        assert upload.reads == 3

    async def test_exactly_the_limit_is_fine(self):
        spooled = await spool_upload(FakeUpload("edge.txt", b"a" * 25), max_bytes=25)
        spooled.discard()
        assert spooled.size == 25

    @pytest.mark.parametrize(
        "name,data,status",
        [
            ("virus.exe", b"MZ", 415),
            ("noextension", b"text", 415),
            ("fake.pdf", b"not a pdf", 415),
            ("empty.txt", b"", 400),
            ("..", b"text", 400),
        ],
    )
    async def test_rejections(self, name, data, status):
        with pytest.raises(UploadRejected) as caught:
            await spool_upload(FakeUpload(name, data), max_bytes=1000)
        assert caught.value.status_code == status

    async def test_a_rejected_file_leaves_no_temporary_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        with pytest.raises(UploadRejected):
            await spool_upload(FakeUpload("fake.pdf", b"not a pdf"), max_bytes=1000)
        with pytest.raises(UploadRejected):
            await spool_upload(FakeUpload("big.txt", b"a" * 5000), max_bytes=1000)
        assert list(tmp_path.iterdir()) == []
