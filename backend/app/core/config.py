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
from typing import List, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # When DEBUG is True we print more detail and echo every SQL statement.
    # Turn this OFF in production: it is slow and leaks internals into logs.
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
    SECRET_KEY: str = "7e15291b0f19c25f1b1c55bc6d01e1498b671a532321bc36efc0d8f07df590ad"

    # HS256 = sign using one shared secret (symmetric signing). Simple and
    # standard for a single backend that both issues and verifies its tokens.
    ALGORITHM: str = "HS256"

    # Access tokens expire quickly, so a stolen one is only briefly useful.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Refresh tokens live much longer and can ONLY be used to obtain a new
    # access token. This is what stops users being logged out every 30 minutes
    # while still keeping the short-lived access token.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

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

    # ==================================================================
    # 8. LLM -- the AI that writes the final answer (the "G" in RAG)
    # ==================================================================
    # Groq is a free and extremely fast host for open models such as Llama 3.3.
    # Get a key (no credit card required) at https://console.groq.com/keys
    LLM_PROVIDER: Literal["groq", "mock"] = "groq"

    # ##################################################################
    # ##  PUT YOUR FREE GROQ API KEY IN backend/.env LIKE THIS:       ##
    # ##      GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx              ##
    # ##  NEVER paste the real key into this file -- config.py is    ##
    # ##  committed to GitHub, while .env is git-ignored.            ##
    # ##################################################################
    GROQ_API_KEY: str = ""

    # The specific model Groq should run for us. 70B means 70 billion
    # parameters -- large enough to reliably obey an instruction like "answer
    # ONLY from the provided context and otherwise say you don't know".
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # `temperature` controls randomness: 0 always picks the most likely next
    # word (factual, repeatable), 1 is creative and varied. For answering
    # questions about real documents we want facts, so we keep it very low.
    LLM_TEMPERATURE: float = 0.1

    # A hard ceiling on the length of the generated answer, measured in tokens
    # (roughly three-quarters of a word each). Prevents runaway responses.
    LLM_MAX_TOKENS: int = 1024

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
    # 50 MB, written as a multiplication so the number stays human-readable.
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024

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
    def cors_origins(self) -> List[str]:
        """
        The final list of websites allowed to call this API from a browser.

        Combines the hard-coded local development origins with whatever the
        FRONTEND_ORIGIN environment variable holds (comma-separated), so the
        deployed frontend can be permitted without editing any code.
        """
        origins = list(self.BACKEND_CORS_ORIGINS)
        if self.FRONTEND_ORIGIN:
            # split(",") turns "a.com,b.com" into ["a.com", "b.com"], and
            # .strip() removes any accidental spaces around each entry.
            for origin in self.FRONTEND_ORIGIN.split(","):
                cleaned = origin.strip()
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
