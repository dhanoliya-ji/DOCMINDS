"""It turns TEXT into NUMBERS."""
import hashlib
import json
import logging
import math
import random
import re
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Callable, List, Optional, Tuple

from app.core.config import settings
from app.services.pacing import RatePacer

logger = logging.getLogger(__name__)

_local_model = None

_local_model_lock = threading.Lock()


gemini_pacer = RatePacer()


class EmbeddingRateLimited(Exception):
    """The provider is still refusing us after the whole retry budget."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


wait_listener: "ContextVar[Optional[Callable[[float, str], None]]]" = ContextVar(
    "embedding_wait_listener", default=None
)


def _now() -> float:
    """The retry loop's clock. A function, so tests can swap in a fake one."""
    return time.monotonic()


def _sleep(seconds: float) -> None:
    """The retry loop's sleep (see `_now`)."""
    time.sleep(seconds)


def gemini_retry_hint(response) -> Optional[float]:
    """How long Google asked us to wait, in seconds -- or None if it did not say."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass  # an HTTP-date; rare enough to fall back to backoff
    try:
        details = (response.json().get("error") or {}).get("details") or []
    except Exception:
        return None
    for detail in details:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"\s*([\d.]+)s\s*", str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


class EmbeddingService:
    """A namespace holding every embedding-related function."""


    @classmethod
    def get_embeddings(cls, texts: List[str], *, for_query: bool = False) -> List[List[float]]:
        """Convert a LIST of texts into a LIST of vectors, all in one go."""
        if not texts:
            return []

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
            logger.warning(
                f"Unknown provider '{provider}'. "
                f"Falling back to mock embeddings."
            )
            return cls._get_mock_embeddings(texts)

    @classmethod
    def get_embedding(cls, text: str) -> List[float]:
        """Convenience wrapper for embedding exactly ONE text."""
        cache_key = None
        if settings.EMBEDDING_CACHE_ENABLED:
            cache_key = query_cache.key(cls.current_model_name(), text)
            cached = query_cache.get(cache_key)
            if cached is not None:
                return cached

        results = cls.get_embeddings([text], for_query=True)
        vector = results[0] if results else []

        if cache_key is not None and vector:
            query_cache.set(cache_key, vector)
        return vector


    @classmethod
    def _load_local_model(cls):
        """Load the sentence-transformers model into memory, exactly once."""
        global _local_model

        if _local_model is not None:
            return _local_model

        with _local_model_lock:
            if _local_model is None:
                from sentence_transformers import SentenceTransformer

                logger.info(
                    f"Loading local model "
                    f"'{settings.local_embedding_model}' (first use only, "
                    f"this can take ~10s)..."
                )
                _local_model = SentenceTransformer(settings.local_embedding_model)
                logger.info("Local model ready.")

        return _local_model

    @classmethod
    def _get_local_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using a model running on THIS server."""
        try:
            model = cls._load_local_model()

            embeddings = model.encode(
                texts,
                batch_size=settings.EMBEDDING_BATCH_SIZE,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

            return embeddings.tolist()

        except ImportError:
            logger.warning(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers. Using mock vectors."
            )
            return cls._get_mock_embeddings(texts)
        except Exception as exc:
            logger.warning(f"Local embedding failed: {exc}. Using mock vectors.")
            return cls._get_mock_embeddings(texts)


    @classmethod
    def _get_openai_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """Generate embeddings by calling OpenAI's hosted API over the network."""
        try:
            if not settings.OPENAI_API_KEY:
                logger.warning(
                    "OPENAI_API_KEY is missing from .env. "
                    "Using mock vectors."
                )
                return cls._get_mock_embeddings(texts)

            from openai import OpenAI

            client = OpenAI(api_key=settings.OPENAI_API_KEY)

            response = client.embeddings.create(
                input=texts,
                model=settings.OPENAI_EMBEDDING_MODEL,
            )

            return [item.embedding for item in response.data]

        except Exception as exc:
            logger.warning(f"OpenAI API call failed: {exc}. Using mock vectors.")
            return cls._get_mock_embeddings(texts)


    _GEMINI_BATCH = 100
    _GEMINI_MAX_INPUT_TOKENS = 2048
    _TOKEN_SAFETY = 1.15
    _gemini_transport = None

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        """Roughly how many tokens of Gemini's quota this text will use."""
        from app.services.chunker import ChunkerService

        counted = min(ChunkerService.count_tokens(text), cls._GEMINI_MAX_INPUT_TOKENS)
        return max(1, int(counted * cls._TOKEN_SAFETY))

    @classmethod
    def _gemini_batches(cls, texts: List[str]) -> List[Tuple[int, int, int]]:
        """Split `texts` into (start, end, tokens) batches that each fit the quota."""
        rpm = settings.GEMINI_EMBED_REQUESTS_PER_MINUTE
        max_texts = max(1, min(cls._GEMINI_BATCH, rpm if rpm > 0 else cls._GEMINI_BATCH))
        tpm = settings.GEMINI_EMBED_TOKENS_PER_MINUTE
        max_tokens = max(1, tpm // 2) if tpm > 0 else None

        batches: List[Tuple[int, int, int]] = []
        start, tokens = 0, 0
        for index, text in enumerate(texts):
            cost = cls._estimate_tokens(text)
            full = index - start >= max_texts or (
                max_tokens is not None and tokens + cost > max_tokens and index > start
            )
            if full:
                batches.append((start, index, tokens))
                start, tokens = index, 0
            tokens += cost
        if start < len(texts):
            batches.append((start, len(texts), tokens))
        return batches

    @classmethod
    def _get_gemini_embeddings(cls, texts: List[str], *, for_query: bool = False) -> List[List[float]]:
        """Embed through Google's Gemini API, which has a free tier."""
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
        task_type = "RETRIEVAL_QUERY" if for_query else "RETRIEVAL_DOCUMENT"
        budget = (
            settings.GEMINI_QUERY_RETRY_BUDGET_SECONDS if for_query else settings.GEMINI_RETRY_BUDGET_SECONDS
        )

        vectors: List[List[float]] = []
        with httpx.Client(timeout=60, transport=cls._gemini_transport) as client:
            for start, end, tokens in cls._gemini_batches(texts):
                batch = texts[start:end]
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
                data = cls._post_gemini(
                    client, url, body, texts=len(batch), tokens=tokens,
                    budget_seconds=budget, for_query=for_query,
                )
                embeddings = data.get("embeddings", [])
                if len(embeddings) != len(batch):
                    raise RuntimeError(
                        f"Gemini returned {len(embeddings)} embeddings for {len(batch)} texts."
                    )
                vectors.extend(cls._unit(item["values"]) for item in embeddings)
        return vectors

    @classmethod
    def _post_gemini(
        cls,
        client,
        url: str,
        body: dict,
        *,
        texts: int = 1,
        tokens: int = 1,
        budget_seconds: Optional[float] = None,
        for_query: bool = False,
    ) -> dict:
        """POST one batch: paced, and patient with rate limits (429) and blips."""
        import httpx

        if budget_seconds is None:
            budget_seconds = settings.GEMINI_RETRY_BUDGET_SECONDS
        cap = float(settings.GEMINI_RETRY_MAX_DELAY_SECONDS)
        started = _now()
        listener = wait_listener.get()
        attempt = 0

        def notify(seconds: float, reason: str) -> None:
            if listener is not None:
                try:
                    listener(seconds, reason)
                except Exception:  # pragma: no cover - status updates are best effort
                    logger.debug("embedding wait listener failed", exc_info=True)

        while True:
            gemini_pacer.acquire(
                texts,
                tokens,
                settings.GEMINI_EMBED_REQUESTS_PER_MINUTE,
                settings.GEMINI_EMBED_TOKENS_PER_MINUTE,
                block=not for_query,
                on_wait=lambda seconds: notify(seconds, "pacing"),
            )
            hint: Optional[float] = None
            try:
                response = client.post(
                    url, json=body, headers={"x-goog-api-key": settings.GEMINI_API_KEY}
                )
            except httpx.TransportError as exc:
                what = f"network error: {exc.__class__.__name__}"
                status_code = None
            else:
                status_code = response.status_code
                if status_code < 400:
                    return response.json()
                if status_code != 429 and status_code < 500:
                    raise RuntimeError(
                        f"Gemini embeddings failed (HTTP {status_code}): {response.text[:300]}"
                    )
                hint = gemini_retry_hint(response)
                what = f"HTTP {status_code}"

            if hint is not None and hint > cap:
                raise EmbeddingRateLimited(
                    f"Gemini quota exhausted ({what}); Google asks to retry in {int(hint)}s.",
                    retry_after=hint,
                )
            backoff = min(cap, 2.0 * 2**attempt) * random.uniform(0.5, 1.0)
            delay = min(cap, max(hint or 0.0, backoff))
            spent = _now() - started
            if spent + delay > budget_seconds:
                raise EmbeddingRateLimited(
                    f"Gemini embeddings still rate limited after {int(spent)}s of retries ({what}).",
                    retry_after=delay,
                )
            if status_code == 429:
                gemini_pacer.cool_down(delay)
            logger.info("Gemini %s; retrying in %.1fs (attempt %d)", what, delay, attempt + 1)
            notify(delay, "rate_limit" if status_code == 429 else "retry")
            _sleep(delay)
            attempt += 1

    @staticmethod
    def _unit(values: List[float]) -> List[float]:
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


    @classmethod
    def _get_mock_embeddings(cls, texts: List[str]) -> List[List[float]]:
        """Produce fake vectors that are nonetheless DETERMINISTIC."""
        dimension = settings.EMBEDDING_DIMENSION
        results = []

        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            seed = int(digest, 16) % (2**32)

            rng = random.Random(seed)
            vector = [rng.uniform(-1, 1) for _ in range(dimension)]

            magnitude = sum(value * value for value in vector) ** 0.5
            if magnitude > 0:
                vector = [value / magnitude for value in vector]

            results.append(vector)

        return results


    @classmethod
    def current_model_name(cls) -> str:
        """Return a human-readable name for the model currently in use."""
        provider = settings.EMBEDDING_PROVIDER.lower()
        if provider == "local":
            return settings.local_embedding_model
        elif provider == "openai":
            return settings.OPENAI_EMBEDDING_MODEL
        elif provider == "gemini":
            return f"{settings.GEMINI_EMBEDDING_MODEL}@{settings.EMBEDDING_DIMENSION}"
        return f"mock-embedding-{settings.EMBEDDING_DIMENSION}"


class QueryEmbeddingCache:
    """A small TTL + LRU cache for question vectors, Redis-backed when possible."""

    _REDIS_RETRY_SECONDS = 30.0
    _PREFIX = "docminds:qemb:"

    def __init__(self) -> None:
        self._memory: "OrderedDict[str, Tuple[float, List[float]]]" = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None
        self._redis_down_until = 0.0
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(model_name: str, text: str) -> str:
        digest = hashlib.sha256(f"{model_name}\x00{text}".encode("utf-8")).hexdigest()
        return QueryEmbeddingCache._PREFIX + digest

    def _redis_client(self):
        """The Redis client, or None when Redis is not in use right now."""
        if settings.EMBEDDING_CACHE_BACKEND != "auto":
            return None
        if time.monotonic() < self._redis_down_until:
            return None
        if self._redis is None:
            try:
                import redis  # already a dependency, for Celery

                self._redis = redis.Redis.from_url(
                    settings.REDIS_URL, socket_timeout=0.25, socket_connect_timeout=0.25
                )
            except Exception as exc:  # pragma: no cover - import/config failure
                self._mark_redis_down(exc)
                return None
        return self._redis

    def _mark_redis_down(self, exc: Exception) -> None:
        logger.info("Query-embedding cache: Redis unavailable, using memory (%s)", exc)
        self._redis = None
        self._redis_down_until = time.monotonic() + self._REDIS_RETRY_SECONDS

    def get(self, key: str) -> Optional[List[float]]:
        client = self._redis_client()
        if client is not None:
            try:
                raw = client.get(key)
                if raw is not None:
                    self.hits += 1
                    return json.loads(raw)
            except Exception as exc:
                self._mark_redis_down(exc)

        with self._lock:
            entry = self._memory.get(key)
            if entry is not None:
                expires_at, vector = entry
                if expires_at > time.monotonic():
                    self._memory.move_to_end(key)
                    self.hits += 1
                    return vector
                del self._memory[key]
        self.misses += 1
        return None

    def set(self, key: str, vector: List[float]) -> None:
        ttl = max(1, settings.EMBEDDING_CACHE_TTL_SECONDS)
        client = self._redis_client()
        if client is not None:
            try:
                client.set(key, json.dumps(vector), ex=ttl)
                return
            except Exception as exc:
                self._mark_redis_down(exc)

        with self._lock:
            self._memory[key] = (time.monotonic() + ttl, list(vector))
            self._memory.move_to_end(key)
            while len(self._memory) > max(1, settings.EMBEDDING_CACHE_MAX_ENTRIES):
                self._memory.popitem(last=False)

    def clear(self) -> None:
        """Empty the in-memory store (tests; Redis entries simply expire)."""
        with self._lock:
            self._memory.clear()
        self.hits = 0
        self.misses = 0


query_cache = QueryEmbeddingCache()
