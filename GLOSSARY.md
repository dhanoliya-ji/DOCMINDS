# Glossary

Every term this project uses that is not plain English, explained without
assuming you have met it before. Roughly in the order you meet them.

Jump to: [RAG](#the-rag-terms) · [Storage](#storage-and-search) ·
[Models](#models-and-providers) · [Infrastructure](#infrastructure) ·
[Web](#web-and-api) · [Tuning](#the-numbers-you-can-tune)

---

## The RAG terms

### RAG
**Retrieval-Augmented Generation.** Three words, in order, describing exactly
what happens:

1. **Retrieval** — find the passages in *your* documents that relate to the
   question.
2. **Augmented** — paste those passages into the prompt.
3. **Generation** — ask a language model to answer using them.

The point is the middle step. A model on its own answers from what it absorbed
during training — it has never seen your company handbook, and when asked about
it will produce something plausible and wrong. RAG hands it the relevant pages
first, so the answer comes from your documents rather than from its memory.

### Hallucination
When a model states something false with complete confidence. It is the failure
mode RAG exists to reduce, and it is *not* a bug the model could be patched to
fix — a language model generates likely text, and likely text is often untrue.
Grounding it in retrieved excerpts is the mitigation.

DocMinds pushes on this from three directions: excerpts in the prompt, a
temperature near zero, and a minimum similarity score so an unanswerable
question retrieves nothing and gets "I don't know" rather than invention.

### Chunk
A piece of a document, a few hundred tokens long. Documents are split into
chunks before storage, and a chunk is the unit that gets searched, retrieved and
cited.

**Why not store the whole document?** Because a single vector representing a
300-page handbook is an average of everything it says, and therefore close to
nothing in particular. Chunks are what make a search result specific enough to
answer with.

### Overlap
The last ~50 tokens of one chunk repeated at the start of the next. It exists
because a fact that straddles a boundary would otherwise be in neither chunk in
full, and so would match neither well. The cost is storing that text twice,
which is cheap.

### Citation
The bracketed `[1]`, `[2]` markers in an answer, each pointing at the exact
file, page and passage it came from. They are the difference between an answer
you can check and one you have to trust.

They work because every excerpt is numbered in the prompt, so a number the model
writes maps back to a known source.

### Context window
The maximum amount of text a model can consider at once, measured in tokens.
It is why chunks must be small and why only `top_k` of them are sent: everything
does not fit, so retrieval has to choose well.

---

## Storage and search

### Embedding (also: vector)
A list of numbers representing a piece of text's *meaning*. Here, 384 of them.

The whole trick is that texts with similar meanings get similar numbers — so
similarity becomes arithmetic. "How much annual leave do I get?" and "Employees
accrue 1.75 days of paid leave per month" share no keywords at all, and their
vectors sit close together.

That is what makes this **semantic** search rather than keyword search: it finds
passages that *mean* the right thing, not ones that contain the right letters.

### Embedding model
The model that turns text into a vector. `all-MiniLM-L6-v2` by default.

**The one rule:** the question must be embedded by the same model that embedded
the chunks. Vectors from two different models are not comparable — the numbers
describe different spaces, and the distance between them is arithmetic on
unrelated quantities. Changing the provider invalidates every stored vector.

### Dimension
How many numbers are in a vector. 384 for the local model, 1536 for OpenAI's.
The database column has a fixed width, so `EMBEDDING_DIMENSION` must match the
provider or every insert fails.

### Cosine distance / similarity
How the closeness of two vectors is measured — by the *angle* between them
rather than the distance in a straight line, so the length of a vector does not
affect the result.

Distance 0 means identical in meaning; larger means less alike. This project
reports `similarity = 1 - distance`, because `0.83` reads more naturally than
`0.17`.

### pgvector
The PostgreSQL extension that adds a `vector` column type and the `<=>` distance
operator. It is why DocMinds needs no separate vector database — the embeddings
live in the same Postgres as everything else, in the same transactions.

### HNSW
*Hierarchical Navigable Small World* — the index that makes vector search fast.

Without it, finding the nearest chunks means computing the distance to **every**
stored vector, every time. HNSW builds a navigable graph that reaches the
neighbourhood of the answer in a fraction of the comparisons. It is approximate
— it can very occasionally miss a true nearest neighbour — and that trade is
what makes search scale.

### Top-k
How many chunks retrieval returns. Default 5.

Too few and the answer misses context; too many and the prompt fills with weak
matches that dilute the good ones — and cost more tokens.

### Tokenization
Splitting text into tokens, the units a model actually reads. A token is
roughly ¾ of a word. Chunk sizes are counted in tokens rather than characters
because tokens are what the context window is measured in — a character limit is
meaningless to a model.

### OCR
*Optical Character Recognition* — reading text out of an image. Needed for
scanned documents and photographs, where the "text" is only pixels.

Used as a **fallback**, never a default: a born-digital PDF already contains its
characters, and OCR would be slower *and* less accurate than reading them.

---

## Models and providers

### LLM
*Large Language Model* — the thing that writes the answer. GPT-OSS 120B via Groq
here.

### Temperature
How much randomness the model is allowed. High is for creative writing; this
project uses **0.1**, close to deterministic, because the job is to restate what
the excerpts say accurately. Invention is the failure mode, not the goal.

### Prompt
The complete text sent to the model — here, the numbered excerpts followed by
the question. In a RAG system the prompt is not configuration around the real
work; the prompt *is* the work.

### Provider
Which implementation of a capability is in use. Every external dependency here
has several, chosen by a setting:

```
OCR_PROVIDER        tesseract | easyocr | paddleocr | mock
EMBEDDING_PROVIDER  local | openai | mock
LLM_PROVIDER        groq | mock
```

### Mock provider
A fake implementation producing plausible, **deterministic** output with no API
key, no network and no model download. Set all three to `mock` and the whole
pipeline runs on a laptop.

Deterministic is the important word: the same input always gives the same
output, which is what would make these components testable.

---

## Infrastructure

### Celery
The library that runs background jobs. A **worker** is a separate process that
takes jobs off a queue and runs them — which is how a 300-page PDF can take four
minutes without an HTTP request waiting four minutes.

### Broker / result backend
Two roles, both played by Redis here:

- **Broker** — carries work *to* the worker. The queue itself.
- **Result backend** — carries answers *back*, which is what makes
  "is that task done?" answerable.

Same Redis, different jobs.

### Idempotent
Safe to run more than once, with the same end result. The ingestion task deletes
a document's existing chunks before writing new ones, so a redelivered job
produces one set of chunks rather than two. Celery *does* redeliver jobs whose
worker died, so this is a requirement rather than a nicety.

### Alembic / migration
Alembic manages schema changes. A **migration** is one ordered, reviewable
change — "add this column", "create this index" — that can be applied to a
database with data already in it.

### asyncpg / async
`async`/`await` let one process serve many requests at once. A query spends
almost all its time *waiting* for Postgres; async code hands the waiting time to
another request instead of sitting idle.

### Multi-tenant
One deployment serving several organisations whose data must never mix. Here the
tenant is the **organisation**, and `org_id` is the boundary.

It is enforced inside the SQL — the retrieval query joins chunk → document →
project and filters on `Project.org_id` — so another tenant's chunk is never a
*candidate*, rather than being filtered out afterwards. That is a materially
stronger guarantee: forgetting a post-filter leaks data, while a missing join
returns nothing.

---

## Web and API

### JWT
*JSON Web Token* — a signed blob proving who you are, sent with every request.
The server verifies the signature rather than looking anything up, which is what
makes it stateless.

Signed, **not encrypted**: anyone can read a token's contents. Never put a
secret in one.

### Access token vs refresh token
Two tokens with different jobs. The **access** token is used on every request and
expires in 30 minutes, limiting the damage if it leaks. The **refresh** token
lasts 7 days and does one thing: get a new access token.

A refresh token deliberately carries no role and no `org_id` — those are re-read
from the database at refresh time, so a revoked role is gone within half an hour
instead of living in a token for a week.

### Bcrypt
The password hashing algorithm. Deliberately **slow**, which is the feature: a
fast hash lets someone with a stolen database try billions of guesses a second.
It also salts each hash, so two users with the same password store different
values.

### CORS
The browser rule that stops a page on one origin calling an API on another
unless the API says it may. There is no dev proxy here, so the backend's
allow-list must include the frontend's URL — a CORS error means the *backend*
needs the change.

### 401 vs 403
- **401 Unauthorized** — "I do not know who you are." Signing in may help.
- **403 Forbidden** — "I know who you are, and you may not do this." Signing in
  again will not help.

Collapsing them gives you a UI that logs people out over permission problems.

### Pydantic schema
A class describing the shape of JSON in or out. Requests are validated against
one before your code runs; responses are filtered *through* one on the way out,
which is what makes leaking a password hash structurally impossible rather than
something to remember.

### 202 Accepted
"I have taken this; it is not finished." The correct answer when work has been
queued rather than done — which is why `/tasks/trigger-*` returns it.

---

## The numbers you can tune

| Setting | Default | Turn it up when | Turn it down when |
|---|---|---|---|
| `RETRIEVAL_TOP_K` | 5 | Answers miss context | Answers wander off-topic |
| `RETRIEVAL_MIN_SCORE` | 0.15 | Irrelevant sources appear | It says "I don't know" too readily |
| `DEFAULT_CHUNK_SIZE` | 500 | Answers are cut off mid-idea | Results are too vague to cite |
| `DEFAULT_CHUNK_OVERLAP` | 50 | Facts fall between chunks | Storage or duplication matters |
| `LLM_TEMPERATURE` | 0.1 | You want varied phrasing | You want it to stop embellishing |

Change one at a time, and use the **Search** tab to see the effect — it shows
exactly what retrieval returned, with no model in the way.
