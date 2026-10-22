# Development

Local setup, the full configuration reference, testing, linting and troubleshooting. For how the system works see [ARCHITECTURE.md](ARCHITECTURE.md).

## Contents

1. [Prerequisites](#prerequisites)
2. [Local setup](#local-setup)
3. [Configuration reference](#configuration-reference)
4. [Frontend conventions](#frontend-conventions)
5. [Testing](#testing)
6. [Linting and build checks](#linting-and-build-checks)
7. [Database migrations](#database-migrations)
8. [Troubleshooting](#troubleshooting)
9. [Glossary](#glossary)

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
| `MAX_UPLOAD_SIZE_BYTES` | `52428800` (50 MB) | Per-file limit, counted while the upload is read in 1 MB chunks. Over it: `413` |
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
| `INGESTION_RETRY_BACKOFF_MAX_SECONDS` | `600` | Cap on the exponential backoff between retries (2 s, 4 s, 8 s, ...) |
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

### Retrieval and chunking

| Variable | Default | Description |
|---|---|---|
| `RETRIEVAL_TOP_K` | `5` | Chunks per search (a chat request may set 1-20) |
| `RETRIEVAL_MIN_SCORE` | `0.15` | Matches below this cosine similarity are discarded |
| `DEFAULT_CHUNK_STRATEGY` | `fixed_size` | `fixed_size`, `sentence` or `markdown` |
| `DEFAULT_CHUNK_SIZE` | `500` | Tokens per chunk |
| `DEFAULT_CHUNK_OVERLAP` | `50` | Tokens repeated between consecutive chunks |

The `DEFAULT_CHUNK_*` values apply when a document has no chunk options recorded; the upload endpoint's own form defaults are `fixed_size`, 500 and 50.

### Frontend

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend base URL, set in `frontend/.env.local`. The `NEXT_PUBLIC_` prefix exposes it to browser code, so never put secrets in it |

There is no development proxy; the browser calls the API directly, so the API's CORS origins must include the frontend URL.

---

## Frontend conventions

- **Adding an endpoint:** add the TypeScript interface to `lib/api.ts`, matching the backend schema field for field; add a method to `api` that calls `apiFetch<YourType>(path, ...)`; call it from the component. Raw `fetch` opts out of `apiFetch`'s 401 handling (clear the token, redirect to login), so use it only for endpoints that do not take JSON.
- **Loading, error and success are separate states**, each with its own render.
- **Write Tailwind class names out in full.** Tailwind generates only classes it finds literally in source, so a runtime-built class such as `` `text-${color}-500` `` is purged from the build.

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
| Tier 1 (`test_uploads.py`, `test_storage.py`, `test_zip_safety.py`, `test_malware.py`) | Nothing | Filename sanitizing, content sniffing, the streamed size limit, local and S3 storage (against a fake boto3 client), zip-bomb limits, the clamd protocol (against a fake daemon on a loopback socket) |
| Tier 1 + 2 (`test_ingestion_durability.py`, `test_upload_hardening.py`) | Nothing for `[sqlite]`; Postgres for `[postgres]` | The claim, retries, quarantine, the recovery sweep, inline retries and the Celery task options; the upload and preview endpoints (`413`, `415`, storage keys, queueing after the commit) |
| Tier 2 (`test_retrieval_db.py`, `test_api.py`) | Postgres with pgvector, migrated | Retrieval SQL, tenant isolation, cascades, the HTTP layer |
| `gemini_live` marker | `GEMINI_API_KEY` | One call to the real Gemini embedding API |

`test_auth_flows.py`, `test_ingestion_durability.py` and `test_upload_hardening.py` run every test twice (the last two build their SQLite database with `tests/sqlite_harness.py`). The `[sqlite]` run builds the schema from the models in an in-memory SQLite database (via `aiosqlite`) and always runs; it is acceptable there because the account system uses plain rows, not pgvector or JSONB queries. The `[postgres]` run uses the migrated database, which is what checks that the Alembic migration matches the models. Both replace bcrypt with SHA-256 for speed.

Tier 2 tests skip, with a reason, when Postgres is unreachable or pgvector is missing. CI runs them against a pgvector service and fails if any test is skipped. They use the real database rather than SQLite because the queries depend on the `vector` type, the `<=>` operator and JSONB. `tests/test_agent.py` drives the real Pydantic AI agent and tools with a scripted model, so it needs no Groq key.

#### Writing tests

- Write a Tier 1 test whenever the behaviour can be tested without a database.
- A Tier 2 file needs the module-level `pytestmark` skip used in `test_retrieval_db.py` and `test_api.py`, and must create data through the `db` fixture so the organisations it creates (and everything below them, by cascade) are deleted afterwards.
- Tier 2 runs against the database configured by `POSTGRES_*`, which must be migrated; rows are created there and removed after each test.
- Tests share one database, so use names that are unique per test to avoid unique-constraint collisions.
- Confirm a new test can fail: break the code it covers and check that it goes red.

Known gaps: binary PDF and Office extraction, real OCR engines, a real S3 bucket and a real ClamAV daemon (both are tested against fakes), Celery's own retry scheduling and time limits (the task options are checked, not exercised), and live Groq tool calling.

### Frontend unit tests (Vitest)

```bash
cd frontend
npm test                          # watch mode
npm run test:run                  # single run
npx vitest run tests/api.test.ts  # one file
```

Vitest with Testing Library and jsdom; no backend or network required. Suites cover `lib/api.ts`, `ChatPanel`, `SearchPanel`, `UploadZone` and the project page's polling and auth guard. `tests/setup.ts` stubs `next/navigation`, `window.location`, `scrollIntoView` and `framer-motion`, and clears `localStorage` between tests. Component tests mock `lib/api`.

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
