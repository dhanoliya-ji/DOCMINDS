"""OCR stands for Optical Character Recognition."""
import io
import logging
from typing import Tuple

import fitz  # PyMuPDF
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

SAFE_IMAGE_FORMATS = ("PNG", "JPEG", "TIFF", "BMP", "GIF")


def _open_image(image_bytes: bytes) -> Image.Image:
    """Open uploaded image bytes, allowing only the decoders listed above."""
    return Image.open(io.BytesIO(image_bytes), formats=SAFE_IMAGE_FORMATS)


class OCRService:
    """Reads text out of images and scanned pages."""


    @classmethod
    def ocr_pdf(cls, file_path: str) -> Tuple[str, int]:
        """Rasterise every page of a PDF and run OCR on each one."""
        document = fitz.open(file_path)
        pages_text = []

        try:
            for page_number, page in enumerate(document, 1):
                page_text = cls.ocr_page(page)
                pages_text.append(f"--- PAGE {page_number} (OCR) ---\n{page_text}")

            page_count = len(document)
        finally:
            document.close()

        return "\n\n".join(pages_text), page_count

    @staticmethod
    def page_dpi(page) -> int:
        """The resolution to rasterise this page at: OCR_DPI, lowered if needed."""
        dpi = settings.OCR_DPI
        width_in = page.rect.width / 72
        height_in = page.rect.height / 72
        pixels = width_in * height_in * dpi * dpi
        if pixels > settings.OCR_MAX_PIXELS > 0:
            dpi = int(dpi * (settings.OCR_MAX_PIXELS / pixels) ** 0.5)
        return max(72, dpi)

    @classmethod
    def render_page(cls, page) -> bytes:
        """One page as PNG bytes, at the capped resolution above."""
        pixmap = page.get_pixmap(dpi=cls.page_dpi(page))
        try:
            return pixmap.tobytes("png")
        finally:
            del pixmap

    @classmethod
    def ocr_page(cls, page) -> str:
        """OCR ONE PyMuPDF page. The page-by-page extractor calls this, so a"""
        return cls.ocr_image(cls.render_page(page))


    @classmethod
    def ocr_image(cls, image_bytes: bytes) -> str:
        """Run OCR on one image, using whichever engine is configured."""
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


    @classmethod
    def _ocr_tesseract(cls, image_bytes: bytes) -> str:
        """Google's Tesseract: the most widely used open-source OCR engine."""
        try:
            import pytesseract

            if settings.TESSERACT_CMD != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

            image = _open_image(image_bytes)
            return pytesseract.image_to_string(image)

        except Exception as exc:
            logger.warning(
                f"Tesseract failed or is not installed: {exc}. "
                f"Falling back to mock OCR."
            )
            return cls._ocr_mock(image_bytes)


    @classmethod
    def _ocr_easyocr(cls, image_bytes: bytes) -> str:
        """A pure-Python, deep-learning OCR engine."""
        try:
            import easyocr

            reader = easyocr.Reader(["en"])

            results = reader.readtext(image_bytes, detail=0)
            return "\n".join(results)

        except ImportError:
            logger.warning("easyocr is not installed. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)
        except Exception as exc:
            logger.warning(f"EasyOCR failed: {exc}. Falling back to mock OCR.")
            return cls._ocr_mock(image_bytes)


    @classmethod
    def _ocr_paddleocr(cls, image_bytes: bytes) -> str:
        """Baidu's OCR engine. Particularly strong on dense documents, tables and"""
        try:
            from paddleocr import PaddleOCR

            engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

            import numpy as np

            image = _open_image(image_bytes)
            image_array = np.array(image)

            result = engine.ocr(image_array, cls=True)

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


    @classmethod
    def _ocr_mock(cls, image_bytes: bytes) -> str:
        """Return placeholder text describing the image, without doing real OCR."""
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
