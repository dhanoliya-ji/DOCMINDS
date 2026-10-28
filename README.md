# DocMinds

Upload documents, then ask questions about them. Answers cite the exact file and page.

[Live demo](https://docminds-dhanoliya.vercel.app) · [API docs](https://docminds-api.onrender.com/docs)

## How it works (RAG in 4 steps)

1. **Extract** text from the upload (PDF, Office, HTML, email, images via OCR, ZIP …).
2. **Chunk** the text into small page-aware pieces.
3. **Embed** each chunk as a vector and store it in PostgreSQL + pgvector.
4. **Answer**: embed the question, find the closest chunks (vector + keyword search),
   and ask the LLM (Groq) to answer *only* from them, with numbered citations.

Two chat modes: `rag` (one search, one LLM call) and `agent` (the LLM plans several
searches with tools `search_documents`, `list_documents`, `read_document_section`).

## Where the logic lives

```
backend/app/
  main.py                 FastAPI app, mounts every router
  core/config.py          every setting (env var or backend/.env)
  api/v1/*.py             HTTP endpoints (auth, projects, documents, chat, search, billing …)
  models.py, schemas.py   database tables / request+response shapes
  services/
    extractor.py ocr.py   step 1: file -> text
    chunker.py            step 2: text -> chunks
    embedding.py          step 3: chunks -> vectors
    retrieval.py          step 4: question -> best chunks
    llm.py agent.py       step 4: chunks -> answer
  tasks/ingestion.py      background job that runs steps 1-3 (Celery or inline)
frontend/
  app/                    Next.js pages
  components/             UI pieces (ChatPanel, UploadZone …)
  lib/api.ts              every call to the backend
```

Other features: organisations with viewer/editor/admin roles, invites, API keys, audit log,
SSO, share links, prompt library, document compare, usage quotas, Stripe/Razorpay billing,
webhooks, cloud imports (Google Drive, OneDrive, Notion, Confluence), English + Hindi UI.

## Run locally

Needs Python 3.11+, Node 20.9+, Docker (and Tesseract for OCR of scans).

```bash
cd backend
docker compose up -d                 # Postgres + pgvector, Redis
cp .env.example .env                 # add GROQ_API_KEY (optional: without it answers are demo-mode)
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
celery -A app.core.celery_app worker --pool=solo    # or set INGESTION_MODE=inline and skip this

cd ../frontend
npm install && cp .env.local.example .env.local && npm run dev   # http://localhost:3000
```

Set `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock OCR_PROVIDER=mock` to run with no API keys at all.
All settings and their defaults are in `backend/app/core/config.py` and `backend/.env.example`.

## Tests

```bash
cd backend && python -m pytest      # DB tests run when Postgres is up (SKIP_DB_TESTS=1 to skip)
cd frontend && npm run test:run     # Vitest
cd frontend && npm run test:e2e     # Playwright, needs the backend running
```

After changing the API, regenerate the frontend types: `cd frontend && npm run gen:api`.

## Deploy

`render.yaml` deploys the API to Render; the frontend deploys to Vercel. In production set a real
`SECRET_KEY` (the app refuses to start with the default when `ENV=production`).

## Security

Please report vulnerabilities privately via GitHub Security Advisories, not public issues.
