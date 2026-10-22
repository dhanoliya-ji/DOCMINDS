"""
test_image_formats.py
=====================
WHAT THIS FILE TESTS
--------------------
`SAFE_IMAGE_FORMATS` and `_open_image` in `services/ocr.py` -- the allowlist
that limits Pillow to the image formats DocMinds actually accepts.

WHY IT EXISTS
-------------
Most of Pillow's security advisories are in decoders for formats nobody
uploads to a document tool. EPS is the worst: Pillow hands it to Ghostscript,
and a crafted file can inject arguments into that call. The allowlist keeps
every upload away from those decoders, whatever its file extension claims.

It cuts both ways, and both halves are tested here:
  * every format the extractor routes to OCR must still OPEN, or scanned
    images silently stop being read;
  * everything else must be REFUSED before a decoder runs.

The first half caught a real bug while this was written: listing "MPO" made
Pillow raise KeyError for every format after it, because MPO has no decoder
entry of its own -- it is read through the JPEG one.

TIER 1: images are generated in memory. No database, no network, no Tesseract.
"""

import io

import pytest
from PIL import Image, UnidentifiedImageError

from app.services.ocr import SAFE_IMAGE_FORMATS, _open_image


def _encode(fmt: str, **options) -> bytes:
    """A tiny solid-colour image saved in the given format."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buffer, fmt, **options)
    return buffer.getvalue()


class TestAllowedFormatsOpen:
    """Every image type the extractor sends to OCR must still decode."""

    @pytest.mark.parametrize("fmt", ["PNG", "JPEG", "TIFF", "BMP", "GIF"])
    def test_each_accepted_format_opens(self, fmt):
        assert _open_image(_encode(fmt)).format == fmt

    def test_multi_picture_jpeg_from_a_phone_opens(self):
        """MPO goes through the JPEG decoder, so it must not need its own entry."""
        frames = [Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))]
        buffer = io.BytesIO()
        frames[0].save(buffer, "MPO", save_all=True, append_images=frames[1:])

        assert _open_image(buffer.getvalue()).format == "MPO"

    def test_the_allowlist_names_only_registered_decoders(self):
        """
        An unregistered name raises KeyError inside Image.open for every file
        that reaches it -- the MPO bug described above. Guard against it.
        """
        Image.init()
        for fmt in SAFE_IMAGE_FORMATS:
            assert fmt in Image.OPEN, f"{fmt} has no decoder entry of its own"


class TestEverythingElseIsRefused:
    """Formats DocMinds never accepts must not reach a decoder."""

    def test_eps_is_refused_before_ghostscript(self):
        eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n"

        with pytest.raises(UnidentifiedImageError):
            _open_image(eps)

    @pytest.mark.parametrize("fmt", ["WEBP", "ICO", "PDF"])
    def test_other_real_images_are_refused(self, fmt):
        with pytest.raises(UnidentifiedImageError):
            _open_image(_encode(fmt))
