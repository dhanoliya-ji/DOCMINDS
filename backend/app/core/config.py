"""This is the single place where every "setting" of the whole backend lives."""
import os
import re
from typing import Dict, List, Literal, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET_KEY = "7e15291b0f19c25f1b1c55bc6d01e1498b671a532321bc36efc0d8f07df590ad"


def _for_asyncpg(url: str) -> str:
    """Translate libpq connection options into what asyncpg understands."""
    parts = urlsplit(url)
    pairs = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "channel_binding"
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


_RATE_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_RATE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*(\d*)\s*([a-z]+)\s*$")


def parse_rate_limit(text: str) -> Optional[Tuple[int, int]]:
    """Turn "20/minute" into (20, 60): at most 20 requests per 60 seconds."""
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
    model_config = SettingsConfigDict(
        env_file=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
        ),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "DocMinds"

    API_V1_STR: str = "/api/v1"

    ENV: Literal["development", "production", "testing"] = "development"

    DEBUG: bool = True

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",   # Next.js development server
        "http://127.0.0.1:3000",   # the same thing, written as an IP
    ]

    FRONTEND_ORIGIN: str = ""

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_DB: str = "document_intelligence"

    DATABASE_URL: str = ""

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    REDIS_URL_OVERRIDE: str = ""

    SECRET_KEY: str = _DEFAULT_SECRET_KEY

    ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    PASSWORD_MIN_LENGTH: int = 10

    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60

    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 48

    INVITE_EXPIRE_DAYS: int = 7

    REQUIRE_EMAIL_VERIFICATION: bool = False

    FRONTEND_URL: str = ""

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    EMAIL_FROM: str = "DocMinds <no-reply@docminds.local>"

    OCR_PROVIDER: Literal["tesseract", "easyocr", "paddleocr", "mock"] = "tesseract"

    TESSERACT_CMD: str = "tesseract"

    OCR_CHAR_THRESHOLD_PER_PAGE: int = 100

    OCR_DPI: int = 150
    OCR_MAX_PIXELS: int = 12_000_000

    VISION_PROVIDER: Literal["off", "groq", "gemini"] = "off"
    VISION_MODEL: str = ""
    VISION_API_KEY: str = ""
    VISION_MAX_IMAGES_PER_DOCUMENT: int = 20
    VISION_REQUESTS_PER_MINUTE: int = 10
    VISION_MIN_IMAGE_COVERAGE: float = 0.3

    EMBEDDING_PROVIDER: Literal["local", "openai", "gemini", "mock"] = "local"

    LOCAL_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    GEMINI_API_KEY: str = ""
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"

    EMBEDDING_DIMENSION: int = 384

    INGESTION_MODE: Literal["celery", "inline"] = "celery"

    EMBEDDING_BATCH_SIZE: int = 32

    GEMINI_EMBED_REQUESTS_PER_MINUTE: int = 90
    GEMINI_EMBED_TOKENS_PER_MINUTE: int = 25_000
    GEMINI_RETRY_MAX_DELAY_SECONDS: int = 90
    GEMINI_RETRY_BUDGET_SECONDS: int = 300
    GEMINI_QUERY_RETRY_BUDGET_SECONDS: int = 20

    INGESTION_EMBED_BATCH_SIZE: int = 50
    INGESTION_MAX_PAGES: int = 2000
    INGESTION_MAX_CHUNKS: int = 3000
    INGESTION_ROWS_PER_SEGMENT: int = 200
    INGESTION_PAUSE_SECONDS: int = 120
    INGESTION_PAUSE_MAX_SECONDS: int = 3600
    INGESTION_MAX_IDLE_PAUSES: int = 24
    INGESTION_INLINE_CONCURRENCY: int = 1
    INGESTION_INLINE_SWEEP_SECONDS: int = 60

    LLM_PROVIDER: Literal["groq", "mock"] = "groq"

    GROQ_API_KEY: str = ""

    GROQ_MODEL: str = "openai/gpt-oss-120b"

    LLM_TEMPERATURE: float = 0.1

    LLM_MAX_TOKENS: int = 2048

    AGENT_MODEL: str = ""

    AGENT_MAX_STEPS: int = 6

    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 1.0

    LLM_TIMEOUT_SECONDS: float = 30.0

    LLM_FALLBACK_MODEL: str = ""

    RETRIEVAL_TOP_K: int = 5

    RETRIEVAL_MIN_SCORE: float = 0.15

    RETRIEVAL_HYBRID: bool = True

    RETRIEVAL_CANDIDATES: int = 20

    RETRIEVAL_RRF_K: int = 60

    RERANKER: Literal["off", "cross-encoder"] = "off"
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    QUERY_REWRITE_ENABLED: bool = True

    DEFAULT_CHUNK_STRATEGY: Literal["fixed_size", "sentence", "markdown"] = "fixed_size"

    DEFAULT_CHUNK_SIZE: int = 500

    DEFAULT_CHUNK_OVERLAP: int = 50

    MAX_UPLOAD_SIZE_BYTES: int = 100 * 1024 * 1024

    MAX_FILES_PER_UPLOAD: int = 20

    ZIP_MAX_ENTRIES: int = 1000
    ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES: int = 500 * 1024 * 1024
    ZIP_MAX_COMPRESSION_RATIO: int = 100
    ZIP_MAX_NESTING_DEPTH: int = 1

    STORAGE_BACKEND: Literal["local", "s3"] = "local"

    LOCAL_STORAGE_DIR: str = ""

    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_KEY_PREFIX: str = ""

    INGESTION_MAX_RETRIES: int = 3
    INGESTION_RETRY_BACKOFF_SECONDS: int = 10
    INGESTION_RETRY_BACKOFF_MAX_SECONDS: int = 600

    INGESTION_SOFT_TIME_LIMIT_SECONDS: int = 1500
    INGESTION_TIME_LIMIT_SECONDS: int = 1800

    INGESTION_STALE_AFTER_MINUTES: int = 45

    INGESTION_RECOVERY_INTERVAL_MINUTES: int = 10
    INGESTION_RECOVER_ON_STARTUP: bool = True

    MALWARE_SCAN: Literal["off", "clamav"] = "off"
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    CLAMAV_SOCKET: str = ""
    CLAMAV_TIMEOUT_SECONDS: int = 60

    RATE_LIMIT_ENABLED: bool = True

    RATE_LIMIT_STORAGE: Literal["auto", "memory"] = "auto"

    RATE_LIMIT_LOGIN: str = "20/minute"
    RATE_LIMIT_SIGNUP: str = "10/hour"
    RATE_LIMIT_PASSWORD_RESET: str = "10/hour"
    RATE_LIMIT_VERIFY_RESEND: str = "5/hour"
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_SEARCH: str = "60/minute"
    RATE_LIMIT_UPLOAD: str = "30/minute"
    RATE_LIMIT_SHARE_VIEW: str = "120/minute"
    RATE_LIMIT_COMPARE: str = "10/minute"

    TRUSTED_PROXY_COUNT: int = 0

    CLIENT_IP_HEADER: str = ""

    QUOTA_MONTHLY_TOKENS: int = 1_000_000
    QUOTA_MAX_DOCUMENTS: int = 1000
    QUOTA_MAX_STORAGE_BYTES: int = 5 * 1024 * 1024 * 1024

    LLM_PRICING: Dict[str, Dict[str, float]] = {
        "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
        "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},
    }

    LOG_FORMAT: Literal["json", "text"] = "json"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_ENVIRONMENT: str = ""

    READINESS_TIMEOUT_SECONDS: float = 3.0

    HSTS_MAX_AGE_SECONDS: int = 365 * 24 * 60 * 60

    OIDC_ENABLED: bool = False
    OIDC_DISCOVERY_URL: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_SCOPES: str = "openid email profile"
    OIDC_PROVIDER_NAME: str = "SSO"
    OIDC_REDIRECT_URI: str = ""
    OIDC_ALLOW_SIGNUP: bool = True
    OIDC_REQUIRE_EMAIL_VERIFIED: bool = True

    EMBEDDING_MULTILINGUAL: bool = False
    MULTILINGUAL_EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"

    EMBEDDING_CACHE_ENABLED: bool = False
    EMBEDDING_CACHE_BACKEND: Literal["auto", "memory"] = "auto"
    EMBEDDING_CACHE_MAX_ENTRIES: int = 2048
    EMBEDDING_CACHE_TTL_SECONDS: int = 24 * 60 * 60

    WEBHOOKS_ENABLED: bool = False
    WEBHOOK_MAX_ATTEMPTS: int = 6
    WEBHOOK_RETRY_BACKOFF_SECONDS: int = 30
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    WEBHOOK_ALLOW_PRIVATE_TARGETS: bool = False
    WEBHOOK_ALLOW_HTTP: bool = False
    WEBHOOK_MAX_ENDPOINTS_PER_ORG: int = 10

    CONNECTORS_ENABLED: bool = False
    CONNECTOR_REDIRECT_BASE_URL: str = ""
    CONNECTOR_ENCRYPTION_KEY: str = ""
    CONNECTOR_GOOGLE_CLIENT_ID: str = ""
    CONNECTOR_GOOGLE_CLIENT_SECRET: str = ""
    CONNECTOR_MICROSOFT_CLIENT_ID: str = ""
    CONNECTOR_MICROSOFT_CLIENT_SECRET: str = ""
    CONNECTOR_MICROSOFT_TENANT: str = "common"
    CONNECTOR_NOTION_CLIENT_ID: str = ""
    CONNECTOR_NOTION_CLIENT_SECRET: str = ""
    CONNECTOR_CONFLUENCE_CLIENT_ID: str = ""
    CONNECTOR_CONFLUENCE_CLIENT_SECRET: str = ""
    CONNECTOR_TIMEOUT_SECONDS: float = 30.0

    CONNECTOR_SYNC_ENABLED: bool = False
    CONNECTOR_SYNC_INTERVAL_MINUTES: int = 60
    CONNECTOR_SYNC_BATCH_SIZE: int = 100
    CONNECTOR_IMPORT_STALE_MINUTES: int = 15

    BILLING_ENABLED: bool = False
    BILLING_PROVIDER: Literal["stripe", "razorpay"] = "stripe"
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_TEAM: str = ""
    STRIPE_WEBHOOK_TOLERANCE_SECONDS: int = 300
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    RAZORPAY_PLAN_PRO: str = ""
    RAZORPAY_PLAN_TEAM: str = ""
    RAZORPAY_TOTAL_COUNT: int = 120
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
    BILLING_PRICE_LABELS: Dict[str, str] = {"free": "$0", "pro": "$29 / month", "team": "$99 / month"}

    ADMIN_DASHBOARD_ENABLED: bool = False


    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """The ASYNC database address, used by the FastAPI application."""
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgres://"):
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
        """The SYNC (ordinary, blocking) database address."""
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
        """Address of the Redis server, used as Celery's message queue."""
        if self.REDIS_URL_OVERRIDE:
            return self.REDIS_URL_OVERRIDE
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def frontend_url(self) -> str:
        """The base URL that links in emails point at, without a trailing slash."""
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

    @model_validator(mode="after")
    def _refuse_weak_secret_in_production(self) -> "Settings":
        """Refuse to start in production with a guessable SECRET_KEY."""
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
        """STORAGE_BACKEND=s3 without a bucket is a typo, not a choice."""
        if self.STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
            raise ValueError("STORAGE_BACKEND=s3 needs S3_BUCKET to be set.")
        return self

    @model_validator(mode="after")
    def _check_rate_limits_and_quotas(self) -> "Settings":
        """Parse every RATE_LIMIT_* string now, at startup."""
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
        backend_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(backend_dir, "uploads")

    @property
    def max_upload_request_bytes(self) -> int:
        """The ceiling for a whole upload request body."""
        return self.MAX_UPLOAD_SIZE_BYTES * self.MAX_FILES_PER_UPLOAD + 1024 * 1024

    @property
    def cors_origins(self) -> List[str]:
        """The final list of websites allowed to call this API from a browser."""
        origins = list(self.BACKEND_CORS_ORIGINS)
        candidates = self.FRONTEND_ORIGIN.split(",") + [self.FRONTEND_URL]
        for origin in candidates:
            cleaned = origin.strip().rstrip("/")
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
        return origins


settings = Settings()
