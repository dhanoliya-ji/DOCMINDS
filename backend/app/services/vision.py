"""
vision.py
=========
WHAT THIS FILE DOES
-------------------
It describes a PICTURE in a sentence or two, so the picture becomes searchable.

OCR (ocr.py) reads the words printed in an image. That is all it can do. An
organisation chart, a bar graph, a floor plan or a photo of a damaged part
carries meaning that is not written anywhere on it -- OCR returns a few stray
labels, and a question like "which team reports to the CFO?" finds nothing.

A vision model can look at the image and say what it shows:

    "An organisation chart: the CFO oversees Finance, Procurement and Payroll;
     Payroll has two sub-teams..."

That caption is added to the page's text as

    [Image description] An organisation chart: ...

and from then on it is chunked, embedded and cited exactly like any other text.

OFF BY DEFAULT
--------------
Every caption is a model call: time, and quota on a free tier. So nothing
happens unless VISION_PROVIDER is set to "groq" or "gemini". Even then it is
spent carefully:

    * uploaded image files are captioned;
    * PDF pages only when pictures cover at least VISION_MIN_IMAGE_COVERAGE of
      the page (a scan, a chart, a diagram -- not a logo in a letterhead);
    * at most VISION_MAX_IMAGES_PER_DOCUMENT per document;
    * paced to VISION_REQUESTS_PER_MINUTE (services/pacing.py).

A caption is a BONUS, never a requirement: any failure -- no key, a timeout, a
refusal -- is logged and the document carries on without it.

PROVIDERS
---------
    groq    OpenAI-compatible chat completions with an image_url data URI.
            Uses GROQ_API_KEY (the key that already writes the answers).
    gemini  generateContent with inline image data. Uses GEMINI_API_KEY (the
            key that already makes the embeddings).
VISION_API_KEY, when set, is used instead -- to keep vision on its own quota.

The image is shrunk to at most 1024 px on its longest side and sent as JPEG
before it leaves the server: plenty for a description, and a fraction of the
upload size and memory.
"""

import base64
import io
import logging
from typing import Optional

from app.core.config import settings
from app.services.pacing import RatePacer

logger = logging.getLogger(__name__)

# The models used when VISION_MODEL is empty. Both are on their provider's
# free tier at the time of writing; set VISION_MODEL to override.
DEFAULT_MODELS = {
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "gemini": "gemini-2.5-flash-lite",
}

PROMPT = (
    "Describe this image for a document search index in at most three sentences. "
    "Say what kind of image it is (chart, diagram, table, photo, scanned page...) "
    "and the key facts it shows: labels, numbers, relationships. "
    "Do not speculate beyond what is visible."
)

# Longest side, in pixels, of the image actually sent to the model.
_MAX_SIDE = 1024

# Shared by every caption in this process.
vision_pacer = RatePacer()

# Tests swap in an httpx.MockTransport here; None means the real network.
_transport = None


def enabled() -> bool:
    """Is captioning switched on at all?"""
    return settings.VISION_PROVIDER != "off"


def _model() -> str:
    return settings.VISION_MODEL or DEFAULT_MODELS.get(settings.VISION_PROVIDER, "")


def _api_key() -> str:
    if settings.VISION_API_KEY:
        return settings.VISION_API_KEY
    if settings.VISION_PROVIDER == "groq":
        return settings.GROQ_API_KEY
    if settings.VISION_PROVIDER == "gemini":
        return settings.GEMINI_API_KEY
    return ""


def _shrink_to_jpeg(image_bytes: bytes) -> bytes:
    """Downscale to _MAX_SIDE and re-encode as JPEG, freeing the original."""
    from app.services.ocr import _open_image

    with _open_image(image_bytes) as image:
        image.thumbnail((_MAX_SIDE, _MAX_SIDE))
        rgb = image.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=80)
    rgb.close()
    return out.getvalue()


def caption_image(image_bytes: bytes) -> Optional[str]:
    """
    A short description of the image, or None.

    None means "no caption": vision is off, no key is configured, or the call
    failed. Never raises -- the document must not fail over a caption.
    """
    if not enabled():
        return None
    key = _api_key()
    if not key:
        logger.warning("VISION_PROVIDER=%s but no API key is set; skipping captions.", settings.VISION_PROVIDER)
        return None
    try:
        import httpx

        jpeg = _shrink_to_jpeg(image_bytes)
        encoded = base64.b64encode(jpeg).decode("ascii")
        # A caption is optional, so it never waits long for quota: if the
        # minute is full for more than a minute, skip this one.
        vision_pacer.acquire(1, 0, settings.VISION_REQUESTS_PER_MINUTE, 0, max_wait=60)

        with httpx.Client(timeout=60, transport=_transport) as client:
            if settings.VISION_PROVIDER == "groq":
                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": _model(),
                        "max_tokens": 200,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": PROMPT},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                                    },
                                ],
                            }
                        ],
                    },
                )
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"]
            else:
                response = client.post(
                    "https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{_model()}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={
                        "contents": [
                            {
                                "parts": [
                                    {"text": PROMPT},
                                    {"inline_data": {"mime_type": "image/jpeg", "data": encoded}},
                                ]
                            }
                        ],
                        "generationConfig": {"maxOutputTokens": 200},
                    },
                )
                response.raise_for_status()
                parts = response.json()["candidates"][0]["content"]["parts"]
                text = " ".join(part.get("text", "") for part in parts)
        text = " ".join((text or "").split())
        return text or None
    except Exception as exc:
        logger.warning("Image captioning failed; continuing without it: %s", exc)
        return None


def caption_block(caption: Optional[str]) -> str:
    """The text a caption is indexed as (empty when there is none)."""
    return f"\n[Image description] {caption}\n" if caption else ""
