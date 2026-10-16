"""
embedding.py
============
WHAT THIS FILE DOES
-------------------
It turns TEXT into NUMBERS. That is the whole job.

    "annual leave policy"  ->  [0.021, -0.184, 0.377, ... 384 numbers total]

That list of numbers is called an EMBEDDING (or a "vector").

WHY WOULD ANYONE WANT THAT?
---------------------------
Because computers cannot compare meanings, but they CAN compare numbers.

The model that produces these numbers was trained so that text with a similar
MEANING lands in a similar place. Picture every possible sentence as a dot in
space: "how many holidays do I get" and "annual leave entitlement" end up as
two dots sitting right next to each other, even though they share zero words.
"chocolate cake recipe" lands far away from both.

So to find the passage that answers a question, we:
   1. convert the question into a vector,
   2. convert every paragraph of every document into a vector (done once, at
      upload time),
   3. find whichever paragraph vectors sit closest to the question vector.

That is semantic search, and it is step "R" (Retrieval) of RAG.

WHERE THIS FITS IN THE PIPELINE
-------------------------------
Called from TWO places, and this is the key thing to understand:

  * app/tasks/ingestion.py -- when a document is uploaded. Every chunk gets
    embedded and the vectors are saved into Postgres. Happens ONCE per chunk.

  * app/services/retrieval.py -- when a user asks a question. The QUESTION is
    embedded and compared against the saved chunk vectors. Happens on every
    search.

Both sides MUST use the same model. Vectors from two different models are not
comparable at all -- it would be like measuring one in centimetres and the
other in pounds. That is why the model name is stored alongside every vector.

THE PROVIDER PATTERN
--------------------
Rather than hard-wiring one embedding company into the codebase, this file
exposes ONE function, `get_embeddings()`, and internally routes to whichever
provider `settings.EMBEDDING_PROVIDER` names. Swapping OpenAI for a local model
is then a one-line change in .env, and no other file in the project notices.
"""

import hashlib
import math
import random
import threading
import time
from typing import List

from app.core.config import settings

# ----------------------------------------------------------------------
# MODULE-LEVEL CACHE for the local AI model.
# ----------------------------------------------------------------------
# Loading a sentence-transformers model means reading ~90 MB from disk and
# building the neural network in memory. That takes several seconds. We must
# NOT do it once per request -- the app would be unusably slow.
#
# So we load it the first time it is needed and keep it in this variable for
# the lifetime of the process. This is called a "singleton" or "lazy cache".
_local_model = None

# A lock guards the loading step. Without it, two web requests arriving at the
# exact same moment could BOTH see `_local_model is None` and both start
# loading the model, wasting memory and time. The lock ensures only the first
# one loads while the second waits and then reuses the result.
_local_model_lock = threading.Lock()


class EmbeddingService:
    """
    A namespace holding every embedding-related function.

    These are all `@classmethod`s rather than instance methods because the
    service holds no per-instance state -- you never need to write
    `EmbeddingService()`. You just call `EmbeddingService.get_embeddings(...)`.
    """

    # ==================================================================
    # PUBLIC ENTRY POINTS -- the rest of the codebase only calls these two
    # ==================================================================

    @classmethod
    def get_embeddings(cls, texts: List[str], *, for_query: bool = False) -> List[List[float]]:
        """
        Convert a LIST of texts into a LIST of vectors, all in one go.

        Input : ["first chunk of text", "second chunk of text"]
        Output: [[0.1, 0.2, ...], [0.4, -0.1, ...]]

        The order is guaranteed: output[0] is the vector for input[0]. The
        ingestion task relies on that to pair each vector back to its chunk.

        We accept a list rather than a single string because embedding 32 texts
        in one call is dramatically faster than 32 separate calls -- the model
        processes them as one batch on the CPU/GPU, and for API providers it is
        one network round-trip instead of 32.

        `for_query` says the text is a user's question rather than document
        content. Only the Gemini provider uses it (it embeds questions and
        documents slightly differently, which improves retrieval); the others
        treat both the same.
        """
        # Guard clause: an empty input gives an empty output. Without this,
        # some providers would raise an error on a zero-length batch.
        if not texts:
            return []

        # .lower() makes the setting forgiving: "Local", "LOCAL" and "local"
        # all work, so a typo in .env capitalisation does not break the app.
        provider = settings.EMBEDDING_PROVIDER.lower()

        if provider == "local":
            return cls._get_local_embeddings(texts)
        elif provider == "openai":
            return cls._get_openai_embeddings(texts)
        elif provider == "gemini":
            return cls._get_gemini_embeddings(texts, for_query=for_query)
        elif provider == "mock":
            return cls._get_mock_embeddings(texts)
        else:
            # An unknown provider name is a configuration mistake. We warn
            # loudly but fall back to mock so the app still starts, which makes
            # the problem much easier to diagnose than a hard crash on boot.
            print(
                f"[Embedding] Unknown provider '{provider}'. "
                f"Falling back to mock embeddings."
            )
            return cls._get_mock_embeddings(texts)

    @classmethod
    def get_embedding(cls, text: str) -> List[float]:
        """
        Convenience wrapper for embedding exactly ONE text.

        Used when a user asks a question: there is only one question, so
        wrapping it in a one-item list and unwrapping the result keeps the
        calling code in retrieval.py readable.
        """
        results = cls.get_embeddings([text], for_query=True)
        # `results[0] if results else []` guards against the (unlikely) case of
        # a provider returning nothing, so we return an empty list rather than
        # raising an IndexError.
        return results[0] if results else []

    # ==================================================================
    # PROVIDER 1: LOCAL MODEL (the default -- free, no API key needed)
    # ==================================================================

    @classmethod
    def _load_local_model(cls):
        """
        Load the sentence-transformers model into memory, exactly once.

        The leading underscore in the name is a Python convention meaning
        "internal -- do not call this from outside this file".
        """
        # `global` tells Python we intend to REASSIGN the module-level variable
        # rather than create a new local variable that shadows it.
        global _local_model

        # First check WITHOUT taking the lock. Once the model is loaded (the
        # overwhelmingly common case) this returns immediately with no locking
        # overhead at all.
        if _local_model is not None:
            return _local_model

        # Only the very first callers reach here and contend for the lock.
        with _local_model_lock:
            # Check AGAIN, now that we hold the lock. Another thread may have
            # finished loading while we were queued waiting for it. This
            # check-lock-check sequence is the standard "double-checked
            # locking" pattern.
            if _local_model is None:
                # The import lives INSIDE the function on purpose. Importing
                # sentence_transformers pulls in PyTorch, which is heavy and
                # slow. Doing it here means a deployment configured to use
                # OpenAI or mock never pays that cost at all.
                from sentence_transformers import SentenceTransformer

                print(
                    f"[Embedding] Loading local model "
                    f"'{settings.LOCAL_EMBEDDING_MODEL}' (first use only, "
                    f"this can take ~10s)..."
                )
                # On the very first run this downloads the model from
                # HuggingFace and caches it on disk (~/.cache/huggingface).
                # Every later start reads from that cache and is much faster.
                _local_model = SentenceTransformer(settings.LOCAL_EMBEDDING_MODEL)
                print("[Embedding] Local model ready.")

        return _local_model

    @classmethod
    def _get_local_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings using a model running on THIS server.

        No API key, no network call, no per-request cost, and the document text
        never leaves the machine -- which genuinely matters for the
        "enterprise documents" use case this project is built around.
        """
        try:
            model = cls._load_local_model()

            embeddings = model.encode(
                texts,
                # Process in batches rather than all at once, to bound memory.
                batch_size=settings.EMBEDDING_BATCH_SIZE,
                # Scale every vector to length 1. This is important: once all
                # vectors have the same length, "angle between them" and
                # "distance between them" rank results identically, so cosine
                # similarity becomes a simple dot product. Postgres then gives
                # us clean 0..1 similarity scores.
                normalize_embeddings=True,
                # We are embedding many chunks at once; a progress bar in the
                # server log would just be noise.
                show_progress_bar=False,
                # The model natively returns NumPy arrays.
                convert_to_numpy=True,
            )

            # Postgres and JSON cannot store NumPy types, so convert to plain
            # Python lists of floats before handing the result onward.
            return embeddings.tolist()

        except ImportError:
            # Raised when the `sentence-transformers` package is not installed.
            print(
                "[Embedding] sentence-transformers is not installed. "
                "Run: pip install sentence-transformers. Using mock vectors."
            )
            return cls._get_mock_embeddings(texts)
        except Exception as exc:
            # Any other failure (out of memory, corrupted model cache, no
            # internet on the very first download) also degrades to mock rather
            # than taking the entire ingestion pipeline down.
            print(f"[Embedding] Local embedding failed: {exc}. Using mock vectors.")
            return cls._get_mock_embeddings(texts)

    # ==================================================================
    # PROVIDER 2: OPENAI (optional, paid)
    # ==================================================================

    @classmethod
    def _get_openai_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings by calling OpenAI's hosted API over the network.

        Higher quality than the small local model, but it costs money, requires
        internet, and sends your document text to a third party.
        """
        try:
            # Fail fast and clearly if the key was never configured.
            if not settings.OPENAI_API_KEY:
                print(
                    "[Embedding] OPENAI_API_KEY is missing from .env. "
                    "Using mock vectors."
                )
                return cls._get_mock_embeddings(texts)

            # Imported inside the function so the package is only required by
            # deployments that actually choose this provider.
            from openai import OpenAI

            client = OpenAI(api_key=settings.OPENAI_API_KEY)

            response = client.embeddings.create(
                input=texts,
                model=settings.OPENAI_EMBEDDING_MODEL,
            )

            # OpenAI returns the results in the same order they were sent, so a
            # straightforward list comprehension preserves the pairing.
            return [item.embedding for item in response.data]

        except Exception as exc:
            print(f"[Embedding] OpenAI API call failed: {exc}. Using mock vectors.")
            return cls._get_mock_embeddings(texts)

    # ==================================================================
    # PROVIDER: GEMINI (Google's hosted API -- free tier)
    # ==================================================================

    # batchEmbedContents takes at most 100 texts per call.
    _GEMINI_BATCH = 100
    # Tests swap in an httpx.MockTransport here; None means the real network.
    _gemini_transport = None

    @classmethod
    def _get_gemini_embeddings(cls, texts: List[str], *, for_query: bool = False) -> List[List[float]]:
        """
        Embed through Google's Gemini API, which has a free tier.

        This is what lets the live demo run on a 512 MB free host: there is no
        PyTorch model in memory, just an HTTPS call.

        Unlike the other providers, a failure here RAISES instead of falling
        back to mock vectors. Mock vectors would be stored as if they were
        real, and search would quietly return nothing; a raised error marks
        the document "failed" and says why.
        """
        import httpx

        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER is 'gemini' but GEMINI_API_KEY is empty. "
                "Get a free key at https://aistudio.google.com/apikey"
            )

        model = settings.GEMINI_EMBEDDING_MODEL
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:batchEmbedContents"
        )
        # Questions and passages are embedded differently on purpose: the
        # model places a question near the passages that ANSWER it.
        task_type = "RETRIEVAL_QUERY" if for_query else "RETRIEVAL_DOCUMENT"

        vectors: List[List[float]] = []
        with httpx.Client(timeout=60, transport=cls._gemini_transport) as client:
            for start in range(0, len(texts), cls._GEMINI_BATCH):
                batch = texts[start:start + cls._GEMINI_BATCH]
                body = {
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": task_type,
                            "outputDimensionality": settings.EMBEDDING_DIMENSION,
                        }
                        for text in batch
                    ]
                }
                data = cls._post_gemini(client, url, body)
                embeddings = data.get("embeddings", [])
                if len(embeddings) != len(batch):
                    raise RuntimeError(
                        f"Gemini returned {len(embeddings)} embeddings for {len(batch)} texts."
                    )
                # Below the full 3072 numbers, Gemini's vectors are not unit
                # length; its docs say to normalise them. Cosine distance would
                # cope either way, but every other provider stores unit vectors.
                vectors.extend(cls._unit(item["values"]) for item in embeddings)
        return vectors

    @classmethod
    def _post_gemini(cls, client, url: str, body: dict) -> dict:
        """POST, retrying the free tier's rate limits (429) and blips (5xx)."""
        delay = 2.0
        for attempt in range(5):
            response = client.post(
                url, json=body, headers={"x-goog-api-key": settings.GEMINI_API_KEY}
            )
            if response.status_code < 400:
                return response.json()
            if response.status_code != 429 and response.status_code < 500:
                # 400/401/403: a bad key or request. Retrying cannot fix it.
                raise RuntimeError(
                    f"Gemini embeddings failed (HTTP {response.status_code}): {response.text[:300]}"
                )
            if attempt < 4:
                time.sleep(float(response.headers.get("retry-after", delay)))
                delay *= 2
        raise RuntimeError(
            f"Gemini embeddings still failing after retries (HTTP {response.status_code})."
        )

    @staticmethod
    def _unit(values: List[float]) -> List[float]:
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    # ==================================================================
    # PROVIDER 3: MOCK (for tests, CI, and offline development)
    # ==================================================================

    @classmethod
    def _get_mock_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """
        Produce fake vectors that are nonetheless DETERMINISTIC.

        "Deterministic" means the same input text always yields the exact same
        vector. That is what makes these usable in automated tests: the test
        can assert that searching for a chunk's own text retrieves that chunk,
        with no API key, no network and no model download.

        These vectors carry no real meaning, so semantic search results will be
        nonsense. Never enable this provider in production.
        """
        dimension = settings.EMBEDDING_DIMENSION
        results = []

        for text in texts:
            # Hash the text to get a number that is always the same for that
            # text, and use it to seed the random generator. Same text -> same
            # seed -> same "random" sequence -> same vector, every time.
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            seed = int(digest, 16) % (2**32)

            # A private Random instance, so seeding it cannot disturb any other
            # part of the program that happens to use the global `random`.
            rng = random.Random(seed)
            vector = [rng.uniform(-1, 1) for _ in range(dimension)]

            # Normalise to length 1, matching what the real providers do, so
            # similarity scores stay on the same 0..1 scale in tests.
            # Length of a vector = square root of the sum of its squares.
            magnitude = sum(value * value for value in vector) ** 0.5
            if magnitude > 0:
                vector = [value / magnitude for value in vector]

            results.append(vector)

        return results

    # ==================================================================
    # HELPER used when saving vectors, so we always record WHICH model made them
    # ==================================================================

    @classmethod
    def current_model_name(cls) -> str:
        """
        Return a human-readable name for the model currently in use.

        This gets stored in the `model_name` column next to every vector. It
        matters because vectors from different models are NOT comparable. If
        you later switch models, this column tells you exactly which rows are
        stale and need re-embedding -- without it you would have silently
        broken search and no way to tell which data was affected.
        """
        provider = settings.EMBEDDING_PROVIDER.lower()
        if provider == "local":
            return settings.LOCAL_EMBEDDING_MODEL
        elif provider == "openai":
            return settings.OPENAI_EMBEDDING_MODEL
        elif provider == "gemini":
            # The size is part of the name: the same model at a different
            # size produces vectors that are not comparable.
            return f"{settings.GEMINI_EMBEDDING_MODEL}@{settings.EMBEDDING_DIMENSION}"
        return f"mock-embedding-{settings.EMBEDDING_DIMENSION}"
