"""Everything the upload endpoint checks about a file BEFORE it is accepted."""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Optional

from app.services.extractor import SUPPORTED_EXTENSIONS

CHUNK_SIZE = 1024 * 1024

MAX_FILENAME_LENGTH = 200

ALLOWED_EXTENSIONS = frozenset(SUPPORTED_EXTENSIONS)


class UploadRejected(Exception):
    """A file failed validation. Carries the HTTP status the endpoint should use:"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


_UNSAFE_CHARACTERS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(raw: Optional[str]) -> str:
    """Turn whatever the client sent into a short, printable display name."""
    name = raw or ""

    name = re.split(r"[/\\]", name)[-1]

    name = unicodedata.normalize("NFC", name)

    name = name.replace("\t", " ")
    name = "".join(ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf"})

    name = _UNSAFE_CHARACTERS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()

    name = name.lstrip(". ")

    if len(name) > MAX_FILENAME_LENGTH:
        stem, extension = os.path.splitext(name)
        extension = extension[:16]
        name = stem[: MAX_FILENAME_LENGTH - len(extension)].rstrip() + extension

    return name


def extension_of(filename: str) -> str:
    """The lowercased extension without its dot, or "" if there is none."""
    return os.path.splitext(filename)[1].lstrip(".").lower()


@dataclass
class SpooledUpload:
    """A validated upload, sitting in a temporary file on this machine."""

    filename: str  # sanitized display name
    extension: str  # allowlisted, lowercase, no dot
    path: str  # the temporary file; delete with `discard()`
    size: int
    sha256: str

    def discard(self) -> None:
        with contextlib.suppress(OSError):
            os.remove(self.path)


async def spool_upload(upload, max_bytes: int) -> SpooledUpload:
    """Copy an UploadFile to a temporary file, hashing and counting as it goes."""
    filename = sanitize_filename(upload.filename)
    if not filename:
        raise UploadRejected(400, "The uploaded file has no usable filename.")

    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadRejected(
            415,
            f"'{filename}': file type '.{extension or '?'}' is not supported. "
            f"Supported types: {allowed}.",
        )

    hasher = hashlib.sha256()
    size = 0
    handle, path = tempfile.mkstemp(prefix="docminds-upload-", suffix=f".{extension}")
    try:
        with os.fdopen(handle, "wb") as out:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejected(
                        413,
                        f"'{filename}' is larger than the "
                        f"{max_bytes // (1024 * 1024)} MB upload limit.",
                    )
                hasher.update(chunk)
                out.write(chunk)

        if size == 0:
            raise UploadRejected(400, f"'{filename}' is empty.")

        problem = check_content(path, extension)
        if problem:
            raise UploadRejected(415, f"'{filename}': {problem}")
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise

    return SpooledUpload(
        filename=filename, extension=extension, path=path, size=size, sha256=hasher.hexdigest()
    )


_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "bmp": (b"BM",),
    "tiff": (b"II*\x00", b"MM\x00*"),
    "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}

_OOXML_FOLDER = {"docx": "word/", "pptx": "ppt/", "xlsx": "xl/"}

_TEXT_FORMATS = {"txt", "md", "markdown", "csv", "html", "htm", "eml"}

_BINARY_STARTS = (
    b"%PDF-",
    b"PK\x03\x04",
    b"\x7fELF",
    *(sig for sigs in _SIGNATURES.values() for sig in sigs if len(sig) >= 3),
)

_SNIFF_BYTES = 8192


def check_content(path: str, extension: str) -> Optional[str]:
    """Does the file's content match its extension?"""
    with open(path, "rb") as handle:
        head = handle.read(_SNIFF_BYTES)

    if extension == "pdf":
        if b"%PDF-" not in head[:1024]:
            return "the file does not look like a PDF."
        return None

    if extension in _SIGNATURES:
        if not head.startswith(_SIGNATURES[extension]):
            return f"the file's contents do not match the .{extension} extension."
        return None

    if extension == "zip" or extension in _OOXML_FOLDER:
        if not zipfile.is_zipfile(path):
            return f"the file is not a valid .{extension} (zip) archive."
        if extension in _OOXML_FOLDER:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
            except zipfile.BadZipFile:
                return f"the file is not a valid .{extension} archive."
            folder = _OOXML_FOLDER[extension]
            if "[Content_Types].xml" not in names or not any(n.startswith(folder) for n in names):
                return f"the file is a zip archive, but not a .{extension} document."
        return None

    if extension in _TEXT_FORMATS:
        if head.startswith(_BINARY_STARTS):
            return f"the file is binary, not a .{extension} text file."
        if b"\x00" in head:
            return (
                f"the file contains binary data, not .{extension} text "
                "(if it is UTF-16 text, save it as UTF-8)."
            )
        return None

    return f"no content check is defined for .{extension} files."
