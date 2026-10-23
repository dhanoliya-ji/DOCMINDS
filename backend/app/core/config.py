"""
config.py
=========
WHAT THIS FILE IS
-----------------
This is the single place where every "setting" of the whole backend lives.

Think of it as the control panel of the application. Database address, secret
keys, which AI model to use, how many chunks to retrieve -- all of it is here.

HOW IT WORKS (the important idea)
---------------------------------
We use `pydantic-settings`. It does one clever thing for us:

    every attribute you see below can be overridden by an environment variable
    of the SAME NAME, or by a line in the `.env` file.

Example: below we write `POSTGRES_PORT: int = 5433`.
  - If nothing else is provided, the value is 5433.
  - If `.env` contains `POSTGRES_PORT=5432`, the value becomes 5432.
  - If the server has an env var `POSTGRES_PORT=6000`, that wins.

This is why the same code can run on your laptop and on Render without editing
a single line -- only the environment changes.

The `int` / `bool` / `Literal` annotations also VALIDATE the value. If someone
sets `PORT=hello`, the app refuses to start with a clear error message instead
of crashing mysteriously somewhere else later.

WHO USES THIS FILE
------------------
Almost everyone. `from app.core.config import settings` appears in the database
layer, the Celery worker, the security layer, the AI services and the API
routes. There is exactly ONE `settings` object (created on the last line of
this file) and every other module shares it.
"""

import os
import re
from typing import Dict, List, Literal, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The development signing key, committed to the repository on purpose so the
# project runs with zero setup. Because it is PUBLIC, it must never sign
# production tokens -- the startup guard at the bottom of `Settings` compares
# against this exact value.
_DEFAULT_SECRET_KEY = "7e15291b0f19c25f1b1c55bc6d01e1498b671a532321bc36efc0d8f07df590ad"


def _for_asyncpg(url: str) -> str:
    """Translate libpq connection options into what asyncpg understands.

    Neon (and most managed Postgres hosts) hand out URLs ending in
    `?sslmode=require&channel_binding=require`. Those are libpq options:
    psycopg2 (Alembic) reads them happily, but asyncpg rejects both with
    "unexpected keyword argument 'sslmode'". asyncpg spells TLS `ssl`, and has
    no channel binding option at all.
    """
    parts = urlsplit(url)
    pairs = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "channel_binding"
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


# ----------------------------------------------------------------------
# RATE LIMIT STRINGS
# ----------------------------------------------------------------------
# A limit is written the way people say it: "20/minute", "10/hour",
# "5/15minutes". One string per limit keeps the settings readable -- the
# alternative is two numbers per limit (count and window) and twice as many
# environment variables to get wrong.
_RATE_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_RATE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*([a-z]+)\s*$")


def parse_rate_limit(text: str) -> Optional[Tuple[int, int]]:
    """
    Turn "20/minute" into (20, 60): at most 20 requests per 60 seconds.

    An empty string or "off" switches that one limit off and returns None.
    Anything unparseable raises ValueError -- at startup, because Settings
    validates every RATE_LIMIT_* value -- rather than silently meaning
    "unlimited" after a typo like "20/minuet".

    It lives here, not in core/rate_limit.py, because the startup validator
    below needs it, and rate_limit.py itself imports `settings` from this file.
    """
    cleaned = (text or "").strip().lower()
    if cleaned in ("", "off", "none", "disabled"):
        return None
    match = _RATE_PATTERN.match(cleaned)
    if not match or match.group(3) not in _RATE_UNITS:
        raise ValueError(
            f"Rate limit {text!r} is not in the form '<count>/<period>', "
            "for example '20/minute', '10/hour' or '5/15minutes'."
        )
    count = int(match.group(1))
    multiplier = int(match.group(2) or 1)
    window = multiplier * _RATE_UNITS[match.group(3)]
    if count < 1 or window < 1:
        raise ValueError(f"Rate limit {text!r} must allow at least 1 request per period.")
    return count, window


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Tell pydantic-settings HOW to find and read the .env file.
    # ------------------------------------------------------------------
    model_config = SettingsConfigDict(
        # Build an absolute path to `backend/.env`:
        #   __file__     -> backend/app/core/config.py
        #   dirname x1   -> backend/app/core
        #   dirname x2   -> backend/app
        #   dirname x3   -> backend            <-- .env lives here
        # An absolute path is used so the app works no matter which directory
        # you happen to launch `uvicorn` or `celery` from.
        env_file=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
        ),
        env_file_encoding="utf-8",
        # The env var must be UPPERCASE exactly as written below. This prevents
        # confusing near-miss names silently doing nothing.
        case_sensitive=True,
        # If .env contains variables we never defined here (for example ones
        # used only by docker-compose), ignore them instead of crashing.
        extra="ignore",
    )

    # ==================================================================
    # 1. GENERAL APPLICATION IDENTITY
    # ==================================================================
    PROJECT_NAME: str = "DocMinds"

    # Every API route is prefixed with this, e.g. /api/v1/documents/upload.
    # Putting a version in the URL means we could later ship /api/v2 with
    # different behaviour without breaking clients still using v1.
    API_V1_STR: str = "/api/v1"

    # `Literal` restricts the value to exactly these three strings, nothing else.
    ENV: Literal["development", "production", "testing"] = "development"

    # Reported by the /health endpoint as `debug_mode`. Note that SQL echo is
    # controlled by ENV (it is on only when ENV == "development"), not by this.
    DEBUG: bool = True

    # 0.0.0.0 means "listen on every network interface". This is required
    # inside Docker and on Render. Using 127.0.0.1 instead would only accept
    # connections originating inside the container, so the outside world could
    # never reach the API.
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ==================================================================
    # 2. CORS -- which websites may call this API from a browser
    # ==================================================================
    # A browser blocks a page served by site A from calling an API on site B
    # unless that API explicitly allows it. Our Next.js frontend runs on port
    # 3000 while the API runs on 8000, and the browser counts a different port
    # as a different site. So we must list the frontend here.
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",   # Next.js development server
        "http://127.0.0.1:3000",   # the same thing, written as an IP
    ]

    # In production the deployed frontend URL is supplied through this env var.
    # Comma-separated, e.g. "https://my-app.vercel.app,https://foo.com".
    # It is a plain string rather than a list because environment variables can
    # only ever hold strings; the `cors_origins` property below splits it.
    FRONTEND_ORIGIN: str = ""

    # ==================================================================
    # 3. POSTGRESQL -- where all persistent data lives
    # ==================================================================
    POSTGRES_HOST: str = "localhost"
    # 5433 rather than the usual 5432, because docker-compose maps the
    # container's internal 5432 to 5433 on your machine. That avoids a clash
    # with any Postgres you may already have installed natively.
    POSTGRES_PORT: int = 5433
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_DB: str = "document_intelligence"

    # Cloud providers (Render, Neon, Railway, Heroku) hand you ONE ready-made
    # connection string instead of five separate fields. When this is set it
    # overrides the five settings above -- see the URI properties at the bottom
    # of this class for exactly how that override happens.
    DATABASE_URL: str = ""

    # ==================================================================
    # 4. REDIS -- the queue Celery uses to hand jobs to background workers
    # ==================================================================
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # Same idea as DATABASE_URL: hosted Redis gives you one complete URL.
    REDIS_URL_OVERRIDE: str = ""

    # ==================================================================
    # 5. SECURITY / JWT (JSON Web Tokens)
    # ==================================================================
    # SECRET_KEY is what signs the login tokens. Anyone who learns it can forge
    # a token and log in as ANY user. The default below is acceptable for local
    # development only. On a real server, generate a fresh one with:
    #     openssl rand -hex 32
    #
    # With ENV=production the app REFUSES TO START if this is still the
    # committed default, or shorter than 32 characters. See the validator
    # `_refuse_weak_secret_in_production` near the bottom of this class.
    SECRET_KEY: str = _DEFAULT_SECRET_KEY

    # HS256 = sign using one shared secret (symmetric signing). Simple and
    # standard for a single backend that both issues and verifies its tokens.
    ALGORITHM: str = "HS256"

    # Access tokens expire quickly, so a stolen one is only briefly useful.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Refresh tokens live much longer and can ONLY be used to obtain a new
    # access token. This is what stops users being logged out every 30 minutes
    # while still keeping the short-lived access token.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ------------------------------------------------------------------
    # ACCOUNTS: passwords, invites, reset and verification links
    # ------------------------------------------------------------------
    # The shortest password signup and password reset will accept. Length is
    # the single biggest factor in how long a password resists guessing, far
    # more than "must contain a symbol" rules, which mostly produce
    # "Password1!". See `password_problems` in core/security.py for the rules.
    PASSWORD_MIN_LENGTH: int = 10

    # How long a "reset your password" link works. Short, because the link is
    # as good as the password for anyone who reads the email.
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60

    # How long a "confirm your email address" link works.
    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 48

    # How long an invitation to join an organization stays valid.
    INVITE_EXPIRE_DAYS: int = 7

    # When True, an account must confirm its email address before it can sign
    # in. Off by default so local development and the demo work without an
    # inbox; the verification flow itself always runs either way.
    REQUIRE_EMAIL_VERIFICATION: bool = False

    # ------------------------------------------------------------------
    # OUTGOING EMAIL (invites, password reset, email verification)
    # ------------------------------------------------------------------
    # Links inside emails point at the FRONTEND, which owns the pages that
    # read the token out of the URL. Left empty it falls back to the first
    # FRONTEND_ORIGIN, then to http://localhost:3000 -- see `frontend_url`.
    FRONTEND_URL: str = ""

    # With SMTP_HOST empty, emails are not sent at all: the "console" sender
    # writes them to the log instead, so a developer can copy the link from
    # the terminal. Setting SMTP_HOST switches to real delivery -- see
    # services/email.py. Nothing else needs to change.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    # STARTTLS on port 587 is the modern default. Set SMTP_USE_SSL instead for
    # a server that wants TLS from the first byte (usually port 465).
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    EMAIL_FROM: str = "DocMinds <no-reply@docminds.local>"

    # ==================================================================
    # 6. OCR -- reading text out of images and scanned PDFs
    # ==================================================================
    # OCR = Optical Character Recognition: "look at a picture of text and tell
    # me the actual letters".
    OCR_PROVIDER: Literal["tesseract", "easyocr", "paddleocr", "mock"] = "tesseract"

    # Path to the tesseract program. On Linux/Mac "tesseract" is already on the
    # system PATH so this default works. On Windows you normally need the full
    # path, e.g. C:\Program Files\Tesseract-OCR\tesseract.exe
    TESSERACT_CMD: str = "tesseract"

    # A PDF page yielding fewer characters than this is assumed to be a SCAN
    # (a photograph of a page) rather than real digital text, so we run OCR on
    # it. For scale: a normal text page holds 1500-3000 characters, whereas a
    # scanned page extracts almost zero because there is no text layer at all.
    OCR_CHAR_THRESHOLD_PER_PAGE: int = 100

    # Resolution used when converting a PDF page into an image for OCR.
    # Higher = more accurate but slower and more memory-hungry. 150 DPI is the
    # usual sweet spot for printed documents.
    OCR_DPI: int = 150
    # A huge page (an A0 poster, a 300 DPI scan of a broadsheet) rasterised
    # at OCR_DPI can be hundreds of megabytes of pixels. The resolution is
    # lowered for that one page until it fits in this many pixels (~12 MP,
    # about 36 MB as RGB), so one page can never exhaust a 512 MB host.
    OCR_MAX_PIXELS: int = 12_000_000

    # ------------------------------------------------------------------
    # IMAGE UNDERSTANDING (optional) -- services/vision.py
    # ------------------------------------------------------------------
    # OCR reads the WORDS in an image. A chart, a diagram or a photo has
    # meaning beyond its words, and a vision model can describe it. With this
    # on, uploaded images and image-heavy PDF pages get a short caption that
    # is indexed next to their OCR text, so "the org chart" or "the revenue
    # graph" becomes searchable.
    #   off    -> the default; no extra calls, no extra quota
    #   groq   -> a Groq vision model, using GROQ_API_KEY
    #   gemini -> a Gemini model, using GEMINI_API_KEY
    VISION_PROVIDER: Literal["off", "groq", "gemini"] = "off"
    # Empty means the provider's default model (see services/vision.py).
    VISION_MODEL: str = ""
    # Empty means reuse GROQ_API_KEY or GEMINI_API_KEY. Set it only to keep
    # vision on a separate key (and so a separate quota):
    #     VISION_API_KEY=your-key-here
    VISION_API_KEY: str = ""
    # Captions cost a model call each, so they are capped per document...
    VISION_MAX_IMAGES_PER_DOCUMENT: int = 20
    # ...paced like the embeddings...
    VISION_REQUESTS_PER_MINUTE: int = 10
    # ...and only spent on PDF pages where pictures cover at least this
    # fraction of the page (0.3 = 30%). A logo in a letterhead is not worth one.
    VISION_MIN_IMAGE_COVERAGE: float = 0.3

    # ==================================================================
    # 7. EMBEDDINGS -- turning text into numbers for semantic search
    # ==================================================================
    # An "embedding" is a list of numbers (a vector) that represents MEANING.
    # Two sentences with similar meaning get vectors pointing in similar
    # directions, even when they share no words at all. That property is what
    # lets us search by meaning rather than by keyword: a question about
    # "time off policy" can match a paragraph titled "annual leave".
    #
    # Providers:
    #   local   -> sentence-transformers runs ON THIS SERVER. Free, needs no
    #              API key, works offline. This is our default.
    #   openai  -> OpenAI's paid API. Higher quality, requires a paid key.
    #   mock    -> fake but deterministic vectors, used by tests and CI so the
    #              test suite never needs a network connection or a key.
    #   gemini  -> Google's hosted API. Free tier, no PyTorch in memory, so the
    #              API fits a 512 MB free host. Used by the free live demo.
    EMBEDDING_PROVIDER: Literal["local", "openai", "gemini", "mock"] = "local"

    # The local model. all-MiniLM-L6-v2 is small (~90 MB), fast on a plain CPU,
    # and produces 384-number vectors. It downloads automatically on first use
    # and is then cached on disk.
    LOCAL_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Free key: https://aistudio.google.com/apikey. gemini-embedding-001 can
    # shorten its vectors to any size from 128 to 3072, so it is asked for
    # EMBEDDING_DIMENSION numbers and the database column stays the same.
    GEMINI_API_KEY: str = ""
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"

    # >>> IMPORTANT <<<
    # Every vector stored in the database MUST contain exactly this many
    # numbers, because the Postgres column is declared with a fixed width.
    #   all-MiniLM-L6-v2       -> 384
    #   text-embedding-3-small -> 1536
    # If you switch provider you must change this value AND re-embed every
    # existing document, otherwise Postgres rejects the insert with a
    # "expected N dimensions, not M" error.
    EMBEDDING_DIMENSION: int = 384

    # ------------------------------------------------------------------
    # WHERE UPLOADED DOCUMENTS ARE PROCESSED
    # ------------------------------------------------------------------
    #   celery  -> a separate worker process picks the job up from Redis.
    #              The normal setup: heavy OCR never slows the API down.
    #   inline  -> the API processes each upload itself, right after
    #              replying. No worker and no Redis needed -- which is what
    #              lets the live demo run on free hosting, where background
    #              workers are not free.
    INGESTION_MODE: Literal["celery", "inline"] = "celery"

    # How many chunks to hand the embedding model in one call. Batching is far
    # faster than one-at-a-time, but too large a batch exhausts memory.
    EMBEDDING_BATCH_SIZE: int = 32

    # ------------------------------------------------------------------
    # GEMINI FREE-TIER PACING (services/pacing.py, services/embedding.py)
    # ------------------------------------------------------------------
    # Google's free tier enforces PER-MINUTE quotas. Sending requests as fast
    # as possible and retrying on HTTP 429 wastes the quota on rejections and,
    # with a large document, ran out of retries long before the minute reset.
    # So requests are PACED on our side instead: never more than this many
    # texts or (estimated) tokens in any rolling 60 seconds. The defaults sit a
    # little under the free tier's published limits, leaving room for the
    # questions users ask while a document is being embedded.
    #
    # REQUESTS are counted per TEXT, not per HTTP call: a batchEmbedContents
    # call with 25 texts counts as 25. That is the conservative reading of how
    # the free tier bills a batch -- pacing slightly too slowly costs seconds,
    # pacing too fast costs a 429.
    GEMINI_EMBED_REQUESTS_PER_MINUTE: int = 90
    GEMINI_EMBED_TOKENS_PER_MINUTE: int = 25_000
    # When a 429 arrives anyway: wait as long as Google asks (Retry-After or
    # the RetryInfo retryDelay in the error body), else exponential backoff
    # with jitter -- but never longer than this per wait...
    GEMINI_RETRY_MAX_DELAY_SECONDS: int = 90
    # ...and never longer than this in total for one call. When the budget
    # runs out the DOCUMENT is paused (not failed) and resumes later from the
    # chunk it reached. A user's QUESTION gets the much shorter budget below:
    # nobody waits minutes for a search.
    GEMINI_RETRY_BUDGET_SECONDS: int = 300
    GEMINI_QUERY_RETRY_BUDGET_SECONDS: int = 20

    # ------------------------------------------------------------------
    # LARGE DOCUMENTS (tasks/ingestion.py)
    # ------------------------------------------------------------------
    # Chunks are embedded, saved and COMMITTED this many at a time. A crash, a
    # restart or a rate limit loses at most one batch; the next run skips
    # every chunk that already has a vector for the current model.
    INGESTION_EMBED_BATCH_SIZE: int = 50
    # Hard caps per document, with a clear message when exceeded. The chunk cap
    # is what keeps one enormous upload from occupying a free embedding quota
    # for a day: at the default pacing, 3,000 chunks of ~500 tokens take about
    # an hour to embed.
    INGESTION_MAX_PAGES: int = 2000
    INGESTION_MAX_CHUNKS: int = 3000
    # Spreadsheets and CSVs are read this many rows at a time, so a 200,000-row
    # sheet never sits in memory as one string.
    INGESTION_ROWS_PER_SEGMENT: int = 200
    # A paused document (rate-limit budget exhausted) is resumed after this
    # long, doubling each time a resume makes no progress, up to the maximum.
    # After INGESTION_MAX_IDLE_PAUSES resumes in a row with no progress at all
    # it is failed -- with the Re-index button still able to resume it.
    INGESTION_PAUSE_SECONDS: int = 120
    INGESTION_PAUSE_MAX_SECONDS: int = 3600
    INGESTION_MAX_IDLE_PAUSES: int = 24
    # INGESTION_MODE=inline only: how many documents the API processes at the
    # same time. 1 is right for a 512 MB host -- a second large PDF in
    # parallel would double the peak memory for no gain, because both would
    # share the same embedding quota anyway.
    INGESTION_INLINE_CONCURRENCY: int = 1
    # INGESTION_MODE=inline only: how often the API itself looks for paused
    # documents that are due to resume (there is no Celery beat to do it).
    INGESTION_INLINE_SWEEP_SECONDS: int = 60

    # ==================================================================
    # 8. LLM -- the AI that writes the final answer (the "G" in RAG)
    # ==================================================================
    # Groq is a free and extremely fast host for open models such as OpenAI's GPT-OSS.
    # Get a key (no credit card required) at https://console.groq.com/keys
    LLM_PROVIDER: Literal["groq", "mock"] = "groq"

    # ##################################################################
    # ##  PUT YOUR FREE GROQ API KEY IN backend/.env LIKE THIS:       ##
    # ##      GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx              ##
    # ##  NEVER paste the real key into this file -- config.py is    ##
    # ##  committed to GitHub, while .env is git-ignored.            ##
    # ##################################################################
    GROQ_API_KEY: str = ""

    # The specific model Groq should run for us: OpenAI's open-weight
    # gpt-oss-120b (120 billion parameters) -- large enough to reliably obey an
    # instruction like "answer ONLY from the provided context and otherwise say
    # you don't know", and good at the tool calling agent mode relies on.
    # Groq retires models: llama-3.3-70b-versatile was shut down on
    # 2026-08-16, after which every answer failed. gpt-oss-120b is the
    # replacement Groq recommends. https://console.groq.com/docs/deprecations
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # `temperature` controls randomness: 0 always picks the most likely next
    # word (factual, repeatable), 1 is creative and varied. For answering
    # questions about real documents we want facts, so we keep it very low.
    LLM_TEMPERATURE: float = 0.1

    # A hard ceiling on the length of the generated answer, measured in tokens
    # (roughly three-quarters of a word each). Prevents runaway responses.
    # 2048, not 1024: reasoning models such as gpt-oss count their hidden
    # reasoning against this limit too, and a tight cap can cut off the
    # answer itself.
    LLM_MAX_TOKENS: int = 2048

    # ------------------------------------------------------------------
    # AGENT MODE  (services/agent.py, used when a chat request sends
    # mode="agent" instead of the default mode="rag")
    # ------------------------------------------------------------------
    # In agent mode the model is not handed a fixed set of excerpts. It is
    # given TOOLS -- search, list documents, read a section -- and decides
    # for itself which to call and how often before it writes an answer.
    #
    # Which Groq model drives the agent. Left EMPTY it simply reuses
    # GROQ_MODEL above, which is what you want almost always. It exists so you
    # can try a stronger tool-calling model for agent mode only, without
    # changing the cheaper model that answers the everyday "rag" questions.
    AGENT_MODEL: str = ""

    # The hard ceiling on how many times the agent may call the model while
    # answering ONE question. Every tool round-trip is one more call, so this
    # is what stops a confused agent from searching in circles until it has
    # burned through the Groq free-tier rate limit. 6 is enough for "search,
    # search again, read a section, answer" with room to spare.
    AGENT_MAX_STEPS: int = 6

    # ------------------------------------------------------------------
    # WHEN THE MODEL PROVIDER MISBEHAVES  (services/llm.py)
    # ------------------------------------------------------------------
    # A hosted model fails in two very different ways, and they need opposite
    # treatment:
    #
    #   TRANSIENT  a rate limit (429), an overloaded server (5xx), a dropped
    #              connection. The same request a moment later usually works,
    #              so it is retried, waiting longer each time.
    #   PERMANENT  a bad key (401), a malformed request (400). Retrying cannot
    #              fix it, so it is not retried at all.
    #
    # How many times a transient failure is retried on the same model, and the
    # first wait (doubled for each later retry: 1s, 2s, ...). A server that
    # says how long to wait (a Retry-After header) is believed instead.
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 1.0

    # How long one model call may take before it is abandoned. A call that
    # TIMES OUT is not retried on the same model: an overloaded model that did
    # not answer in 30 seconds rarely answers the second time either, and the
    # user is already waiting. It goes straight to the fallback model instead.
    LLM_TIMEOUT_SECONDS: float = 30.0

    # A second Groq model to try when the main one keeps failing -- out of
    # retries, timed out, or RETIRED. That last case is not hypothetical:
    # Groq shut llama-3.3-70b-versatile down on 2026-08-16 and every answer
    # failed until GROQ_MODEL was changed. Groq's rate limits are per model,
    # so a fallback also rides out one model's limit being exhausted.
    # Empty = no fallback. e.g. LLM_FALLBACK_MODEL=openai/gpt-oss-20b
    LLM_FALLBACK_MODEL: str = ""

    # ==================================================================
    # 9. RETRIEVAL -- how the "R" in RAG behaves
    # ==================================================================
    # How many of the most similar chunks to feed the LLM as context.
    #   Too few  -> the answer may be missing information.
    #   Too many -> irrelevant text distracts the model and wastes tokens.
    # 5 is a solid default for document question-answering.
    RETRIEVAL_TOP_K: int = 5

    # Chunks scoring below this are discarded even if they made the top K.
    # The score runs 0..1 where 1 means identical meaning. This threshold is
    # what allows the app to honestly answer "I don't know" instead of
    # inventing something from text that merely happened to be closest.
    RETRIEVAL_MIN_SCORE: float = 0.15

    # ---- Hybrid search (services/retrieval.py) ----
    # Semantic search alone is weak at EXACT terms: a policy number, a product
    # code, a surname. Postgres full-text search is strong at exactly those
    # and weak at paraphrase. With this on, every search runs BOTH and merges
    # the two rankings with reciprocal rank fusion (RRF). Off = vectors only,
    # the original behaviour.
    RETRIEVAL_HYBRID: bool = True

    # How many candidates EACH of the two searches contributes before fusion.
    # Larger finds more, at the cost of a bigger sort. Never below top_k.
    RETRIEVAL_CANDIDATES: int = 20

    # The constant in RRF's 1 / (k + rank). 60 is the value from the original
    # paper (Cormack et al., 2009) and almost everyone's default: large enough
    # that rank 1 does not utterly dominate rank 2, small enough that the top
    # of each list still counts most.
    RETRIEVAL_RRF_K: int = 60

    # ---- Optional reranker ----
    # "off" (default) or "cross-encoder". A cross-encoder reads the question
    # and each candidate TOGETHER and scores how well one answers the other --
    # far more accurate than comparing two independent embeddings, but it
    # needs sentence-transformers and a model download, and costs ~20-100 ms
    # of CPU per search. Only the fused candidates are reranked.
    RERANKER: Literal["off", "cross-encoder"] = "off"
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ---- Follow-up query rewriting (services/query_rewrite.py) ----
    # "and for part-time staff?" embeds to a vector about part-time staff in
    # general -- not about LEAVE for part-time staff, which is what the user
    # means. With this on, a follow-up is rewritten into a standalone search
    # query using the conversation so far (by the model when a key is set, by
    # a simple heuristic otherwise). Only the SEARCH uses the rewrite; the
    # model still sees the user's own words.
    QUERY_REWRITE_ENABLED: bool = True

    # ==================================================================
    # 10. CHUNKING DEFAULTS -- how documents get cut into pieces
    # ==================================================================
    # These apply when an upload request does not specify its own values.
    DEFAULT_CHUNK_STRATEGY: Literal["fixed_size", "sentence", "markdown"] = "fixed_size"

    # The size of each chunk in TOKENS, not characters. 500 tokens is roughly
    # two paragraphs: big enough to contain a complete idea, small enough that
    # its embedding vector stays focused on a single topic.
    DEFAULT_CHUNK_SIZE: int = 500

    # Each chunk repeats the final 50 tokens of the previous chunk. This
    # "overlap" stops a sentence that straddles a boundary from being lost, or
    # split across two chunks so that neither one makes sense on its own.
    DEFAULT_CHUNK_OVERLAP: int = 50

    # ==================================================================
    # 11. FILE UPLOAD LIMITS
    # ==================================================================
    # 100 MB, written as a multiplication so the number stays human-readable.
    # Large files are fine on a small host: the upload is streamed to a
    # temporary file, and ingestion reads it a page (or a batch of rows) at a
    # time -- see tasks/ingestion.py. Enforced PER FILE while the upload is read in chunks (services/uploads.py),
    # so an oversized file is refused with 413 as soon as it crosses the line,
    # not after the whole thing has been read into memory.
    MAX_UPLOAD_SIZE_BYTES: int = 100 * 1024 * 1024

    # How many files one upload request may carry. Together with the per-file
    # limit this sets a ceiling on the WHOLE request body, which middleware in
    # main.py checks before the multipart body is even parsed -- so a 10 GB
    # POST is turned away at the door instead of being spooled to disk first.
    MAX_FILES_PER_UPLOAD: int = 20

    # ------------------------------------------------------------------
    # ZIP SAFETY (services/extractor.py)
    # ------------------------------------------------------------------
    # A "zip bomb" is a tiny archive that expands to gigabytes -- 42 KB can
    # unpack to 4.5 PB. Without limits, one upload fills the worker's disk.
    # Each of these closes a different variant of that attack:
    #
    # most members one archive may hold (millions of empty files is a bomb too)
    ZIP_MAX_ENTRIES: int = 1000
    # total bytes ALL members may expand to, counted while actually unpacking
    # (the sizes an archive declares about itself can be lies)
    ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES: int = 500 * 1024 * 1024
    # uncompressed / compressed for any one member. Real documents compress
    # 2-10x; text maybe 20x. A bomb is in the thousands.
    ZIP_MAX_COMPRESSION_RATIO: int = 100
    # How deep archives inside archives are followed. 1 = a zip inside the
    # uploaded zip is opened; a zip inside THAT is skipped. Recursive nesting
    # is the classic way to multiply a bomb's expansion.
    ZIP_MAX_NESTING_DEPTH: int = 1

    # ==================================================================
    # 12. FILE STORAGE -- where the original uploaded bytes live
    # ==================================================================
    #   local -> a directory on this machine's disk (backend/uploads by
    #            default). Zero setup, but a cloud container's disk is often
    #            wiped on restart, taking every original with it.
    #   s3    -> any S3-compatible object store: AWS S3, Cloudflare R2,
    #            MinIO, Backblaze B2. Survives restarts, can be versioned and
    #            backed up. Needs `boto3` (in requirements.txt).
    # See services/storage.py.
    STORAGE_BACKEND: Literal["local", "s3"] = "local"

    # Empty means backend/uploads. Set an absolute path to put files on a
    # mounted volume instead.
    LOCAL_STORAGE_DIR: str = ""

    # Only read when STORAGE_BACKEND=s3. The bucket is required; the others
    # are optional:
    #   S3_REGION        e.g. "eu-west-1"; "auto" for Cloudflare R2
    #   S3_ENDPOINT_URL  empty for AWS; the provider's URL for R2/MinIO/B2,
    #                    e.g. https://<account>.r2.cloudflarestorage.com
    #   S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
    #                    empty means boto3's normal credential chain (an IAM
    #                    role, AWS_* environment variables, ~/.aws).
    #   S3_KEY_PREFIX    prepended to every object key, so one bucket can be
    #                    shared, e.g. "docminds/".
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_KEY_PREFIX: str = ""

    # ==================================================================
    # 13. INGESTION DURABILITY -- what happens when processing goes wrong
    # ==================================================================
    # A TRANSIENT failure (database blip, embedding API rate limit, object
    # store timeout) is retried this many times, waiting longer each time. A
    # PERMANENT failure (corrupt file, no text, unsupported type) is not
    # retried at all -- doing the same thing again would only fail the same way.
    INGESTION_MAX_RETRIES: int = 3
    # The first wait; each retry doubles it -- 10s, 20s, 40s by default --
    # capped at the maximum below. (With Celery each wait is also randomised
    # between zero and that value, so a recovered service is not hit by every
    # waiting document at the same instant.) Ten seconds rather than one or
    # two because the outages worth retrying last that long: a per-minute
    # rate limit resetting, ClamAV reloading its definitions, a database
    # failing over. Retries that are all over in a few seconds would fail the
    # document before most of them had ended.
    INGESTION_RETRY_BACKOFF_SECONDS: int = 10
    INGESTION_RETRY_BACKOFF_MAX_SECONDS: int = 600

    # Celery kills a task that runs too long. The SOFT limit raises an
    # exception inside the task so it can record "timed out" on the document;
    # the HARD limit is the backstop that kills the process if even that hangs.
    INGESTION_SOFT_TIME_LIMIT_SECONDS: int = 1500
    INGESTION_TIME_LIMIT_SECONDS: int = 1800

    # A document still "processing" (or "pending") this long after it was last
    # touched is assumed to be orphaned -- the worker died, the API restarted
    # mid-job, the queue was flushed -- and is put back on the queue by the
    # recovery routine. Must be comfortably LONGER than the hard time limit,
    # or a slow-but-alive job would be started a second time.
    INGESTION_STALE_AFTER_MINUTES: int = 45

    # How often Celery beat runs the recovery routine. It also runs once when
    # the API starts, unless INGESTION_RECOVER_ON_STARTUP is false.
    INGESTION_RECOVERY_INTERVAL_MINUTES: int = 10
    INGESTION_RECOVER_ON_STARTUP: bool = True

    # ==================================================================
    # 14. MALWARE SCANNING (optional) -- services/malware.py
    # ==================================================================
    #   off    -> no scanning (the default; nothing extra to install)
    #   clamav -> every upload is streamed to a ClamAV daemon (clamd) before
    #             it is processed. An infected file is marked failed and
    #             quarantined, and its bytes are deleted from storage.
    # If clamd is unreachable the job is retried and finally FAILS -- an
    # unscanned file is never processed when scanning is switched on.
    MALWARE_SCAN: Literal["off", "clamav"] = "off"
    # TCP address of clamd (port 3310 is its standard). CLAMAV_SOCKET, if set,
    # is a Unix socket path and wins over host/port.
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    CLAMAV_SOCKET: str = ""
    CLAMAV_TIMEOUT_SECONDS: int = 60

    # ==================================================================
    # 15. RATE LIMITING -- core/rate_limit.py
    # ==================================================================
    # Caps how often one caller may hit an expensive or sensitive endpoint.
    # Over the limit the request is refused with 429 Too Many Requests and a
    # Retry-After header saying how many seconds to wait.
    #
    # Two kinds of "caller":
    #   per IP ADDRESS  for the sign-in endpoints, where there is no user yet.
    #                   This is what slows down password guessing and
    #                   sign-up spam.
    #   per USER        for chat, search and upload, the endpoints that cost
    #                   real money (model tokens) or real CPU (OCR, embedding).
    #
    # The off switch exists for tests and emergencies; leave it on.
    RATE_LIMIT_ENABLED: bool = True

    # Where the counters live:
    #   auto   -> Redis when it is reachable, so every API process shares ONE
    #             set of counters; otherwise an in-memory store inside each
    #             process (and Redis is tried again every 30 seconds).
    #   memory -> always in-memory. Fine for a single process; with several
    #             (`uvicorn --workers 4`) each keeps its own counters, so the
    #             effective limit is multiplied by the number of processes.
    RATE_LIMIT_STORAGE: Literal["auto", "memory"] = "auto"

    # "<count>/<period>": "20/minute", "10/hour", "5/15minutes". Empty or
    # "off" switches that one limit off. Checked at startup.
    #
    # Per IP. Generous enough for an office of people behind one shared
    # address, tight enough that guessing a 10-character password is hopeless.
    RATE_LIMIT_LOGIN: str = "20/minute"
    RATE_LIMIT_SIGNUP: str = "10/hour"
    # Request AND confirm share this one. The request sends an email, so this
    # also stops the endpoint being used to flood someone's inbox.
    RATE_LIMIT_PASSWORD_RESET: str = "10/hour"
    # Per user.
    # "Resend the confirmation email": each call sends an email, so keep it low.
    RATE_LIMIT_VERIFY_RESEND: str = "5/hour"
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_SEARCH: str = "60/minute"
    RATE_LIMIT_UPLOAD: str = "30/minute"
    # Per IP: anonymous visitors to public share links (/share/<token>).
    # Generous for people reading; a script guessing 256-bit tokens never
    # finds one anyway, this only keeps it from costing database time.
    RATE_LIMIT_SHARE_VIEW: str = "120/minute"
    # Per user: a document comparison is one large model call.
    RATE_LIMIT_COMPARE: str = "10/minute"

    # ------------------------------------------------------------------
    # WHO IS THE CLIENT? (behind a proxy, the TCP peer is the proxy)
    # ------------------------------------------------------------------
    # On Render, Heroku, behind nginx or a load balancer, every request
    # reaches the API FROM THE PROXY. Per-IP limits would then lump every
    # user together into one bucket. The real address travels in the
    # X-Forwarded-For header -- but any client can also SEND that header, so
    # it may only be believed as far as proxies you control wrote it.
    #
    # TRUSTED_PROXY_COUNT: how many proxies you control sit in front of the
    # API. Each appends the address it received the request from, so with N
    # proxies the client's real address is the Nth entry FROM THE RIGHT --
    # the leftmost entries are whatever the client claimed. 0 (the default)
    # ignores the header and uses the TCP peer, which is right when nothing
    # sits in front of the API.
    TRUSTED_PROXY_COUNT: int = 0

    # Alternatively, a header your proxy or CDN OVERWRITES with the client's
    # address, e.g. "CF-Connecting-IP" (Cloudflare) or "True-Client-IP". Wins
    # over TRUSTED_PROXY_COUNT when present. Only set it if every request
    # really passes through that proxy -- otherwise a client can send the
    # header itself and pick any address it likes.
    CLIENT_IP_HEADER: str = ""

    # ==================================================================
    # 16. USAGE METERING AND QUOTAS -- services/usage.py
    # ==================================================================
    # Every chat answer records a usage event: the model, the tokens it read
    # (input) and wrote (output), and an estimated cost. Admins see the
    # month's totals at GET /api/v1/org/usage.
    #
    # The quotas below are PER ORGANIZATION. 0 means unlimited. Each can be
    # overridden for one organization with the matching column on the
    # `organizations` table (NULL there = use these defaults), which is how
    # an operator gives one customer a bigger plan without a redeploy -- see
    # docs/DEPLOYMENT.md, "Quotas".
    #
    # AI tokens per calendar month (UTC), across every member. Over it, chat
    # answers 429 with Retry-After pointing at the 1st of next month.
    QUOTA_MONTHLY_TOKENS: int = 1_000_000
    # Documents the organization may hold, and the bytes they may add up to.
    # An upload that would cross either answers 402 and stores nothing.
    QUOTA_MAX_DOCUMENTS: int = 1000
    QUOTA_MAX_STORAGE_BYTES: int = 5 * 1024 * 1024 * 1024

    # What each model costs, in US dollars per MILLION tokens, split into
    # input (the prompt: excerpts, history, question) and output (the answer).
    # Used only to ESTIMATE cost on the usage report -- the provider's invoice
    # is the real number. Prices from https://console.groq.com/docs/models
    # (October 2026). A model missing from this table is costed at 0 and
    # logged once, so add your model here if you switch. From the
    # environment, set it as JSON:
    #   LLM_PRICING='{"openai/gpt-oss-120b": {"input": 0.15, "output": 0.60}}'
    LLM_PRICING: Dict[str, Dict[str, float]] = {
        "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
        "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},
    }

    # ==================================================================
    # 17. OBSERVABILITY -- core/observability.py, core/middleware.py
    # ==================================================================
    # Every log line is one JSON object -- time, level, message, plus the
    # request id, user and organization it belongs to -- so a log platform
    # can filter "everything request 3f9c... did" without regular expressions.
    # "text" prints plain lines instead, easier to read in a local terminal.
    LOG_FORMAT: Literal["json", "text"] = "json"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Error reporting with Sentry (optional, off by default). Set the DSN
    # from your Sentry project to switch it on for the API and the worker;
    # `sentry-sdk` is in requirements.txt but only imported when this is set.
    SENTRY_DSN: str = ""
    # Fraction of requests whose performance is traced too (0.0 - 1.0).
    # Errors are always reported; tracing everything costs Sentry quota.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # Tags every event; empty means the value of ENV.
    SENTRY_ENVIRONMENT: str = ""

    # GET /api/v1/health/ready waits at most this long for EACH dependency
    # (database, Redis, a Celery worker) before reporting it down.
    READINESS_TIMEOUT_SECONDS: float = 3.0

    # Strict-Transport-Security: tells browsers to use HTTPS for this API for
    # this many seconds, even if a link says http://. Sent only with
    # ENV=production -- on localhost over plain HTTP it would be ignored at
    # best. 0 switches it off.
    HSTS_MAX_AGE_SECONDS: int = 365 * 24 * 60 * 60

    # ==================================================================
    # 18. SINGLE SIGN-ON (optional) -- api/v1/sso.py
    # ==================================================================
    # "Continue with SSO" through any OpenID Connect provider: Google,
    # Microsoft Entra ID, Okta, Auth0, Keycloak... OIDC is the same protocol
    # for all of them, so ONE generic implementation covers every provider --
    # the provider is chosen entirely by the DISCOVERY URL, a JSON document
    # every OIDC provider publishes that lists its other addresses:
    #   Google     https://accounts.google.com/.well-known/openid-configuration
    #   Microsoft  https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
    #   Okta       https://<your-org>.okta.com/.well-known/openid-configuration
    #
    # Off by default. Switching it on needs all three of the values below,
    # which you get by registering DocMinds as an "application" (Google calls
    # it an "OAuth client") with the provider.
    OIDC_ENABLED: bool = False
    OIDC_DISCOVERY_URL: str = ""
    OIDC_CLIENT_ID: str = ""
    # A secret, exactly like a password: .env or the host's secret store only.
    OIDC_CLIENT_SECRET: str = ""
    # What we ask the provider for. "openid" is mandatory; "email" is what
    # account linking needs; "profile" is harmless and some providers want it.
    OIDC_SCOPES: str = "openid email profile"
    # The label on the login button: "Continue with Google", "... with Okta".
    OIDC_PROVIDER_NAME: str = "SSO"
    # The address the provider sends the browser back to. It must be
    # registered with the provider EXACTLY. Empty = derived from the request
    # (<this API>/api/v1/auth/sso/callback), which is right unless a proxy
    # rewrites the host.
    OIDC_REDIRECT_URI: str = ""
    # Someone signing in with SSO for the first time, with no account and no
    # invite: may they create a NEW organization of their own (exactly what
    # the normal signup form allows)? They can never join an EXISTING
    # organization without an invite -- that rule is not configurable.
    OIDC_ALLOW_SIGNUP: bool = True
    # Accounts are matched by email, so the email must be one the provider
    # has VERIFIED (the `email_verified` claim). Google and Okta send it.
    # Microsoft Entra ID does NOT send it for work accounts -- with Entra,
    # set this to false, which is safe only because Entra itself controls
    # the addresses in its directory. Never turn it off for a provider where
    # users can type in any email they like.
    OIDC_REQUIRE_EMAIL_VERIFIED: bool = True

    # ==================================================================
    # 19. MULTILINGUAL EMBEDDINGS (optional) -- services/embedding.py
    # ==================================================================
    # all-MiniLM-L6-v2 (the default local model) understands English only: a
    # German question will not find the German paragraph that answers it.
    # Switching this on makes the LOCAL provider use MULTILINGUAL_EMBEDDING_MODEL
    # instead, which places 50+ languages in ONE shared vector space -- a
    # French question can even find the English passage that answers it.
    #
    # paraphrase-multilingual-MiniLM-L12-v2 also makes 384-number vectors, so
    # the vector(384) column fits unchanged and no migration is needed.
    #
    # >>> SWITCHING REQUIRES RE-INDEXING <<<  Vectors from two models are
    # not comparable. Documents embedded by the old model stay searchable
    # only by the old model -- reprocess every document after switching (see
    # docs/DEVELOPMENT.md, "Multilingual embeddings").
    EMBEDDING_MULTILINGUAL: bool = False
    MULTILINGUAL_EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # ==================================================================
    # 20. QUERY-EMBEDDING CACHE (optional) -- services/embedding.py
    # ==================================================================
    # Users ask the same questions again and again ("what is the leave
    # policy?"). Embedding a question costs ~20 ms of CPU locally, or a paid
    # API call with OpenAI/Gemini. The cache keeps each QUESTION's vector so
    # the second identical question skips the model entirely.
    #
    # Only questions are cached -- document chunks are embedded once at upload
    # and never asked for again. The key includes the model name and size, so
    # switching model can never return a vector from the previous one.
    #
    #   auto   -> Redis when reachable (shared by every API process), falling
    #             back to an in-memory LRU inside each process.
    #   memory -> in-memory LRU only.
    EMBEDDING_CACHE_ENABLED: bool = False
    EMBEDDING_CACHE_BACKEND: Literal["auto", "memory"] = "auto"
    # How many questions the in-memory LRU remembers (384 floats ~ 3 KB each).
    EMBEDDING_CACHE_MAX_ENTRIES: int = 2048
    # How long a cached vector lives, in Redis and in memory.
    EMBEDDING_CACHE_TTL_SECONDS: int = 24 * 60 * 60

    # ==================================================================
    # 21. WEBHOOKS (optional) -- services/webhooks.py, api/v1/webhooks.py
    # ==================================================================
    # Org admins register an HTTPS URL and the events they care about
    # (document.processed, member.joined, ...). Each event is POSTed there as
    # JSON, signed with HMAC-SHA256 so the receiver can prove it came from us,
    # and retried with growing waits by Celery when the receiver is down.
    # Every attempt is kept in a delivery log admins can read.
    WEBHOOKS_ENABLED: bool = False
    # How many times one delivery is attempted in total, and the first wait
    # between attempts (doubled each time: 30s, 60s, 120s ... capped at 1h).
    WEBHOOK_MAX_ATTEMPTS: int = 6
    WEBHOOK_RETRY_BACKOFF_SECONDS: int = 30
    # How long the receiver gets to answer one POST.
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    # SSRF PROTECTION. A webhook makes OUR server send a request to an address
    # a customer typed in. Without a check, "http://169.254.169.254/" (the
    # cloud metadata service, which hands out credentials) or
    # "http://localhost:5432" would be reachable by anyone who is an admin of
    # any organization. So every URL is resolved and refused if ANY address
    # it resolves to is private, loopback, link-local or reserved -- at
    # registration AND again at every delivery (DNS can change in between).
    # Only switch this on for local testing against a receiver on your own
    # machine; never in production.
    WEBHOOK_ALLOW_PRIVATE_TARGETS: bool = False
    # Plain http:// targets. Off: the signed payload travels encrypted only.
    WEBHOOK_ALLOW_HTTP: bool = False
    # Most endpoints one organization may register.
    WEBHOOK_MAX_ENDPOINTS_PER_ORG: int = 10

    # ==================================================================
    # 22. CLOUD IMPORTS (optional) -- services/connectors/, api/v1/integrations.py
    # ==================================================================
    # Editors connect their organization's Google Drive, OneDrive /
    # SharePoint, Notion or Confluence through OAuth, pick files or pages,
    # and import them into a project. Google Docs, Notion pages and
    # Confluence pages are EXPORTED to a format the extractor reads (.docx,
    # .md, .html) on the way in.
    #
    # Off by default. With the master switch on, a provider appears only
    # once its client id AND secret are set -- each is an "app" you register
    # with that provider, whose redirect URI must be
    #   <this API>/api/v1/integrations/callback/<provider>
    # (google_drive, onedrive, notion, confluence).
    CONNECTORS_ENABLED: bool = False
    # Empty = derive <this API> from the request (right unless a proxy
    # rewrites the host); otherwise e.g. https://docminds-api.onrender.com
    CONNECTOR_REDIRECT_BASE_URL: str = ""
    # OAuth tokens are ENCRYPTED in the database (Fernet: AES-128-CBC +
    # HMAC-SHA256). Empty = a key derived from SECRET_KEY with HKDF, so
    # rotating SECRET_KEY then disconnects every connection. Set your own
    # (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
    # to decouple them. Comma-separated: the FIRST key encrypts, every key
    # decrypts -- which is how a key is rotated without losing connections.
    CONNECTOR_ENCRYPTION_KEY: str = ""
    CONNECTOR_GOOGLE_CLIENT_ID: str = ""
    CONNECTOR_GOOGLE_CLIENT_SECRET: str = ""
    CONNECTOR_MICROSOFT_CLIENT_ID: str = ""
    CONNECTOR_MICROSOFT_CLIENT_SECRET: str = ""
    # "common" lets work AND personal Microsoft accounts connect; a tenant
    # id (or "organizations") restricts it.
    CONNECTOR_MICROSOFT_TENANT: str = "common"
    CONNECTOR_NOTION_CLIENT_ID: str = ""
    CONNECTOR_NOTION_CLIENT_SECRET: str = ""
    CONNECTOR_CONFLUENCE_CLIENT_ID: str = ""
    CONNECTOR_CONFLUENCE_CLIENT_SECRET: str = ""
    # How long one call to a provider may take.
    CONNECTOR_TIMEOUT_SECONDS: float = 30.0

    # ---- Auto-sync ----
    # Imported documents remember their source id and revision. With this
    # on, a periodic sync asks each source whether the file changed and, if
    # it did, re-imports and re-indexes it. Celery beat sends the sync when
    # INGESTION_MODE=celery; with inline, a loop inside the API runs it.
    # "Sync now" works whether or not this is on.
    CONNECTOR_SYNC_ENABLED: bool = False
    CONNECTOR_SYNC_INTERVAL_MINUTES: int = 60
    # Most documents one sweep checks, so a huge library is spread over
    # several sweeps instead of hammering the provider's rate limit.
    CONNECTOR_SYNC_BATCH_SIZE: int = 100
    # Imports run in the background (POST .../import answers 202). A job
    # whose worker has not reported for this long is treated as orphaned --
    # the process died -- and resumed by the API's startup recovery.
    CONNECTOR_IMPORT_STALE_MINUTES: int = 15

    # ==================================================================
    # 23. BILLING WITH STRIPE OR RAZORPAY (optional) -- services/billing.py, api/v1/billing.py
    # ==================================================================
    # Three plans, each mapped onto the quotas of section 16:
    #   free  -> the QUOTA_* settings themselves (the override columns are
    #            cleared back to NULL)
    #   pro   -> BILLING_PLAN_LIMITS["pro"]
    #   team  -> BILLING_PLAN_LIMITS["team"]
    # A plan change arrives as a SIGNED provider webhook and writes the limits
    # onto the organization's own quota columns. Off by default: with
    # BILLING_ENABLED=false every /billing route answers 404, and quotas are
    # managed by hand exactly as before (docs/DEPLOYMENT.md, "Quotas").
    BILLING_ENABLED: bool = False
    # Which payment provider NEW subscriptions go through. "stripe" (the
    # default) or "razorpay" (India, where Stripe is not available). An
    # organization already subscribed through the other provider keeps
    # being managed there, and either provider's signed webhooks are
    # accepted as long as its webhook secret is set.
    BILLING_PROVIDER: Literal["stripe", "razorpay"] = "stripe"
    # From the Stripe dashboard: Developers -> API keys (sk_live_... or
    # sk_test_...), and Developers -> Webhooks -> your endpoint's signing
    # secret (whsec_...). Secrets: .env or the host's secret store only.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # The recurring PRICE ids (price_...) customers subscribe to.
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_TEAM: str = ""
    # A signed webhook older than this is refused (replay protection).
    STRIPE_WEBHOOK_TOLERANCE_SECONDS: int = 300
    # Razorpay (BILLING_PROVIDER=razorpay). From the Razorpay dashboard
    # (docs/DEPLOYMENT.md, "Billing with Razorpay"): Account & Settings ->
    # API keys (rzp_test_... / rzp_live_... and its secret), Webhooks -> the
    # secret YOU typed when adding the endpoint, and Subscriptions -> Plans
    # -> the plan ids (plan_...) customers subscribe to.
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    RAZORPAY_PLAN_PRO: str = ""
    RAZORPAY_PLAN_TEAM: str = ""
    # Razorpay subscriptions need a fixed number of billing cycles; 120
    # monthly cycles is ten years. Lower it for a yearly plan.
    RAZORPAY_TOTAL_COUNT: int = 120
    # What each paid plan allows. Keys are the quota names; 0 = unlimited.
    # From the environment, set it as JSON like LLM_PRICING.
    BILLING_PLAN_LIMITS: Dict[str, Dict[str, int]] = {
        "pro": {
            "monthly_tokens": 10_000_000,
            "max_documents": 10_000,
            "max_storage_bytes": 50 * 1024 * 1024 * 1024,
        },
        "team": {
            "monthly_tokens": 50_000_000,
            "max_documents": 0,
            "max_storage_bytes": 250 * 1024 * 1024 * 1024,
        },
    }
    # Shown on the upgrade page only; the provider's checkout shows the real
    # price. With Razorpay, set INR labels, e.g.
    #   {"free": "₹0", "pro": "₹2,499 / month", "team": "₹7,999 / month"}
    BILLING_PRICE_LABELS: Dict[str, str] = {"free": "$0", "pro": "$29 / month", "team": "$99 / month"}

    # ==================================================================
    # 24. ADMIN DASHBOARD (optional) -- services/analytics.py, api/v1/analytics.py
    # ==================================================================
    # Settings -> Admin dashboard: usage and cost over time, top questions,
    # documents processed and failed, active users and the feedback trend,
    # for the organization's admins. Off: every /analytics route is 404.
    ADMIN_DASHBOARD_ENABLED: bool = False

    # ==================================================================
    # DERIVED VALUES
    # ------------------------------------------------------------------
    # `@property` means "compute this on demand from the settings above".
    # You read it like a normal attribute (settings.REDIS_URL) but the value is
    # calculated fresh each time rather than stored.
    # ==================================================================

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """
        The ASYNC database address, used by the FastAPI application.

        "Async" means the app can fire off a database query and, while waiting
        for the answer to come back, serve other users instead of sitting idle.
        The `asyncpg` driver named in the URL is what makes that possible.

        Format: postgresql+asyncpg://user:password@host:port/database_name
        """
        if self.DATABASE_URL:
            # Cloud providers hand out a URL starting with `postgresql://`,
            # which SQLAlchemy interprets as "use the default SYNC driver".
            # We rewrite the prefix to force the async driver instead.
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
                # A very old Heroku-style prefix SQLAlchemy no longer accepts.
                url = url.replace("postgres://", "postgresql://", 1)
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return _for_asyncpg(url)
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SQLALCHEMY_SYNC_DATABASE_URI(self) -> str:
        """
        The SYNC (ordinary, blocking) database address.

        Alembic -- the tool that creates and updates our database tables -- is
        not asynchronous, so it needs this plain `psycopg2` version instead of
        the asyncpg one above.

        The driver is named explicitly (`postgresql+psycopg2://`). A bare
        `postgresql://` means "SQLAlchemy's default driver", and SQLAlchemy 2.1
        changed that default from psycopg2 to psycopg 3 -- which is not
        installed, so every migration failed with "No module named 'psycopg'".
        """
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            return url
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        """
        Address of the Redis server, used as Celery's message queue.

        The trailing `/0` selects database number 0. Redis ships with 16
        numbered databases; using different numbers keeps unrelated apps
        sharing one Redis server from overwriting each other's keys.
        """
        if self.REDIS_URL_OVERRIDE:
            return self.REDIS_URL_OVERRIDE
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def frontend_url(self) -> str:
        """
        The base URL that links in emails point at, without a trailing slash.

        FRONTEND_URL wins when set. Otherwise the first FRONTEND_ORIGIN is a
        good guess (it is the deployed frontend), and localhost:3000 is the
        development default.
        """
        if self.FRONTEND_URL:
            return self.FRONTEND_URL.rstrip("/")
        for origin in self.FRONTEND_ORIGIN.split(","):
            if origin.strip():
                return origin.strip().rstrip("/")
        return "http://localhost:3000"

    @property
    def local_embedding_model(self) -> str:
        """The local model actually used: the multilingual one when switched on."""
        if self.EMBEDDING_MULTILINGUAL:
            return self.MULTILINGUAL_EMBEDDING_MODEL
        return self.LOCAL_EMBEDDING_MODEL

    @property
    def oidc_configured(self) -> bool:
        """SSO is on AND has everything it needs to work."""
        return bool(
            self.OIDC_ENABLED
            and self.OIDC_DISCOVERY_URL
            and self.OIDC_CLIENT_ID
            and self.OIDC_CLIENT_SECRET
        )

    @property
    def email_backend(self) -> str:
        """`smtp` when an SMTP server is configured, otherwise `console`."""
        return "smtp" if self.SMTP_HOST else "console"

    # ------------------------------------------------------------------
    # STARTUP GUARD
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _refuse_weak_secret_in_production(self) -> "Settings":
        """
        Refuse to start in production with a guessable SECRET_KEY.

        The default SECRET_KEY above is committed to a public repository, so
        anybody can read it -- and anybody holding it can sign a token that
        says "I am the Admin of organization X". In development that does not
        matter. In production it is a complete authentication bypass, and the
        failure is silent: everything appears to work perfectly.

        So the app fails LOUDLY instead, at startup, with the fix in the
        message. Pydantic runs this validator when `Settings()` is created on
        the last line of this file, which happens before uvicorn or Celery
        serve a single request.
        """
        if self.ENV == "production":
            if self.SECRET_KEY == _DEFAULT_SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY is still the committed development default. "
                    "Refusing to start with ENV=production. Generate a new "
                    "one with `openssl rand -hex 32` and set it in the "
                    "environment."
                )
            if len(self.SECRET_KEY) < 32:
                raise ValueError(
                    f"SECRET_KEY is only {len(self.SECRET_KEY)} characters. "
                    "Refusing to start with ENV=production: use at least 32 "
                    "random characters (`openssl rand -hex 32` gives 64)."
                )
        return self

    @model_validator(mode="after")
    def _require_a_bucket_for_s3(self) -> "Settings":
        """
        STORAGE_BACKEND=s3 without a bucket is a typo, not a choice.

        Caught here, at startup, rather than on the first upload -- where it
        would surface as a confusing boto3 error long after the deploy looked
        healthy.
        """
        if self.STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
            raise ValueError("STORAGE_BACKEND=s3 needs S3_BUCKET to be set.")
        return self

    @model_validator(mode="after")
    def _check_rate_limits_and_quotas(self) -> "Settings":
        """
        Parse every RATE_LIMIT_* string now, at startup.

        A typo such as "20/minuet" would otherwise be discovered by the first
        user to log in -- as a 500 -- or, worse, be read as "no limit". Here
        it stops the app from starting, with the bad value in the message.
        """
        for name in (
            "RATE_LIMIT_LOGIN", "RATE_LIMIT_SIGNUP", "RATE_LIMIT_PASSWORD_RESET", "RATE_LIMIT_VERIFY_RESEND",
            "RATE_LIMIT_CHAT", "RATE_LIMIT_SEARCH", "RATE_LIMIT_UPLOAD",
            "RATE_LIMIT_SHARE_VIEW", "RATE_LIMIT_COMPARE",
        ):
            try:
                parse_rate_limit(getattr(self, name))
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
        for name in ("QUOTA_MONTHLY_TOKENS", "QUOTA_MAX_DOCUMENTS", "QUOTA_MAX_STORAGE_BYTES"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative (0 means unlimited).")
        if self.TRUSTED_PROXY_COUNT < 0:
            raise ValueError("TRUSTED_PROXY_COUNT cannot be negative.")
        if self.CONNECTOR_SYNC_INTERVAL_MINUTES < 1:
            raise ValueError("CONNECTOR_SYNC_INTERVAL_MINUTES must be at least 1.")
        return self

    @property
    def local_storage_dir(self) -> str:
        """Where LocalStorage keeps files: LOCAL_STORAGE_DIR, or backend/uploads."""
        if self.LOCAL_STORAGE_DIR:
            return os.path.abspath(self.LOCAL_STORAGE_DIR)
        # config.py -> core -> app -> backend, then /uploads. The same
        # directory the upload endpoint has always written to.
        backend_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(backend_dir, "uploads")

    @property
    def max_upload_request_bytes(self) -> int:
        """
        The ceiling for a whole upload request body.

        Every file at the per-file maximum, plus 1 MB for the multipart
        boundaries and the small form fields (project id, chunk settings).
        """
        return self.MAX_UPLOAD_SIZE_BYTES * self.MAX_FILES_PER_UPLOAD + 1024 * 1024

    @property
    def cors_origins(self) -> List[str]:
        """
        The final list of websites allowed to call this API from a browser.

        Combines the hard-coded local development origins with whatever the
        FRONTEND_ORIGIN environment variable holds (comma-separated), so the
        deployed frontend can be permitted without editing any code.
        """
        origins = list(self.BACKEND_CORS_ORIGINS)
        # FRONTEND_URL is the deployed site too, so it is allowed as well:
        # setting only one of the two variables is an easy mistake to make.
        candidates = self.FRONTEND_ORIGIN.split(",") + [self.FRONTEND_URL]
        for origin in candidates:
            # .strip() removes accidental spaces. .rstrip("/") matters just as
            # much: a browser sends "https://site.app" with no trailing slash,
            # so "https://site.app/" pasted from the address bar never matches
            # and every request from the deployed frontend is refused.
            cleaned = origin.strip().rstrip("/")
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
        return origins


# ----------------------------------------------------------------------
# Create the settings object ONCE, here, when this module is first imported.
# Python caches imported modules, so every `from app.core.config import
# settings` anywhere in the codebase receives this exact same object, and the
# .env file is therefore read exactly once per process.
# ----------------------------------------------------------------------
settings = Settings()
