# Development

Local setup, the full configuration reference, testing, linting and troubleshooting. For how the system works see [ARCHITECTURE.md](ARCHITECTURE.md).

## Contents

1. [Prerequisites](#prerequisites)
2. [Local setup](#local-setup)
3. [Configuration reference](#configuration-reference)
4. [Frontend conventions](#frontend-conventions)
5. [Testing](#testing)
6. [Evaluating answer quality](#evaluating-answer-quality)
7. [Linting and build checks](#linting-and-build-checks)
8. [Database migrations](#database-migrations)
9. [Troubleshooting](#troubleshooting)
10. [Glossary](#glossary)

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | Backend and worker |
| Node.js | 20.9+ | Required by Next.js 16; CI uses Node 22 |
| Docker | Recent Docker Desktop or Engine | Runs Postgres (pgvector) and Redis via `backend/docker-compose.yml` |
| Tesseract | Any recent release | Only for OCR of scans and images. macOS: `brew install tesseract`; Debian/Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-eng`; Windows: the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki), then set `TESSERACT_CMD` to the full path |

A Groq API key (free at https://console.groq.com/keys) is optional. Without one, chat runs in demo mode (see [Running without external services](#running-without-external-services)).

## Local setup

### Backend

```bash
cd backend
docker compose up -d              # Postgres + pgvector on 5433, Redis on 6379
cp .env.example .env              # PowerShell: Copy-Item .env.example .env
pip install -r requirements.txt
alembic upgrade head              # tables, pgvector extension, HNSW index
```

Set `GROQ_API_KEY` in `backend/.env`. That file is git-ignored; never put keys in `config.py` or `.env.example`. Settings are read once at startup, so restart the API and worker after changing `.env`.

Start the API and the worker in separate terminals:

```bash
uvicorn app.main:app --reload --port 8000
celery -A app.core.celery_app worker --loglevel=info   # add --pool=solo on Windows
```

Both processes are required with the default `INGESTION_MODE=celery`. To run without a worker and Redis, set `INGESTION_MODE=inline`.

Optionally, run Celery beat as well. It sends the recovery sweep that re-queues documents orphaned by a dead worker every `INGESTION_RECOVERY_INTERVAL_MINUTES`. The API also runs the sweep once at startup, so beat is not required for development.

```bash
celery -A app.core.celery_app beat --loglevel=info
# or, with a single worker: celery -A app.core.celery_app worker -B --loglevel=info
```

With `--pool=solo` (Windows) Celery cannot enforce `INGESTION_SOFT_TIME_LIMIT_SECONDS` or `INGESTION_TIME_LIMIT_SECONDS`, because they rely on Unix signals. Retries and recovery still work.

To try malware scanning locally, start ClamAV with `docker compose --profile scan up -d` and set `MALWARE_SCAN=clamav`. The first start downloads virus definitions and takes a minute or two.

Postgres is mapped to host port **5433**, not 5432, to avoid clashing with a local installation. `docker compose down -v` deletes all data.

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev                       # http://localhost:3000
```

| URL | Purpose |
|---|---|
| http://localhost:3000 | Web app |
| http://localhost:8000/docs | Interactive API docs |
| http://localhost:8000/api/v1/health | Health and configuration report |

The first upload with `EMBEDDING_PROVIDER=local` downloads the embedding model (about 90 MB) once.

### Running without external services

Every external dependency has a deterministic `mock` provider. Set `OCR_PROVIDER=mock`, `EMBEDDING_PROVIDER=mock` and `LLM_PROVIDER=mock` to run the full pipeline without API keys, model downloads or Tesseract. With `LLM_PROVIDER=mock` or no `GROQ_API_KEY`, both chat modes return the top matching passage with a demo-mode note; the dashboard shows a demo-mode banner when no `GROQ_API_KEY` is configured. Mock embeddings hash their input, so semantic matches between different wordings will not be found.

---

## Configuration reference

All backend settings are defined in [`backend/app/core/config.py`](../backend/app/core/config.py) and can be overridden by environment variables or `backend/.env` (names are case-sensitive; unknown keys are ignored). [`backend/.env.example`](../backend/.env.example) is the template. Enumerated values are validated at startup.

### Application

| Variable | Default | Description |
|---|---|---|
| `PROJECT_NAME` | `DocMinds` | API title and name reported by `/` and `/api/v1/health` |
| `API_V1_STR` | `/api/v1` | Route prefix |
| `ENV` | `development` | `development`, `production` or `testing`. `development` echoes SQL statements |
| `DEBUG` | `True` | Reported by the health endpoint; set `false` in production |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Informational; the bind address is set by the `uvicorn` command (the Docker image reads `PORT`) |
| `BACKEND_CORS_ORIGINS` | `http://localhost:3000`, `http://127.0.0.1:3000` | Always-allowed CORS origins |
| `FRONTEND_ORIGIN` | *(empty)* | Additional allowed origins, comma-separated (deployed frontend URL) |

### Uploads

| Variable | Default | Description |
|---|---|---|
| `MAX_UPLOAD_SIZE_BYTES` | `104857600` (100 MB) | Per-file limit, counted while the upload is read in 1 MB chunks. Over it: `413` |
| `MAX_FILES_PER_UPLOAD` | `20` | Files per upload request (`400` above it). With the per-file limit it sets the whole-request ceiling (`MAX_UPLOAD_SIZE_BYTES` x `MAX_FILES_PER_UPLOAD` + 1 MB), which middleware enforces with `413` before the body is parsed |
| `ZIP_MAX_ENTRIES` | `1000` | Most files one uploaded archive may contain, nested archives included. Over it the archive fails |
| `ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES` | `524288000` (500 MB) | Most bytes an archive may expand to, counted while unpacking (not trusted from the header) |
| `ZIP_MAX_COMPRESSION_RATIO` | `100` | Highest uncompressed:compressed ratio for any member larger than 1 MB. Higher means a zip bomb, and the archive fails |
| `ZIP_MAX_NESTING_DEPTH` | `1` | How many levels of zip-inside-zip are opened. `1` opens a zip inside the upload and skips a zip inside that; `0` skips all nested archives |

### File storage

| Variable | Default | Description |
|---|---|---|
| `STORAGE_BACKEND` | `local` | Where new uploads are stored: `local` (disk) or `s3` (any S3-compatible store; needs `boto3`). Each document records its backend, so switching does not orphan older files |
| `LOCAL_STORAGE_DIR` | *(empty)* | Directory for `local`. Empty means `backend/uploads` |
| `S3_BUCKET` | *(empty)* | Bucket name. Required when `STORAGE_BACKEND=s3`; the app refuses to start without it |
| `S3_REGION` | *(empty)* | For example `eu-west-1`, or `auto` for Cloudflare R2. Empty lets boto3 decide |
| `S3_ENDPOINT_URL` | *(empty)* | Empty for AWS. The provider URL for R2, MinIO or B2 |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | *(empty)* | Empty uses boto3's credential chain (IAM role, `AWS_*` variables, `~/.aws`) |
| `S3_KEY_PREFIX` | *(empty)* | Prepended to every object key, for example `docminds/` |

### Ingestion durability

| Variable | Default | Description |
|---|---|---|
| `INGESTION_MAX_RETRIES` | `3` | Retries for a transient failure (database or network blip, rate limit, storage or scanner unavailable). Permanent failures are not retried |
| `INGESTION_RETRY_BACKOFF_SECONDS` | `10` | The first wait before a retry; each later retry doubles it (10 s, 20 s, 40 s). Celery randomises each wait between zero and that value; inline mode waits the full value |
| `INGESTION_RETRY_BACKOFF_MAX_SECONDS` | `600` | Cap on any single wait between retries |
| `INGESTION_SOFT_TIME_LIMIT_SECONDS` | `1500` | A job running longer is stopped and the document marked failed (prefork pool only) |
| `INGESTION_TIME_LIMIT_SECONDS` | `1800` | Hard limit: the worker process running the job is killed |
| `INGESTION_STALE_AFTER_MINUTES` | `45` | A document `processing` or `pending` for longer is treated as orphaned and re-queued, or failed once it has used every attempt. Must exceed the hard time limit |
| `INGESTION_RECOVERY_INTERVAL_MINUTES` | `10` | How often Celery beat runs the recovery sweep |
| `INGESTION_RECOVER_ON_STARTUP` | `True` | Also run the sweep in the background when the API starts |

### Malware scanning

| Variable | Default | Description |
|---|---|---|
| `MALWARE_SCAN` | `off` | `off` or `clamav`. With `clamav`, every file is streamed to clamd before processing; an infected file is failed, flagged `quarantined` and deleted from storage. If clamd is unreachable the job is retried and then fails: an unscanned file is never processed |
| `CLAMAV_HOST` / `CLAMAV_PORT` | `localhost` / `3310` | clamd's TCP address |
| `CLAMAV_SOCKET` | *(empty)* | A Unix socket path; overrides host and port when set |
| `CLAMAV_TIMEOUT_SECONDS` | `60` | Connect and read timeout for one scan |

### Database and queue

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | |
| `POSTGRES_PORT` | `5433` | Host port mapped by `docker-compose.yml` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `postgres` / `postgrespassword` | Local development credentials |
| `POSTGRES_DB` | `document_intelligence` | |
| `DATABASE_URL` | *(empty)* | Full connection string; overrides the `POSTGRES_*` settings. `postgres://` and `postgresql://` prefixes are rewritten for asyncpg (app) and psycopg2 (Alembic) |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Celery broker and result backend (database 0) |
| `REDIS_URL_OVERRIDE` | *(empty)* | Full `redis://` URL; overrides host and port |
| `INGESTION_MODE` | `celery` | `celery` (worker via Redis) or `inline` (API processes uploads as a background task) |

### Security

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | A committed development value | JWT signing key. **Set your own outside local development** (`openssl rand -hex 32`). With `ENV=production` the app refuses to start if it is the committed default or shorter than 32 characters |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime; also how long a signed-out token stays on the denylist |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime. Refresh tokens are stored hashed and rotated on every use |

### Accounts and email

| Variable | Default | Description |
|---|---|---|
| `PASSWORD_MIN_LENGTH` | `10` | Minimum password length for signup and password reset. Passwords also may not be all digits or equal to the email |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `60` | Lifetime of a password reset link (single use) |
| `EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS` | `48` | Lifetime of an email verification link (single use) |
| `INVITE_EXPIRE_DAYS` | `7` | Lifetime of an organisation invite |
| `REQUIRE_EMAIL_VERIFICATION` | `False` | When true, login is refused (`403`) until the email is verified |
| `FRONTEND_URL` | *(empty)* | Base URL for links in emails (`/login?invite=`, `/reset-password?token=`, `/verify-email?token=`). Empty falls back to the first `FRONTEND_ORIGIN`, then `http://localhost:3000` |
| `SMTP_HOST` | *(empty)* | Empty: emails are written to the API log (the "console" sender). Set: emails are delivered over SMTP |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | SMTP credentials; login is skipped when the username is empty |
| `SMTP_USE_TLS` | `True` | Upgrade the connection with STARTTLS |
| `SMTP_USE_SSL` | `False` | Use implicit TLS from the first byte (usually port 465) instead of STARTTLS |
| `EMAIL_FROM` | `DocMinds <no-reply@docminds.local>` | Sender address |

### OCR

| Variable | Default | Description |
|---|---|---|
| `OCR_PROVIDER` | `tesseract` | `tesseract`, `easyocr`, `paddleocr` or `mock` |
| `TESSERACT_CMD` | `tesseract` | Path to the Tesseract binary; use the full path on Windows |
| `OCR_CHAR_THRESHOLD_PER_PAGE` | `100` | PDFs with fewer extracted characters per page are treated as scans and OCR'd |
| `OCR_DPI` | `150` | Rasterisation resolution for OCR |

### Embeddings

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_PROVIDER` | `local` | `local` (sentence-transformers), `openai`, `gemini` or `mock`. Changing it requires re-processing all documents |
| `LOCAL_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Model for `local` (384 dimensions) |
| `OPENAI_API_KEY` | *(empty)* | Required for `openai`; without it the provider falls back to mock vectors |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Model for `openai` (1536 dimensions) |
| `GEMINI_API_KEY` | *(empty)* | Required for `gemini`; without it ingestion fails |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Model for `gemini`, requested at `EMBEDDING_DIMENSION` |
| `EMBEDDING_DIMENSION` | `384` | Must match the provider's output and the database column. The column is created as `vector(384)` by migration `a1b2c3d4e5f6`; any other size (for example `openai` at 1536) needs a new migration that alters the column, then re-processing |
| `EMBEDDING_BATCH_SIZE` | `32` | Encoding batch size for the `local` model |

### Language model and agent

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `groq` | `groq` or `mock` (forces demo mode) |
| `GROQ_API_KEY` | *(empty)* | Groq key; empty means demo mode |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model for rag mode, and for agent mode unless `AGENT_MODEL` is set |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature |
| `LLM_MAX_TOKENS` | `2048` | Maximum generated tokens (reasoning models count hidden reasoning against this) |
| `AGENT_MODEL` | *(empty)* | Groq model for agent mode; empty reuses `GROQ_MODEL`. Must support tool calling |
| `AGENT_MAX_STEPS` | `6` | Maximum model requests per agent answer |
| `LLM_MAX_RETRIES` | `2` | Retries of a transient model error (429, 5xx, connection) on the same model. 401/400 are never retried |
| `LLM_RETRY_BACKOFF_SECONDS` | `1.0` | First retry wait; doubles each retry. A server's `Retry-After` is used instead; waits over 20 s go to the fallback model |
| `LLM_TIMEOUT_SECONDS` | `30.0` | Per-call timeout. A timed-out call is not retried on the same model; it goes to the fallback |
| `LLM_FALLBACK_MODEL` | *(empty)* | Second Groq model tried when the main one is out of retries, timed out or retired (e.g. `openai/gpt-oss-20b`). Empty = no fallback |

### Retrieval and chunking

| Variable | Default | Description |
|---|---|---|
| `RETRIEVAL_TOP_K` | `5` | Chunks per search (a chat request may set 1-20) |
| `RETRIEVAL_MIN_SCORE` | `0.15` | Semantic matches below this cosine similarity are discarded (keyword matches from hybrid search are kept) |
| `RETRIEVAL_HYBRID` | `true` | Also run Postgres full-text search and merge the two rankings with reciprocal rank fusion. `false` = vectors only |
| `RETRIEVAL_CANDIDATES` | `20` | Candidates each half of hybrid search (and the reranker) considers before the top `top_k` are kept |
| `RETRIEVAL_RRF_K` | `60` | The constant in RRF's `1 / (k + rank)`; smaller favours the top of each list more |
| `RERANKER` | `off` | `off` or `cross-encoder`: rescore the fused candidates with `RERANKER_MODEL` (needs `sentence-transformers` and a model download; falls back to the fused order if it cannot load) |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | The cross-encoder used when `RERANKER=cross-encoder` |
| `QUERY_REWRITE_ENABLED` | `true` | Rewrite a follow-up question into a standalone search query from the conversation (one small model call with a key, a heuristic without). Only retrieval uses the rewrite |
| `DEFAULT_CHUNK_STRATEGY` | `fixed_size` | `fixed_size`, `sentence` or `markdown` |
| `DEFAULT_CHUNK_SIZE` | `500` | Tokens per chunk |
| `DEFAULT_CHUNK_OVERLAP` | `50` | Tokens repeated between consecutive chunks |

The `DEFAULT_CHUNK_*` values apply when a document has no chunk options recorded; the upload endpoint's own form defaults are `fixed_size`, 500 and 50.

### Rate limiting

Over a limit, the endpoint answers `429` with `Retry-After` (seconds) and `X-RateLimit-Limit` / `X-RateLimit-Remaining`. Limits are written `<count>/<period>` (`20/minute`, `10/hour`, `5/15minutes`); empty or `off` disables one limit, and an unparseable value stops the app at startup.

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `True` | Master switch |
| `RATE_LIMIT_STORAGE` | `auto` | `auto`: Redis when reachable (shared by every API process), in-memory otherwise (Redis retried every 30 s). `memory`: always in-process |
| `RATE_LIMIT_LOGIN` | `20/minute` | Per client IP, `POST /auth/login` |
| `RATE_LIMIT_SIGNUP` | `10/hour` | Per client IP, `POST /auth/signup` |
| `RATE_LIMIT_PASSWORD_RESET` | `10/hour` | Per client IP, shared by `/auth/password-reset/request` and `/confirm` |
| `RATE_LIMIT_VERIFY_RESEND` | `5/hour` | Per user, `POST /auth/verify-email/resend` |
| `RATE_LIMIT_CHAT` | `30/minute` | Per user, asking a question |
| `RATE_LIMIT_SEARCH` | `60/minute` | Per user, `POST /search/` |
| `RATE_LIMIT_UPLOAD` | `30/minute` | Per user, upload requests |
| `TRUSTED_PROXY_COUNT` | `0` | Proxies you control in front of the API. With N > 0 the client IP is the Nth `X-Forwarded-For` entry from the right; 0 ignores the header and uses the TCP peer |
| `CLIENT_IP_HEADER` | *(empty)* | A header your proxy or CDN overwrites with the client IP (e.g. `CF-Connecting-IP`); wins when present. Only set it if every request passes through that proxy |

### Usage metering and quotas

Every chat answer records a row in `usage_events` (model, mode, tokens in and out, estimated cost, request id). Quotas are per organization; `0` means unlimited. Each can be overridden for one organization by the matching nullable column on `organizations` (`monthly_token_quota`, `max_documents`, `max_storage_bytes`; NULL means use the setting). Admins read the month at `GET /api/v1/org/usage`.

| Variable | Default | Description |
|---|---|---|
| `QUOTA_MONTHLY_TOKENS` | `1000000` | AI tokens per calendar month (UTC). Over it, chat answers `429` with `Retry-After` counting down to the 1st |
| `QUOTA_MAX_DOCUMENTS` | `1000` | Documents an organization may hold. An upload crossing it answers `402` and stores nothing |
| `QUOTA_MAX_STORAGE_BYTES` | `5368709120` (5 GB) | Total bytes of those documents; `402` likewise |
| `LLM_PRICING` | `{"openai/gpt-oss-120b": {"input": 0.15, "output": 0.60}, "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30}}` | USD per million tokens, used only to estimate cost. Set as JSON. Unlisted models cost 0 (logged once) |

### Observability

| Variable | Default | Description |
|---|---|---|
| `LOG_FORMAT` | `json` | `json`: one JSON object per line with `request_id`, `user_id`, `org_id` (API) or `task_id` (worker). `text`: readable lines for a terminal |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Health-check access lines are logged at `DEBUG` |
| `SENTRY_DSN` | *(empty)* | Turns on Sentry for the API, worker and beat. Empty means off, and `sentry-sdk` is not even imported |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.0` | Fraction of requests traced for performance (errors are always sent) |
| `SENTRY_ENVIRONMENT` | *(empty)* | Environment tag; empty uses `ENV` |
| `READINESS_TIMEOUT_SECONDS` | `3.0` | Per-dependency timeout of `GET /api/v1/health/ready` |
| `HSTS_MAX_AGE_SECONDS` | `31536000` | `Strict-Transport-Security` max-age, sent only with `ENV=production`; 0 disables it |

### Single sign-on (OpenID Connect)

Off by default. With all of `OIDC_ENABLED`, `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` set, the login page shows **Continue with &lt;provider&gt;** (`backend/app/api/v1/sso.py`, `frontend/lib/sso.ts`). Accounts are matched by **verified** email: an existing account is signed in to; a new person joins an organization only through an invite (the invite link's SSO button carries the token), otherwise they create their own organization (when `OIDC_ALLOW_SIGNUP`).

| Variable | Default | Description |
|---|---|---|
| `OIDC_ENABLED` | `false` | Master switch |
| `OIDC_DISCOVERY_URL` | *(empty)* | The provider's `/.well-known/openid-configuration` URL (Google: `https://accounts.google.com/.well-known/openid-configuration`; Microsoft: `https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration`; Okta: `https://<org>.okta.com/.well-known/openid-configuration`) |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | *(empty)* | From the application you register with the provider. The secret belongs in `.env` only |
| `OIDC_SCOPES` | `openid email profile` | `email` is required for account matching |
| `OIDC_PROVIDER_NAME` | `SSO` | The button label: "Continue with Google" |
| `OIDC_REDIRECT_URI` | *(empty)* | Empty derives `<API>/api/v1/auth/sso/callback` from the request. Register exactly this URL with the provider |
| `OIDC_ALLOW_SIGNUP` | `true` | Whether a first-time SSO user without an invite may create a new organization. Joining an existing one always needs an invite |
| `OIDC_REQUIRE_EMAIL_VERIFIED` | `true` | Trust only emails the provider marks `email_verified`. Microsoft Entra ID omits that claim, so set `false` for Entra only |

Locally, register `http://localhost:8000/api/v1/auth/sso/callback` as the redirect URI and make sure `FRONTEND_URL` (or the first `FRONTEND_ORIGIN`) is `http://localhost:3000`, which is where the callback sends the browser on to (`/login/sso`).

### Multilingual embeddings

The default local model, `all-MiniLM-L6-v2`, understands English only. `EMBEDDING_MULTILINGUAL=true` makes the **local** provider use `MULTILINGUAL_EMBEDDING_MODEL` (default `paraphrase-multilingual-MiniLM-L12-v2`, 50+ languages in one vector space, so a question in one language can match a passage in another). It also produces 384-dimension vectors, so the `vector(384)` column and `EMBEDDING_DIMENSION` stay as they are; no migration is needed. It is about 470 MB on disk, against about 90 MB for the default.

**Switching requires re-indexing.** Vectors from two models are not comparable, and existing chunks keep the old model's vectors. The model name is stored with every vector (`chunk_embeddings.model_name`), and search only compares vectors from the current model, so documents embedded before the switch stop appearing in results until they are reprocessed. Re-upload them, or mark them pending so the recovery sweep re-runs them:

```sql
UPDATE documents SET status = 'pending', updated_at = now() - interval '1 day'
WHERE id IN (SELECT DISTINCT c.document_id FROM chunks c
             JOIN chunk_embeddings e ON e.chunk_id = c.id
             WHERE e.model_name <> 'paraphrase-multilingual-MiniLM-L12-v2');
```

The same applies when switching back.

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_MULTILINGUAL` | `false` | Use the multilingual local model. Re-index after changing |
| `MULTILINGUAL_EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Any sentence-transformers model with `EMBEDDING_DIMENSION` outputs |

### Query-embedding cache

Off by default. When on, each **question's** vector is cached (`QueryEmbeddingCache` in `services/embedding.py`), so a repeated question skips the model or the paid embedding API. Document chunks are never cached. The key is a SHA-256 of the model name plus the text, so changing model can never return an old vector, and question text is not readable in Redis. Redis errors count as cache misses and fall back to memory; Redis is retried every 30 seconds.

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_CACHE_ENABLED` | `false` | Master switch |
| `EMBEDDING_CACHE_BACKEND` | `auto` | `auto`: Redis (`REDIS_URL`) when reachable, otherwise an in-process LRU. `memory`: always the LRU |
| `EMBEDDING_CACHE_MAX_ENTRIES` | `2048` | Size of the in-memory LRU (about 3 KB per entry at 384 dimensions) |
| `EMBEDDING_CACHE_TTL_SECONDS` | `86400` | Lifetime of an entry, in Redis and in memory |

### Webhooks

Off by default; with `WEBHOOKS_ENABLED=false` every `/api/v1/webhooks` route answers `404`. Admins register HTTPS URLs and the events they want; each event is POSTed as JSON, signed with HMAC-SHA256, retried with backoff and logged per attempt (`services/webhooks.py`, `tasks/webhooks.py`, `api/v1/webhooks.py`, migration `f6a8b0c2d4e7`).

| Variable | Default | Description |
|---|---|---|
| `WEBHOOKS_ENABLED` | `false` | Master switch |
| `WEBHOOK_MAX_ATTEMPTS` | `6` | Attempts per delivery before it is marked `failed` |
| `WEBHOOK_RETRY_BACKOFF_SECONDS` | `30` | First wait; doubled per attempt, capped at one hour |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | How long the receiver has to answer |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | `false` | Turns off the SSRF check. Local testing against a receiver on your machine only |
| `WEBHOOK_ALLOW_HTTP` | `false` | Allows `http://` targets. Local testing only |
| `WEBHOOK_MAX_ENDPOINTS_PER_ORG` | `10` | Endpoints one organization may register |

**Endpoints** (Admin only): `GET /webhooks/events` (the catalogue), `POST /webhooks` (returns the signing secret **once**), `GET /webhooks`, `PATCH /webhooks/{id}` (URL, events, description, `is_active`), `DELETE /webhooks/{id}`, `POST /webhooks/{id}/rotate-secret`, `POST /webhooks/{id}/test` (a `ping`), `GET /webhooks/{id}/deliveries`, `POST /webhooks/{id}/deliveries/{delivery_id}/redeliver`.

**Events**: `document.uploaded`, `document.processed`, `document.failed` (final failure only), `document.deleted`, `member.joined`, `member.role_changed`, `member.deactivated`, `member.reactivated`, `member.removed`, `invite.created`, `api_key.created`, `api_key.revoked`, `ping`; subscribe to `*` for all. Most are produced by `audit.record()` (the `AUDIT_EVENT_MAP` in `services/webhooks.py`), so endpoints need no webhook code; `document.processed` and `document.failed` come from `tasks/ingestion.py`. Deliveries are written in the same transaction as the change and queued only after it commits.

**Verifying a delivery** (receiver side):

```python
import hashlib, hmac, time

def verify(secret: str, headers, raw_body: bytes) -> bool:
    timestamp = headers["X-DocMinds-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:  # reject replays
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, headers["X-DocMinds-Signature"])
```

`X-DocMinds-Delivery` stays the same across retries of one delivery, so receivers can de-duplicate on it.

**SSRF protection**: a target URL must be `https://`, carry no credentials, and every address its host resolves to must be public. Loopback, private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local (including the 169.254.169.254 metadata service), multicast and reserved ranges are refused, at registration and again before every attempt. Redirects are not followed. A small window remains between that check and the HTTP client's own DNS lookup.

**Local testing**: run a receiver (for example `python -m http.server 9000` will answer 501, which still shows the attempt in the delivery log), set `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` and `WEBHOOK_ALLOW_HTTP=true`, and register `http://localhost:9000/`. With `INGESTION_MODE=celery` deliveries are sent by the worker, and Celery beat runs a sweep every 5 minutes for deliveries whose queue message was lost; with `INGESTION_MODE=inline` they are sent from the API process.

### Cloud imports (Google Drive, OneDrive / SharePoint, Notion, Confluence)

Off by default; with `CONNECTORS_ENABLED=false` every `/api/v1/integrations` route answers `404`. Editors and admins connect a source with OAuth, browse it, and import files or pages into a project (`services/connectors/`, `api/v1/integrations.py`, Settings → **Cloud imports** at `/settings/integrations`, migration `b3d5f7a9c1e2`). A provider is offered once its client id **and** secret are set; register `<API>/api/v1/integrations/callback/<provider>` (`google_drive`, `onedrive`, `notion`, `confluence`) as its redirect URI (locally `http://localhost:8000/...`).

| Variable | Default | Description |
|---|---|---|
| `CONNECTORS_ENABLED` | `false` | Master switch |
| `CONNECTOR_REDIRECT_BASE_URL` | *(empty)* | Base of the redirect URI; empty derives it from the request |
| `CONNECTOR_ENCRYPTION_KEY` | *(empty)* | Fernet key(s) for the OAuth tokens at rest. Comma-separated: the first encrypts, all decrypt (rotation). A non-Fernet value is stretched with HKDF. Empty derives a key from `SECRET_KEY`, so changing `SECRET_KEY` then disconnects every source |
| `CONNECTOR_GOOGLE_CLIENT_ID` / `_SECRET` | *(empty)* | A Google Cloud OAuth client (Drive API enabled, scope `drive.readonly`) |
| `CONNECTOR_MICROSOFT_CLIENT_ID` / `_SECRET` | *(empty)* | A Microsoft Entra app registration (delegated `Files.Read.All`, `Sites.Read.All`, `offline_access`) |
| `CONNECTOR_MICROSOFT_TENANT` | `common` | `common`, `organizations` or a tenant id |
| `CONNECTOR_NOTION_CLIENT_ID` / `_SECRET` | *(empty)* | A Notion **public** integration |
| `CONNECTOR_CONFLUENCE_CLIENT_ID` / `_SECRET` | *(empty)* | An Atlassian OAuth 2.0 (3LO) app with the Confluence read scopes |
| `CONNECTOR_TIMEOUT_SECONDS` | `30` | Per call to a provider |
| `CONNECTOR_SYNC_ENABLED` | `false` | Periodic auto-sync (below). **Sync now** works regardless |
| `CONNECTOR_SYNC_INTERVAL_MINUTES` | `60` | How often each imported document is checked |
| `CONNECTOR_SYNC_BATCH_SIZE` | `100` | Most documents one sweep (or one **Sync all**) checks |
| `CONNECTOR_IMPORT_STALE_MINUTES` | `15` | A background import whose worker has not reported for this long is treated as orphaned and resumed (at API start-up, and by the Celery beat sync) |

**Imports run in the background**: `POST /integrations/connections/{id}/import` validates the request, records a `connector_imports` job and answers `202`; the downloads run in the Celery task `run_import_task`, or with `INGESTION_MODE=inline` on an in-process queue with one worker. The page polls `GET /integrations/imports/{id}`; each item goes `pending` → `downloading` → `imported` (or `duplicate`, `already_imported`, `skipped`, `failed` with the reason). A job interrupted by a shutdown is handed back as `queued` and resumed at the next start, skipping the items already finished.

**Disconnecting** deletes the tokens locally first, then revokes DocMinds' grant where the provider has an API (Google's token revocation endpoint, Notion's `/v1/oauth/revoke`). Microsoft and Atlassian have none, so the page links to `https://myapps.microsoft.com` (personal accounts: `https://account.live.com/consent/Manage`) or `https://id.atlassian.com/manage-profile/apps`. A failed revocation is logged and shown, never a failed disconnect.

**Formats**: Drive and OneDrive / SharePoint files are downloaded as they are; Google Docs, Sheets and Slides are exported to `.docx`, `.xlsx` and `.pptx`; Notion pages are rendered to Markdown (`.md`) from their blocks; Confluence pages are saved from their `export_view` as `.html`. Every imported file then passes the upload rules -- extension allowlist and content sniffing, `MAX_UPLOAD_SIZE_BYTES`, de-duplication by SHA-256, versioning by name, the document and storage quotas, a server-chosen storage key, a `document.upload` audit entry (with `source`) -- and is queued through the same ingestion entry point as an upload (`tasks/ingestion.py` `dispatch`).

**Auto-sync**: each import remembers the source id and revision (`connector_items`: Drive `version`, Graph `cTag`, Notion `last_edited_time`, Confluence version number). A check asks the provider for the current revision; only a different revision is downloaded, and only different bytes replace the stored file and re-index the document (`document.sync` audit entry). With `INGESTION_MODE=celery`, Celery beat sends `sync_connectors_task`; with `inline`, a loop inside the API runs the sweep (`services/connectors/scheduler.py`). Each link is claimed with a conditional `UPDATE`, so two processes never sync the same document. Statuses: `synced`, `syncing` (re-indexing), `error`, `source_deleted`, `disconnected` (the connection was removed; the document stays).

**Security**: tokens are Fernet-encrypted in `connector_connections.encrypted_tokens` and never returned. The OAuth `state` is a 10-minute JWT of its own type naming user, organization and provider; the callback re-checks that the user is still an active editor. All provider traffic goes through `services/connectors/http.py`: `https` only, a per-provider host allowlist, every resolved address public, no automatic redirects (a download may follow up to three, only to the provider's file hosts, without our bearer token), and the upload size cap while streaming. Item ids are validated and percent-encoded into the path, never the host.

**Endpoints** (editor+): `GET /integrations/providers`, `POST /integrations/{provider}/authorize`, `GET /integrations/callback/{provider}` (browser redirect), `GET /integrations/connections`, `DELETE /integrations/connections/{id}`, `GET /integrations/connections/{id}/items?parent=&q=&cursor=`, `POST /integrations/connections/{id}/import` (`202`), `GET /integrations/imports?connection_id=`, `GET /integrations/imports/{id}`, `POST /integrations/connections/{id}/sync`, `GET /integrations/documents?project_id=`, `PATCH /integrations/documents/{document_id}` (`auto_sync`), `POST /integrations/documents/{document_id}/sync`.

### Billing (Stripe)

Off by default; with `BILLING_ENABLED=false` every `/api/v1/billing` route answers `404` and quotas stay manual. Three plans map onto the existing quotas: **Free** clears the organization's override columns (so the `QUOTA_*` settings apply), **Pro** and **Team** write `BILLING_PLAN_LIMITS` into them (`services/billing.py`, `api/v1/billing.py`, the plan badge in Settings and the page `/settings/billing`). There is no Stripe SDK: three form POSTs to `api.stripe.com` through `httpx`, and an HMAC check.

| Variable | Default | Description |
|---|---|---|
| `BILLING_ENABLED` | `false` | Master switch |
| `STRIPE_SECRET_KEY` | *(empty)* | `sk_test_...` / `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | *(empty)* | The webhook endpoint's signing secret (`whsec_...`); without it every webhook is refused |
| `STRIPE_PRICE_PRO` / `STRIPE_PRICE_TEAM` | *(empty)* | Recurring price ids; a plan without one cannot be bought |
| `STRIPE_WEBHOOK_TOLERANCE_SECONDS` | `300` | Older signed webhooks are refused (replay protection) |
| `BILLING_PLAN_LIMITS` | Pro: 10 M tokens, 10,000 documents, 50 GB; Team: 50 M tokens, unlimited documents, 250 GB | JSON; `0` = unlimited |
| `BILLING_PRICE_LABELS` | `{"free": "$0", "pro": "$29 / month", "team": "$99 / month"}` | Display only; Checkout shows the real price |

**Endpoints**: `GET /billing` (any member: plan, status, enforced limits, the plans), `POST /billing/checkout` `{plan}` and `POST /billing/portal` (admin; each returns a Stripe URL; audited `billing.checkout` / `billing.portal`), `POST /billing/webhook` (Stripe only). A plan changes **only** through a webhook whose `Stripe-Signature` verifies; each event id is stored in `billing_events` in the same transaction, so a redelivered event changes nothing twice. Handled: `checkout.session.completed`, `customer.subscription.created` / `.updated` (price → plan; `active`, `trialing` and `past_due` keep it, anything else drops to Free), `customer.subscription.deleted`, `invoice.payment_failed`. Each change writes a `billing.plan_change` audit entry. Locally, `stripe listen --forward-to localhost:8000/api/v1/billing/webhook` prints the `whsec_` to use.

### Admin dashboard

Off by default (`ADMIN_DASHBOARD_ENABLED=false`: every `/api/v1/analytics` route is `404`). For admins, `/settings/admin` charts the last 7, 30 or 90 days, each chart with a data table (`services/analytics.py`, `api/v1/analytics.py`, `components/settings/TrendChart.tsx`).

| Variable | Default | Description |
|---|---|---|
| `ADMIN_DASHBOARD_ENABLED` | `false` | Master switch |

**Endpoints** (admin; `days` 1-365, UTC days, every day present): `GET /analytics/overview` (everything; used by the page), `/analytics/usage` (answers, tokens, estimated cost), `/analytics/documents` (completed / failed, by the day they last changed), `/analytics/active-users` (distinct members with a chat answer or an audited action), `/analytics/feedback` (up / down), `/analytics/top-questions?limit=` (grouped after trimming and lower-casing).

### Frontend

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend base URL, set in `frontend/.env.local`. The `NEXT_PUBLIC_` prefix exposes it to browser code, so never put secrets in it. Its origin is also the only external origin in the Content-Security-Policy's `connect-src`, so it is read at build time: changing it means rebuilding |
| `NEXT_PUBLIC_PASSWORD_MIN_LENGTH` | `10` | Minimum length shown in the signup and reset password hints. Set it to the backend's `PASSWORD_MIN_LENGTH` if you change that; a mismatch only makes the hint less helpful, since the API still enforces its own value |

### Product analytics (optional)

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_POSTHOG_KEY` | *(empty)* | PostHog project key. Empty means no analytics: no banner, the PostHog library is never loaded, and its host is not added to the CSP |
| `NEXT_PUBLIC_POSTHOG_HOST` | `https://us.i.posthog.com` | `https://eu.i.posthog.com` for the EU cloud, or a self-hosted URL. Added to the CSP `connect-src` only when a key is set |

With a key, a banner asks each visitor first; nothing loads or is sent until they choose **Allow**, and the choice is remembered in `localStorage` (`docminds_analytics_consent`). No personal data is sent: `identify` is never called, autocapture and session recording are off, IPs are not stored with events, and every URL is reduced to its path before sending, because query strings and fragments here carry invite, reset and SSO tokens (`frontend/lib/analytics.ts`). Both values are read at build time.

There is no development proxy; the browser calls the API directly, so the API's CORS origins must include the frontend URL.

---

## Frontend conventions

- **Typed API client:** `frontend/openapi.json` is the backend's OpenAPI schema, exported by `cd backend && python scripts/export_openapi.py` (`--check` fails when it is stale); `npm run gen:api` turns it into `lib/api-types.ts` with openapi-typescript, and `npm run check:api` (run in CI, no backend needed) fails when the two have drifted. Commit both files whenever an endpoint or schema changes.
- **Adding an endpoint:** prefer the generated type -- `export type Foo = Schema<"FooOut">` in `lib/api.ts` -- and hand-write an interface only when it must be stricter than the schema (a role union, say); add a method to `api` that calls `apiFetch<YourType>(path, ...)`; call it from the component. Raw `fetch` opts out of `apiFetch`'s 401 handling (clear the token, redirect to login), so use it only for endpoints that do not take JSON.
- **Loading, error and success are separate states**, each with its own render.
- **Write Tailwind class names out in full.** Tailwind generates only classes it finds literally in source, so a runtime-built class such as `` `text-${color}-500` `` is purged from the build.
- **Signed-in pages go inside `<AppShell>`** (`components/app/`), which supplies the sidebar/topbar, theme toggle, sign-out and the page transition. Use theme tokens (`var(--text-secondary)`, `var(--surface-1)`, ...) rather than literal colours, so both themes work; the light palette is `:root[data-theme="light"]` in `globals.css`, set before first paint by the inline script from `lib/theme.ts`.
- **Translations:** user-facing strings in the app shell, auth pages, chat and upload come from `lib/i18n` (`const { t } = useT(); t("upload.dropToUpload")`), one dictionary file per area with `en` and `hi`. English is the default and the fallback for any key Hindi lacks; the switcher (`components/app/LanguageSwitcher.tsx`) stores the choice in localStorage and sets `<html lang>`. Add the English string first; a missing translation is never an error.
- **Destructive actions use `<HoldToConfirm>`**, not `window.confirm()`. Loading states use `<LogoLoader>`; first-run states use `FirstRunEmpty` / `GuidedTour` from `components/app/Onboarding.tsx`.
- **Accessibility is tested:** `tests/Accessibility.test.tsx` runs axe (vitest-axe) over the dashboard, project and settings pages. Motion must respect `prefers-reduced-motion` (CSS block at the end of `globals.css`, `usePrefersReducedMotion` in JS).

---

## Testing

### Backend (pytest)

```bash
cd backend
python -m pytest                          # all tests
SKIP_DB_TESTS=1 python -m pytest          # tier 1 only
python -m pytest tests/test_chunker.py -v # one file
python -m pytest -m "not gemini_live"     # skip the live Gemini test
```

| Tier | Requires | Covers |
|---|---|---|
| Tier 1 | Nothing | Chunking, extraction dispatch, hashing, security, prompts, embeddings, schemas, database URL handling, the agent (scripted model), roles and password policy (`test_access_control.py`) |
| Tier 1 + 2 (`test_auth_flows.py`) | Nothing for the `[sqlite]` run; Postgres for the `[postgres]` run | Signup and invites, refresh rotation and logout, password reset, email verification, role enforcement, members admin, API keys, audit log, the locked-down task endpoints |
| Tier 1 (`test_uploads.py`, `test_upload_limit.py`, `test_storage.py`, `test_zip_safety.py`, `test_malware.py`) | Nothing | Filename sanitizing, content sniffing, the streamed size limit, the request-size middleware (driven directly as an ASGI app, including chunked bodies with no `Content-Length`), local and S3 storage (against a fake boto3 client, plus the real client's configuration), zip-bomb limits for archives and Office files, the clamd protocol (against a fake daemon on a loopback socket) |
| Tier 1 + 2 (`test_ingestion_durability.py`, `test_upload_hardening.py`) | Nothing for `[sqlite]`; Postgres for `[postgres]` | The claim, retries, quarantine, the recovery sweep, inline retries and the Celery task options; the upload and preview endpoints (`413`, `415`, storage keys, queueing after the commit) |
| Tier 1 (`test_rate_limit.py`, `test_observability.py`, `test_llm_robustness.py`) | Nothing | The sliding-window limiter (fake clock, fake and failing Redis), client-IP resolution behind proxies, `429` + `Retry-After` on every limited endpoint; JSON logs with request context, Sentry staying off, request ids, the access log, security headers, `/health/ready` with faked dependency checks; LLM retries, backoff and fallback (fake Groq client), the question being sent once, prompt-injection fencing in rag and agent output, retrieval's model filter and off-loop embedding |
| Tier 1 + 2 (`test_usage.py`) | Nothing for `[sqlite]`; Postgres for `[postgres]` | Usage events per chat answer, the token quota (`429`), document and storage quotas (`402`, nothing stored), per-org overrides, `GET /org/usage` |
| Tier 1 (`test_retrieval_quality.py`) | Nothing | Reciprocal rank fusion, the reranker (and its fail-safe), follow-up query rewriting (heuristic and a faked model), conversation titles, the SSE framing, Markdown export, and the evaluation harness (golden-set loading, hit rate, MRR, judge parsing, the CLI's errors) |
| Tier 1 + 2 (`test_product_api.py`) | Nothing for `[sqlite]`; Postgres for `[postgres]` | Project get / rename / delete (cascades and stored files), document get / rename / delete / re-index / chunks, role and tenant checks and audit entries; chat rename, list by project, Markdown export, the document filter and the rewritten follow-up reaching retrieval; the streaming endpoint's events in rag and agent mode, the answer saved with mode, model and steps, nothing saved on a mid-stream failure; the admin feedback summary |
| Tier 1 + 2 (`test_connectors.py`, `test_billing.py`) | Nothing for `[sqlite]`; Postgres for `[postgres]` | Token encryption and rotation, the OAuth state, the connector SSRF guard (allowlist, private addresses, redirects without our token, size cap), id validation, provider parsing against faked Drive / Graph / Notion / Confluence APIs; through the API with a fake provider: connect, browse, background import (202, per-item progress while running, claimed once, resumed when stale, failed when disconnected mid-way; queued, audited, de-duplicated, content-checked), revocation on disconnect (Google, Notion; Microsoft and Confluence link instead; a failure never fails the disconnect), Sync now, refresh after a 401, the periodic sweep, disconnect, roles, `404` while off. Stripe signatures, plan-to-quota mapping, the webhook lifecycle and idempotency, Checkout and the portal against a faked Stripe; the admin dashboard series and top questions |
| Tier 2 (`test_retrieval_db.py`, `test_api.py`) | Postgres with pgvector, migrated | Retrieval SQL, hybrid search (an exact code found only by the keyword half, tenant and document filters on both halves), tenant isolation, cascades, the HTTP layer |
| `gemini_live` marker | `GEMINI_API_KEY` | One call to the real Gemini embedding API |

`test_auth_flows.py`, `test_ingestion_durability.py`, `test_upload_hardening.py` and `test_product_api.py` run every test twice (the last two build their SQLite database with `tests/sqlite_harness.py`). The `[sqlite]` run builds the schema from the models in an in-memory SQLite database (via `aiosqlite`) and always runs; it is acceptable there because the account system uses plain rows, not pgvector or JSONB queries. The `[postgres]` run uses the migrated database, which is what checks that the Alembic migration matches the models. Both replace bcrypt with SHA-256 for speed.

Tier 2 tests skip, with a reason, when Postgres is unreachable or pgvector is missing. CI runs them against a pgvector service and fails if any test is skipped. They use the real database rather than SQLite because the queries depend on the `vector` type, the `<=>` operator and JSONB. `tests/test_agent.py` drives the real Pydantic AI agent and tools with a scripted model, so it needs no Groq key.

#### Writing tests

- Write a Tier 1 test whenever the behaviour can be tested without a database.
- A Tier 2 file needs the module-level `pytestmark` skip used in `test_retrieval_db.py` and `test_api.py`, and must create data through the `db` fixture so the organisations it creates (and everything below them, by cascade) are deleted afterwards.
- Tier 2 runs against the database configured by `POSTGRES_*`, which must be migrated; rows are created there and removed after each test.
- Tests share one database, so use names that are unique per test to avoid unique-constraint collisions.
- Confirm a new test can fail: break the code it covers and check that it goes red.

Rate limits are switched off for every test by an autouse fixture in `tests/conftest.py` (the suite signs up many users from one address); `test_rate_limit.py` turns them back on where they are the subject.

The streaming endpoint opens its own database session through `app.api.v1.chat.SessionLocal`; tests that call it on SQLite patch that name the same way they patch `app.tasks.ingestion.SessionLocal`.

Known gaps: binary PDF and Office extraction, the full-text half of hybrid search on SQLite (it is Postgres-only and covered in Tier 2), real OCR engines, a real S3 bucket and a real ClamAV daemon (both are tested against fakes), Celery's own retry scheduling and time limits (the task options are checked, not exercised), and live Groq tool calling.

### Frontend unit tests (Vitest)

```bash
cd frontend
npm test                          # watch mode
npm run test:run                  # single run
npx vitest run tests/api.test.ts  # one file
```

Vitest with Testing Library and jsdom; no backend or network required. Suites cover `lib/api.ts` (silent refresh, logout, `ApiError` and `Retry-After`), the streaming client and the batch-2 endpoints (`streaming.test.ts`: SSE parsing across read boundaries, handler order, `error` events, upload progress over a fake `XMLHttpRequest`), `ChatPanel` (streamed tokens and live steps, Stop, history sidebar, document filter, copy and export, the source viewer), `SourceViewer` (pdf.js mocked: the cited page and snippet, page navigation, the text fallback), Markdown export and passage matching (`export.test.ts`), `SearchPanel`, `UploadZone` (per-file progress and errors, at most three in flight, `413`/`415`/`429`/`402` wording), the project page's polling, auth guard and role gating, the signed-out pages (`AuthPages.test.tsx`: sign in, sign up, invites, forgot/reset password, verify email), the settings area and dashboard (`Settings.test.tsx`), and the password policy, role rules and security headers (`policy.test.ts`), and the cloud-import, billing and admin-dashboard pages (`Platform.test.tsx`: connect, browse and import, Sync now, Checkout and portal redirects, charts with data tables, the settings links shown only when each feature is on). `tests/setup.ts` stubs `next/navigation` (set `searchParamsMock.value` to drive `useSearchParams`), `window.location`, `scrollIntoView` and `framer-motion` (one cached component per tag, so inputs inside `motion.div` keep focus while typing), and clears `localStorage` between tests. Component tests mock `lib/api`; page tests mock only the `api` object and keep the real error helpers.

### End-to-end tests (Playwright)

A single serial journey in Chromium against the real backend: sign up, create a project, upload a document, wait for **Ready**, search, and ask a question with a cited answer. It is the only suite that detects drift between `frontend/lib/api.ts` and `backend/app/schemas/`.

```bash
# backend, with these providers in both the API and the worker:
export EMBEDDING_PROVIDER=local LLM_PROVIDER=mock OCR_PROVIDER=mock
cd backend && docker compose up -d && alembic upgrade head
uvicorn app.main:app --port 8000
celery -A app.core.celery_app worker --loglevel=info      # --pool=solo on Windows

# then
cd frontend
npx playwright install chromium      # first run only
npm run test:e2e                     # or npm run test:e2e:ui
```

Playwright starts `next dev` itself. Set `E2E_BASE_URL` to run against a frontend other than `http://localhost:3000`. Embeddings must be `local`, not `mock`: mock vectors are hashes, so a search for a sentence from the document would not match its chunk. The first run downloads the local model. Retries are disabled locally and set to one in CI.

---

## Evaluating answer quality

`backend/app/eval` measures retrieval (and optionally answers) against a golden set of questions whose right sources you know. Start from `app/eval/golden_example.json`; the format is described in `app/eval/harness.py` and [ARCHITECTURE.md](ARCHITECTURE.md#answer-quality).

```bash
cd backend
# retrieval only: hit rate and MRR, no API key needed
python -m app.eval --golden app/eval/golden_example.json --project-id <uuid>

# compare against vector-only search
python -m app.eval --golden my_set.json --project-id <uuid> --no-hybrid

# also generate answers: keyword check, and faithfulness by an LLM judge when GROQ_API_KEY is set
python -m app.eval --golden my_set.json --project-id <uuid> --answers --output report.json

# as a gate: exit 1 below a hit rate
python -m app.eval --golden my_set.json --project-id <uuid> --min-hit-rate 0.8
```

It reads the database configured by `POSTGRES_*` / `DATABASE_URL`, uses the current retrieval settings (`RETRIEVAL_*`, `RERANKER`), and never writes anything or records usage. Good questions to add: the ones people rated thumbs-down (Settings > Feedback, or `GET /api/v1/org/feedback`), and a few whose answer is not in the documents (an empty `expected_documents` list -- a hit then means nothing was returned).

---

## Linting and build checks

```bash
# backend (configuration in backend/ruff.toml)
pip install ruff
ruff check backend

# frontend
cd frontend
npm run lint
npm run build      # also type-checks
```

For changes that touch the Next.js framework, also start `npm run dev` and load each route, since some runtime issues only surface there.

---

## Database migrations

```bash
cd backend
alembic upgrade head                              # apply all
alembic revision --autogenerate -m "description"  # generate from model changes
alembic downgrade -1                              # revert the last revision
```

The chain is linear -- check there is exactly one head (`grep -n down_revision app/db/migrations/versions/*.py`) before adding a revision. The current head is `b3d5f7a9c1e2` (cloud-import connections and links, billing events and the organization's plan), chained after the large-file (`b8d0f2a4c6e8`) and collaboration (`c9e1a3b5d7f0`) revisions; `a7b9c1d3e5f8` added chat-message `mode` / `model` / `steps` and the full-text index for hybrid search. That index is created on an EXPRESSION in the migration only and is not declared on the model, so autogenerate may offer to drop `ix_chunks_content_fts`: delete that line from the generated revision.

Alembic uses the synchronous psycopg2 URL. Always review a generated migration: autogenerate represents a renamed column as a drop plus an add, which deletes data; use `op.alter_column(..., new_column_name=...)` instead.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Chat shows "Demo mode -- no GROQ_API_KEY configured" | Set `GROQ_API_KEY` in `backend/.env`, restart the API, and confirm `/api/v1/health` reports `"api_key_configured": true` |
| Deep research reports "The AI agent could not finish answering this question" | The exception name is included. `UsageLimitExceeded` means `AGENT_MAX_STEPS` was reached: raise it or narrow the question. A rate-limit error means the Groq quota is exhausted; retry later or use Quick answer. If `AGENT_MODEL` is set, confirm it supports tool calling |
| Documents stay on Queued | No worker is consuming the queue. Start the Celery worker (with `--pool=solo` on Windows), check Redis with `docker ps`, and test the queue with `POST /api/v1/tasks/trigger-add`. A document queued while Redis was down is re-queued by the recovery sweep after `INGESTION_STALE_AFTER_MINUTES` |
| Upload refused with `413` | A file is over `MAX_UPLOAD_SIZE_BYTES`, or the request is over `MAX_UPLOAD_SIZE_BYTES` x `MAX_FILES_PER_UPLOAD`. Raise the setting or split the upload |
| Upload refused with `415` | The extension is not one the extractor reads, or the bytes do not match it (for example a renamed file, or a text file saved as UTF-16; save it as UTF-8) |
| Upload refused with `422` | `chunk_strategy` is not `fixed_size`, `sentence` or `markdown`; `chunk_size` is outside 50-4000; or `chunk_overlap` is negative, over 1000 or not smaller than `chunk_size` |
| Preview returns `409` | `MALWARE_SCAN=clamav` and the file has not been cleared yet: it is still queued or processing, or it failed before its scan finished (usually because clamd was unreachable). Fix clamd, then re-process the document (see [Re-processing a failed document](DEPLOYMENT.md#re-processing-a-failed-document)) |
| Preview returns `410` | The malware scan found something; the file was quarantined and deleted. `meta_data.malware_signature` names what was found |
| A `.docx`, `.pptx` or `.xlsx` fails with "expands to more than ... MB" | The file's parts declare more than `ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES` uncompressed. Real documents rarely approach 500 MB; raise the setting only if you trust the source |
| Every document fails with "ClamAV returned no verdict" or "Cannot reach ClamAV" | `MALWARE_SCAN=clamav`, but clamd is not running, is still loading definitions, or refused the file as larger than its `StreamMaxLength`. Check `docker compose --profile scan ps` and raise `StreamMaxLength` in `clamd.conf` |
| `expected 384 dimensions, not 1536` | `EMBEDDING_DIMENSION` does not match the provider, or the provider changed without re-processing. Fix the setting and re-upload affected documents |
| Search returns nothing | No document is `completed` yet, or all matches score below `RETRIEVAL_MIN_SCORE`. Lower it temporarily (for example `0.05`) and inspect scores in the Search tab |
| Answers are wrong | Use the Search tab: if the right passage is retrieved, the issue is generation (prompt, model); if not, it is retrieval (chunking, `top_k`, threshold, embeddings) |
| CORS errors in the browser | `localhost:3000` is allowed by default. For other origins set `FRONTEND_ORIGIN` on the API to the exact URL, including scheme |
| OCR fails or returns empty text | Tesseract is a system program, not a Python package. Install it and, on Windows, set `TESSERACT_CMD` to its full path |
| Connection refused on port 5432 | The Docker database listens on host port 5433 |

---

## Glossary

| Term | Meaning |
|---|---|
| RAG | Retrieval-Augmented Generation: retrieve relevant passages from your documents, add them to the prompt, and have a language model answer from them |
| Chunk | A section of a document, a few hundred tokens long; the unit that is embedded, retrieved and cited |
| Embedding | A fixed-length vector representing the meaning of a text; similar meanings produce nearby vectors, which enables semantic search |
| pgvector | PostgreSQL extension providing the `vector` type, distance operators such as `<=>`, and HNSW indexes |
| Cosine similarity | Closeness of two vectors by angle; DocMinds reports `1 - cosine distance` as the score |
| Top-k | The number of chunks a search returns |
| Agent | A language model given tools that decides which to call, and how often, before answering; DocMinds' `agent` chat mode |
| Tool | A function the agent may ask the application to run (`search_documents`, `list_documents`, `read_document_section`), scoped to the caller's organisation and project |
| Tenant | An organisation; `org_id` is the isolation boundary enforced inside every query |
