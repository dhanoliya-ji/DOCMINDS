"""
uploads.py
==========
WHAT THIS FILE DOES
-------------------
Everything the upload endpoint checks about a file BEFORE it is accepted:

    1. its NAME is made safe       sanitize_filename()
    2. its EXTENSION is allowed    ALLOWED_EXTENSIONS
    3. its SIZE is within limits   spool_upload()  -- counted while reading
    4. its CONTENT matches its     check_content()  -- "magic bytes"
       extension

WHY EACH CHECK EXISTS
---------------------
NAME. The filename arrives from the client and is attacker-controlled text.
"../../etc/passwd", a name with a newline in it (which forges log lines), a
2,000-character name (which overflows the 255-character column) -- all are
perfectly legal in a multipart request. We keep a cleaned-up copy for display
and never use it to build a storage path (see services/storage.py).

EXTENSION. Only formats extractor.py can actually read are accepted. Anything
else would be stored, queued, and then fail in the worker anyway -- better to
say so at once, with a 415, while the user is still looking at the upload form.

SIZE. MAX_UPLOAD_SIZE_BYTES used to be a setting nothing enforced. The file is
now read in 1 MB chunks and counted as it goes; the moment it crosses the limit
the upload is refused with 413. It is never read into memory whole.

CONTENT. An extension is just part of a name -- renaming malware.exe to
report.pdf changes nothing about the bytes. Most binary formats begin with a
fixed signature ("magic bytes"): every PDF starts with %PDF-, every PNG with
\\x89PNG. Checking that the bytes agree with the extension catches renamed
files, and stops a parser being fed a format it was never written for --
which is where parser vulnerabilities live.

This is a CONSISTENCY check, not malware detection. A well-formed PDF can still
be malicious; that is what the optional ClamAV scan (services/malware.py) is for.
"""

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

# Read and hash the upload this much at a time.
CHUNK_SIZE = 1024 * 1024

# Room for a long but sane name. The column is 255; a margin is left so a
# version suffix or similar never pushes it over.
MAX_FILENAME_LENGTH = 200

#: Exactly the formats extractor.py has a parser for. Defined there, so adding
#: a parser automatically allows its extension here.
ALLOWED_EXTENSIONS = frozenset(SUPPORTED_EXTENSIONS)


class UploadRejected(Exception):
    """
    A file failed validation. Carries the HTTP status the endpoint should use:

        400  empty file / no usable name
        413  too large
        415  extension not allowed, or content does not match it
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ======================================================================
# 1. FILENAMES
# ======================================================================

# Characters Windows forbids in names, plus the path separators of both
# systems. Replacing them keeps a name readable ("Q3: plan" -> "Q3_ plan").
_UNSAFE_CHARACTERS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(raw: Optional[str]) -> str:
    """
    Turn whatever the client sent into a short, printable display name.

        "C:\\Users\\me\\report.pdf"        -> "report.pdf"
        "../../etc/passwd"                -> "passwd"
        "inv\\x00oice\\n.pdf"             -> "invoice.pdf"
        "   .hidden.txt"                  -> "hidden.txt"

    The result is safe to SHOW and to store in the database. It is still never
    used as a path -- that is the storage key's job.
    """
    name = raw or ""

    # Browsers send only the base name, but other clients may send a full
    # path, from either operating system. Keep only the last component.
    name = re.split(r"[/\\]", name)[-1]

    # NFC normalisation, so "é" written as one code point and "é" written as
    # e + combining accent become the same string -- otherwise two uploads of
    # the same name would not be recognised as versions of one another.
    name = unicodedata.normalize("NFC", name)

    # Drop control characters (newlines, NUL, escape codes) and invisible
    # format characters such as the right-to-left override, which can make
    # "evil\u202Efdp.exe" DISPLAY as "evilexe.pdf".
    # A tab is a control character too, but one that plainly meant "space".
    name = name.replace("\t", " ")
    name = "".join(ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf"})

    name = _UNSAFE_CHARACTERS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()

    # A leading dot makes a hidden file on Unix, and "..": the parent folder.
    name = name.lstrip(". ")

    if len(name) > MAX_FILENAME_LENGTH:
        stem, extension = os.path.splitext(name)
        # Keep the extension: it is what the type is read from.
        extension = extension[:16]
        name = stem[: MAX_FILENAME_LENGTH - len(extension)].rstrip() + extension

    return name


def extension_of(filename: str) -> str:
    """The lowercased extension without its dot, or "" if there is none."""
    return os.path.splitext(filename)[1].lstrip(".").lower()


# ======================================================================
# 2. READING THE UPLOAD, WITH A SIZE LIMIT
# ======================================================================


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
    """
    Copy an UploadFile to a temporary file, hashing and counting as it goes.

    Raises UploadRejected for a bad name, a bad extension, an empty file, an
    oversized file or content that does not match the extension. On any
    rejection the temporary file is removed before raising.

    The SHA-256 is computed in the same pass, so the file is read exactly once
    -- the hash is what deduplication compares.
    """
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


# ======================================================================
# 3. CONTENT SNIFFING
# ======================================================================

# The first bytes of each binary format. Several formats can have more than
# one valid start (TIFF is little- or big-endian; JPEG has variants that all
# share the first three bytes).
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "bmp": (b"BM",),
    "tiff": (b"II*\x00", b"MM\x00*"),
    # The legacy Excel format is an OLE2 "compound file".
    "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}

# Office Open XML files are zip archives. Every one of them has a
# [Content_Types].xml, and the folder inside tells Word, PowerPoint and Excel
# files apart -- so a .docx that is really a renamed .xlsx is caught too.
_OOXML_FOLDER = {"docx": "word/", "pptx": "ppt/", "xlsx": "xl/"}

# Formats that are plain text. They have no signature, so the check is the
# other way round: they must NOT look like any binary format.
_TEXT_FORMATS = {"txt", "md", "markdown", "csv", "html", "htm", "eml"}

# Binary starts that are never acceptable as "text", including a Linux ELF
# executable. (A Windows .exe begins with just "MZ" -- too short to test on its
# own without rejecting real text -- but its header is full of NUL bytes, which
# the NUL check below catches.)
_BINARY_STARTS = (
    b"%PDF-",
    b"PK\x03\x04",
    b"\x7fELF",
    *(sig for sigs in _SIGNATURES.values() for sig in sigs if len(sig) >= 3),
)

# How much of a file is examined. Enough for every signature here, and for a
# representative look at whether a "text" file is really text.
_SNIFF_BYTES = 8192


def check_content(path: str, extension: str) -> Optional[str]:
    """
    Does the file's content match its extension?

    Returns None when it does, or a short human-readable reason when it does
    not. Reading the reason aloud should tell a user what to do about it.
    """
    with open(path, "rb") as handle:
        head = handle.read(_SNIFF_BYTES)

    if extension == "pdf":
        # The PDF specification allows a little junk before the header, and
        # some generators emit a byte-order mark or whitespace first.
        if b"%PDF-" not in head[:1024]:
            return "the file does not look like a PDF."
        return None

    if extension in _SIGNATURES:
        if not head.startswith(_SIGNATURES[extension]):
            return f"the file's contents do not match the .{extension} extension."
        return None

    if extension == "zip" or extension in _OOXML_FOLDER:
        # `is_zipfile` reads the central directory at the END of the file, so
        # it is cheap even for a large archive.
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
        # A NUL byte essentially never appears in real text, and appears
        # constantly in binary formats.
        if b"\x00" in head:
            return (
                f"the file contains binary data, not .{extension} text "
                "(if it is UTF-16 text, save it as UTF-8)."
            )
        return None

    # An allowlisted extension with no rule above would be a bug in this
    # table, not the user's fault: fail closed so it gets noticed.
    return f"no content check is defined for .{extension} files."
