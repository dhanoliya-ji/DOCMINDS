# Deploy DocMinds as a free live demo

Four free services, none of which expires:

| Piece | Service | Why |
|---|---|---|
| Database (PostgreSQL + pgvector) | [Neon](https://neon.tech) | Free plan has no expiry and includes pgvector. Render's free Postgres is **deleted after 30 days**. |
| API | [Render](https://render.com) via [`render.yaml`](render.yaml) | Free web service. |
| Frontend (Next.js) | [Vercel](https://vercel.com) | Free, and made for Next.js. |
| AI | [Gemini](https://aistudio.google.com/apikey) (embeddings) + [Groq](https://console.groq.com/keys) (answers) | Both have free API keys, no card. |

## How the demo differs from running locally

Locally, `docker-compose` runs an API, a Celery worker, Redis and a local PyTorch
embedding model. Render's free plan can't host that: background workers aren't free,
and a free web service has 512 MB of memory, less than PyTorch needs. So the demo
uses a lighter build of the same code, switched on by two settings in `render.yaml`:

- **`EMBEDDING_PROVIDER=gemini`**: embeddings come from Google's free API. Render passes
  this to the Docker build too, and the image is then built **without PyTorch**. CI's
  *Free-demo image* job checks that this build starts and stays well under 512 MB.
- **`INGESTION_MODE=inline`**: the API processes each upload itself, right after
  replying, so there's no worker and no Redis. Uploads still return at once and the
  status still moves from *pending* to *completed*, exactly as before.

One trade-off: a free Render service loses its disk when it restarts, so the
**original uploaded files** go with it. Their extracted text and embeddings are in
Neon, so **search and chat keep working**; only the file preview is lost.

## Steps

### 1. Get the free keys (about 5 minutes)

1. **Neon:** sign up at https://neon.tech and create a project (e.g. `docminds`, region
   **AWS US West 2 / Oregon**, near Render's default). Click **Connect**, turn
   **Connection pooling off**, and copy the connection string.
2. **Groq:** https://console.groq.com/keys → **Create API key**.
3. **Gemini:** https://aistudio.google.com/apikey → **Create API key**.

pgvector is switched on by the first migration (`CREATE EXTENSION IF NOT EXISTS vector`);
Neon allows that, so there's nothing to enable by hand.

### 2. Deploy the API on Render

1. https://dashboard.render.com/blueprints → **New Blueprint Instance** → pick this repo.
2. Paste the values it asks for: `DATABASE_URL` (Neon), `GROQ_API_KEY`, `GEMINI_API_KEY`.
   Leave **`FRONTEND_ORIGIN`** empty for now.
3. **Apply.** When it's live, open `https://<your-api>.onrender.com/api/v1/health`. It
   should answer `{"status": "ok", ...}`. Copy the API's address.

### 3. Deploy the frontend on Vercel

1. https://vercel.com/new → import this repo.
2. Set **Root Directory** to `frontend`. Vercel detects Next.js by itself.
3. Under **Environment Variables**, add `NEXT_PUBLIC_API_URL` = your Render API address
   (e.g. `https://docminds-api-xxxx.onrender.com`, with no trailing slash).
4. **Deploy**, then copy the site's address (e.g. `https://docminds.vercel.app`).

### 4. Connect the two

In Render, open **docminds-api → Environment**, set **`FRONTEND_ORIGIN`** to the Vercel
address, and save. Without this, the browser blocks every request with a CORS error.
For several addresses, separate them with commas.

Then open the Vercel site, sign up, create a project and upload a document.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Document stays *failed*, error mentions `GEMINI_API_KEY` | Key missing or mistyped | Set it in Render → Environment |
| Document *failed* with `HTTP 429` | Gemini's free rate limit | Wait a minute and upload again |
| Browser console shows a CORS error | `FRONTEND_ORIGIN` not set, or set to a different address | Step 4 |
| First request takes ~1 minute | Free services sleep after 15 minutes idle | Expected on the free plan |
| `unexpected keyword argument 'sslmode'` | An old build | Redeploy from `main`; the app now converts Neon's URL options itself |
