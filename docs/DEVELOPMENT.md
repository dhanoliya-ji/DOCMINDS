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
| `MAX_UPLOAD_SIZE_BYTES` | `52428800` (50 MB) | Defined but not currently enforced by the upload endpoint |

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
| `SECRET_KEY` | A committed development value | JWT signing key. **Set your own outside local development** (`openssl rand -hex 32`) |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |

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
| Tier 1 | Nothing | Chunking, extraction dispatch, hashing, security, prompts, embeddings, schemas, database URL handling, the agent (scripted model) |
| Tier 2 (`test_retrieval_db.py`, `test_api.py`) | Postgres with pgvector, migrated | Retrieval SQL, tenant isolation, cascades, the HTTP layer |
| `gemini_live` marker | `GEMINI_API_KEY` | One call to the real Gemini embedding API |

Tier 2 tests skip, with a reason, when Postgres is unreachable or pgvector is missing. CI runs them against a pgvector service and fails if any test is skipped. They use the real database rather than SQLite because the queries depend on the `vector` type, the `<=>` operator and JSONB. `tests/test_agent.py` drives the real Pydantic AI agent and tools with a scripted model, so it needs no Groq key.

#### Writing tests

- Write a Tier 1 test whenever the behaviour can be tested without a database.
- A Tier 2 file needs the module-level `pytestmark` skip used in `test_retrieval_db.py` and `test_api.py`, and must create data through the `db` fixture so the organisations it creates (and everything below them, by cascade) are deleted afterwards.
- Tier 2 runs against the database configured by `POSTGRES_*`, which must be migrated; rows are created there and removed after each test.
- Tests share one database, so use names that are unique per test to avoid unique-constraint collisions.
- Confirm a new test can fail: break the code it covers and check that it goes red.

Known gaps: binary PDF and Office extraction, real OCR engines, multipart upload, the ingestion task as a single run, and live Groq tool calling.

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
| Documents stay on Queued | No worker is consuming the queue. Start the Celery worker (with `--pool=solo` on Windows), check Redis with `docker ps`, and test the queue with `POST /api/v1/tasks/trigger-add` |
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
