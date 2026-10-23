"""
ocr.py
======
WHAT THIS FILE DOES
-------------------
OCR stands for Optical Character Recognition. In plain terms:

    "Here is a PICTURE of some text. Tell me what the letters say."

This is what makes scanned documents searchable. A scanned contract is not a
document as far as a computer is concerned -- it is a photograph. There is no
text inside it to extract, only coloured pixels. OCR looks at those pixels and
works out which letters they form.

WHEN THIS RUNS
--------------
Two situations, both triggered from extractor.py:

  1. An image file was uploaded (.png, .jpg, ...). There is nothing but pixels,
     so OCR is the only option.

  2. A PDF turned out to be a scan. extractor.py measures the text it got back
     and, if a PDF averages fewer than 100 characters per page, concludes there
     is no text layer and calls `ocr_pdf` instead.

HOW A PDF PAGE BECOMES AN IMAGE
-------------------------------
OCR engines read images, not PDFs. So each page is RASTERISED first -- drawn
into a bitmap, exactly as it would look on screen -- and that bitmap is handed
to the OCR engine.

The resolution matters. Too low and the letters blur into each other and OCR
guesses wrong; too high and it is slow and memory-hungry for no extra accuracy.
150 DPI is the usual sweet spot for printed text.

THE PROVIDER PATTERN, AND WHY THERE IS A MOCK
---------------------------------------------
Three real engines are supported (Tesseract, EasyOCR, PaddleOCR), chosen by the
`OCR_PROVIDER` setting, plus a mock.

Every one of them falls back to the mock rather than raising. That is
deliberate: Tesseract is a SEPARATE PROGRAM that must be installed on the
operating system -- it is not a Python package, so `pip install` does not
provide it. Without the fallback, anyone cloning this repository without
Tesseract would see their uploads fail with a confusing crash. Instead the
pipeline completes, the document is still chunked and embedded, and the log
says plainly what is missing.
"""

import io
import logging
from typing import Tuple

import fitz  # PyMuPDF
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pillow can decode dozens of image formats, and most of its security
# advisories are in decoders nobody uploads to a document tool: EPS (which
# shells out to Ghostscript), MIC, MSP, XBM, Pillow's own PDF parser, and so
# on. DocMinds only ever accepts the formats below (see extractor.py), so we
# tell Pillow to refuse everything else. A file renamed to .png that is
# really an EPS is then rejected before Ghostscript ever sees it.
#
# The multi-picture JPEGs some phone cameras write (MPO) still open: Pillow
# decodes them through the JPEG entry, which is why MPO is not listed.
SAFE_IMAGE_FORMATS = ("PNG", "JPEG", "TIFF", "BMP", "GIF")


def _open_image(image_bytes: bytes) -> Image.Image:
    """Open uploaded image bytes, allowing only the decoders listed above."""
    # `io.BytesIO` wraps the bytes so Pillow can open them as if they were
    # a file.
    return Image.open(io.BytesIO(image_bytes), formats=SAFE_IMAGE_FORMATS)


class OCRService:
    """Reads text out of images and scanned pages."""

    # ==================================================================
    # PDF -> IMAGES -> TEXT
    # ==================================================================

    @classmethod
    def ocr_pdf(cls, file_path: str) -> Tuple[str, int]:
        """
        Rasterise every page of a PDF and run OCR on each one.

        Returns a tuple of (combined text, page count).

        Each page's text is prefixed with a marker:

            --- PAGE 1 (OCR) ---

        That marker is NOT decoration. chunker.py splits on exactly this
        pattern, which is how a chunk produced from a scanned page still knows
        its page number -- and therefore how a citation for a scanned document
        can say "page 7".
        """
        document = fitz.open(file_path)
        pages_text = []

        try:
            # `enumerate(..., 1)` because page numbering starts at 1 for humans.
            for page_number, page in enumerate(document, 1):
                page_text = cls.ocr_page(page)
                pages_text.append(f"--- PAGE {page_number} (OCR) ---\n{page_text}")

            page_count = len(document)
        finally:
            # Always release the file handle, even if OCR throws part way. On
            # Windows an open handle keeps the file locked so it cannot be
            # deleted or re-processed.
            document.close()

        return "\n\n".join(pages_text), page_count

    @staticmethod
    def page_dpi(page) -> int:
        """
        The resolution to rasterise this page at: OCR_DPI, lowered if needed.

        A page is width x height POINTS (1/72 inch), so at D dots per inch it
        becomes (width * D/72) x (height * D/72) pixels. For an ordinary A4
        page at 150 DPI that is ~2 MP. For an A0 poster it is ~35 MP -- over
        100 MB as raw RGB -- so the DPI is scaled down until the page fits in
        OCR_MAX_PIXELS. Never below 72: lower than that, OCR reads nothing.
        """
        dpi = settings.OCR_DPI
        width_in = page.rect.width / 72
        height_in = page.rect.height / 72
        pixels = width_in * height_in * dpi * dpi
        if pixels > settings.OCR_MAX_PIXELS > 0:
            dpi = int(dpi * (settings.OCR_MAX_PIXELS / pixels) ** 0.5)
        return max(72, dpi)

    @classmethod
    def render_page(cls, page) -> bytes:
        """
        One page as PNG bytes, at the capped resolution above.

        `get_pixmap` is PyMuPDF's renderer -- the same one that draws a PDF on
        screen. The pixmap (raw pixels) is the big object; it is turned into
        compressed PNG bytes in memory -- nothing touches the disk -- and
        dropped at once.
        """
        pixmap = page.get_pixmap(dpi=cls.page_dpi(page))
        try:
            return pixmap.tobytes("png")
        finally:
            del pixmap

    @classmethod
    def ocr_page(cls, page) -> str:
        """
        OCR ONE PyMuPDF page. The page-by-page extractor calls this, so a
        scanned 500-page PDF only ever has one page's pixels in memory.
        """
        return cls.ocr_image(cls.render_page(page))

    # ==================================================================
    # THE DISPATCHER
    # ==================================================================

    @classmethod
    def ocr_image(cls, image_bytes: bytes) -> str:
        """
        Run OCR on one image, using whichever engine is configured.

        Takes raw bytes rather than a file path so the same method serves both
        an uploaded image file and a PDF page rasterised in memory.
        """
        provider = settings.OCR_PROVIDER.lower()

        if provider == "tesseract":
            return cls._ocr_tesseract(image_bytes)
        elif provider == "easyocr":
            return cls._ocr_easyocr(image_bytes)
        elif provider == "paddleocr":
            return cls._ocr_paddleocr(image_bytes)
        elif provider == "mock":
            return cls._ocr_mock(image_bytes)
        else:
            logger.warning(f"Unknown provider '{provider}'. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)

    # ==================================================================
    # ENGINE 1: TESSERACT  -- the default
    # ==================================================================

    @classmethod
    def _ocr_tesseract(cls, image_bytes: bytes) -> str:
        """
        Google's Tesseract: the most widely used open-source OCR engine.

        IMPORTANT: `pytesseract` is only a thin Python WRAPPER. It shells out to
        the actual `tesseract` program, which must be installed separately at
        the operating system level:

            macOS         brew install tesseract
            Ubuntu        sudo apt install tesseract-ocr tesseract-ocr-eng
            Windows       https://github.com/UB-Mannheim/tesseract/wiki
                          then set TESSERACT_CMD in .env to the full .exe path

        The project's Dockerfile installs it automatically, which is why OCR
        works in the deployed container without any manual step.
        """
        try:
            # Imported inside the function so the module still loads on a
            # machine where pytesseract is not installed at all.
            import pytesseract

            # On Windows the binary is rarely on the PATH, so the full path has
            # to be supplied. The default value is the bare command name, so a
            # difference means the user configured something explicit.
            if settings.TESSERACT_CMD != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

            image = _open_image(image_bytes)
            return pytesseract.image_to_string(image)

        except Exception as exc:
            # Usually "tesseract is not installed or it's not in your PATH".
            # We degrade to mock rather than failing the whole ingestion.
            logger.warning(
                f"Tesseract failed or is not installed: {exc}. "
                f"Falling back to mock OCR."
            )
            return cls._ocr_mock(image_bytes)

    # ==================================================================
    # ENGINE 2: EASYOCR
    # ==================================================================

    @classmethod
    def _ocr_easyocr(cls, image_bytes: bytes) -> str:
        """
        A pure-Python, deep-learning OCR engine.

        More accurate than Tesseract on messy real-world images -- photographs,
        skewed scans, unusual fonts -- but much heavier: it pulls in PyTorch and
        downloads model weights on first use.
        """
        try:
            import easyocr

            # `Reader(['en'])` loads the English models. Constructed here rather
            # than at import time so nothing is loaded unless this engine is
            # actually selected.
            reader = easyocr.Reader(["en"])

            # `detail=0` returns just the strings. The default returns bounding
            # boxes and confidence scores too, which we do not need.
            results = reader.readtext(image_bytes, detail=0)
            return "\n".join(results)

        except ImportError:
            logger.warning("easyocr is not installed. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)
        except Exception as exc:
            logger.warning(f"EasyOCR failed: {exc}. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)

    # ==================================================================
    # ENGINE 3: PADDLEOCR
    # ==================================================================

    @classmethod
    def _ocr_paddleocr(cls, image_bytes: bytes) -> str:
        """
        Baidu's OCR engine. Particularly strong on dense documents, tables and
        non-Latin scripts.
        """
        try:
            from paddleocr import PaddleOCR

            # `use_angle_cls=True` first detects and corrects the rotation of
            # each text line, which matters for pages scanned slightly crooked.
            engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

            # PaddleOCR wants a numpy array rather than raw bytes, so we open
            # the image with Pillow and convert.
            import numpy as np

            image = _open_image(image_bytes)
            image_array = np.array(image)

            result = engine.ocr(image_array, cls=True)

            # The result is nested: a list of pages, each a list of detections,
            # each detection being (bounding_box, (text, confidence)). We want
            # only the text, so `res[1][0]`.
            texts = []
            for page_result in result:
                if page_result:
                    for detection in page_result:
                        texts.append(detection[1][0])

            return "\n".join(texts)

        except ImportError:
            logger.warning("paddleocr is not installed. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)
        except Exception as exc:
            logger.warning(f"PaddleOCR failed: {exc}. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)

    # ==================================================================
    # THE MOCK  -- so the pipeline never breaks on a missing dependency
    # ==================================================================

    @classmethod
    def _ocr_mock(cls, image_bytes: bytes) -> str:
        """
        Return placeholder text describing the image, without doing real OCR.

        This exists so that someone cloning the repository WITHOUT Tesseract
        installed still sees the complete pipeline work: the document is
        detected as a scan, "OCR" runs, chunks are produced, embeddings are
        stored and search returns results. Only the words themselves are
        placeholders.

        The alternative -- crashing -- would make it look as though the
        application were broken, when in fact one optional system package is
        simply absent.
        """
        try:
            image = _open_image(image_bytes)
            width, height = image.size
        except Exception:
            width, height = 0, 0

        return (
            f"[OCR Mock Results]\n"
            f"Detected Image layout size: {width}x{height} pixels.\n"
            f"Parsed content: ACME CORPORATION CONFIDENTIAL INGESTION WORKSPACE.\n"
            f"Paragraph 1: Optical Character Recognition ran using "
            f"{settings.OCR_PROVIDER.upper()}.\n"
            f"Paragraph 2: This is placeholder text. Install Tesseract to "
            f"extract the real contents of scanned pages."
        )
