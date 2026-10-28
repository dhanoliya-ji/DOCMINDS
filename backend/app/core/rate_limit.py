"""Counts how often each caller hits an endpoint, and says "no" -- 429 Too Many Requests, with a Retry-After..."""
import asyncio
import functools
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from app.core.config import parse_rate_limit, settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "docminds:ratelimit"

REDIS_RETRY_AFTER_SECONDS = 30.0
REDIS_TIMEOUT_SECONDS = 0.5

LIMITS: Dict[str, Tuple[str, str]] = {
    "login": ("RATE_LIMIT_LOGIN", "sign-in attempts from this address"),
    "signup": ("RATE_LIMIT_SIGNUP", "sign-ups from this address"),
    "password_reset": ("RATE_LIMIT_PASSWORD_RESET", "password reset requests from this address"),
    "verify_resend": ("RATE_LIMIT_VERIFY_RESEND", "verification emails"),
    "chat": ("RATE_LIMIT_CHAT", "questions"),
    "search": ("RATE_LIMIT_SEARCH", "searches"),
    "upload": ("RATE_LIMIT_UPLOAD", "uploads"),
    "share_view": ("RATE_LIMIT_SHARE_VIEW", "shared-link views from this address"),
    "compare": ("RATE_LIMIT_COMPARE", "document comparisons"),
}


def require_known(name: str) -> None:
    """Fail at IMPORT time on a typo'd limit name, never at request time."""
    if name not in LIMITS:
        raise ValueError(f"Unknown rate limit {name!r}; expected one of {sorted(LIMITS)}.")


@functools.lru_cache(maxsize=64)
def _parsed(text: str) -> Optional[Tuple[int, int]]:
    return parse_rate_limit(text)


def configured(name: str) -> Optional[Tuple[int, int]]:
    """(limit, window_seconds) for a named limit, or None when it is off."""
    require_known(name)
    if not settings.RATE_LIMIT_ENABLED:
        return None
    return _parsed(getattr(settings, LIMITS[name][0]))


@dataclass(frozen=True)
class Decision:
    """The verdict on one request."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


def weighted_count(previous: int, current: int, elapsed: float, window: int) -> float:
    """The sliding-window estimate of requests in the last `window` seconds."""
    still_in_view = max(0.0, (window - elapsed) / window)
    return previous * still_in_view + current


def seconds_until_allowed(
    previous: int, current: int, elapsed: float, window: int, limit: int
) -> float:
    """How long until ONE more request fits, if nothing else arrives meanwhile."""
    if current < limit:
        if previous <= 0:
            return 0.0
        needed = window * (1 - (limit - 1 - current) / previous)
        if needed < window:
            return max(0.0, needed - elapsed)
        return window - elapsed
    return (window - elapsed) + window * (1 - (limit - 1) / current)


class MemoryCounterStore:
    """Counters in a dictionary, for when there is no Redis."""

    name = "memory"
    _SWEEP_EVERY = 1000

    def __init__(self) -> None:
        self._counts: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._operations = 0

    def _live(self, key: str, now: float) -> int:
        count, expires_at = self._counts.get(key, (0, 0.0))
        return count if expires_at > now else 0

    async def increment(
        self, current_key: str, previous_key: str, ttl: int, now: float
    ) -> Tuple[int, int]:
        with self._lock:
            self._operations += 1
            if self._operations % self._SWEEP_EVERY == 0:
                self._counts = {k: v for k, v in self._counts.items() if v[1] > now}
            current = self._live(current_key, now) + 1
            self._counts[current_key] = (current, now + ttl)
            return current, self._live(previous_key, now)

    async def decrement(self, current_key: str, now: float) -> None:
        with self._lock:
            count, expires_at = self._counts.get(current_key, (0, 0.0))
            if count > 0 and expires_at > now:
                self._counts[current_key] = (count - 1, expires_at)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


class RedisCounterStore:
    """Counters in Redis, shared by every API process."""

    name = "redis"

    def __init__(self, client) -> None:
        self.client = client

    async def increment(
        self, current_key: str, previous_key: str, ttl: int, now: float
    ) -> Tuple[int, int]:
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.incr(current_key)
            pipe.expire(current_key, ttl)
            pipe.get(previous_key)
            current, _, previous = await pipe.execute()
        return int(current), int(previous or 0)

    async def decrement(self, current_key: str, now: float) -> None:
        await self.client.decr(current_key)


def _default_redis_client():
    """A Redis client with short timeouts. Imported lazily: memory-only"""
    import redis.asyncio as aioredis

    return aioredis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )


class RateLimiter:
    """Applies a limit to one caller, choosing Redis or memory as it goes."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        redis_factory: Callable[[], object] = _default_redis_client,
    ) -> None:
        self.clock = clock
        self.memory = MemoryCounterStore()
        self._redis_factory = redis_factory
        self._redis: Optional[Tuple[asyncio.AbstractEventLoop, RedisCounterStore]] = None
        self._redis_down_until = 0.0

    def reset(self) -> None:
        """Forget every in-memory count (tests), and try Redis afresh."""
        self.memory.reset()
        self._redis = None
        self._redis_down_until = 0.0

    def _redis_store(self) -> Optional[RedisCounterStore]:
        if settings.RATE_LIMIT_STORAGE != "auto":
            return None
        if time.monotonic() < self._redis_down_until:
            return None
        loop = asyncio.get_running_loop()
        if self._redis is None or self._redis[0] is not loop:
            try:
                self._redis = (loop, RedisCounterStore(self._redis_factory()))
            except Exception as exc:
                self._redis_failed(exc)
                return None
        return self._redis[1]

    def _redis_failed(self, exc: BaseException) -> None:
        logger.warning(
            "Rate limiter cannot reach Redis; counting in memory for %ss",
            int(REDIS_RETRY_AFTER_SECONDS),
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        self._redis = None
        self._redis_down_until = time.monotonic() + REDIS_RETRY_AFTER_SECONDS

    async def hit(self, name: str, identity: str, limit: int, window: int) -> Decision:
        """Count one request by `identity` against `limit` per `window` seconds."""
        now = self.clock()
        index = int(now // window)
        elapsed = now - index * window
        base = f"{KEY_PREFIX}:{name}:{window}:{identity}"
        current_key, previous_key = f"{base}:{index}", f"{base}:{index - 1}"
        ttl = 2 * window

        store = self._redis_store() or self.memory
        try:
            current, previous = await store.increment(current_key, previous_key, ttl, now)
        except Exception as exc:
            if store is self.memory:
                raise
            self._redis_failed(exc)
            store = self.memory
            current, previous = await store.increment(current_key, previous_key, ttl, now)

        estimate = weighted_count(previous, current, elapsed, window)
        if estimate <= limit:
            return Decision(True, limit, max(0, int(limit - estimate)), 0)

        try:
            await store.decrement(current_key, now)
        except Exception:
            pass
        wait = seconds_until_allowed(previous, current - 1, elapsed, window, limit)
        return Decision(False, limit, 0, max(1, math.ceil(wait)))


limiter = RateLimiter()


async def check(name: str, identity: str) -> Optional[Decision]:
    """Count one request against the named limit."""
    rate = configured(name)
    if rate is None:
        return None
    limit, window = rate
    return await limiter.hit(name, identity, limit, window)


def _human_wait(seconds: int) -> str:
    """42 -> "42 seconds", 150 -> "3 minutes", 7200 -> "2 hours"."""
    if seconds < 90:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    if seconds < 90 * 60:
        minutes = math.ceil(seconds / 60)
        return f"{minutes} minutes"
    hours = math.ceil(seconds / 3600)
    return f"{hours} hours"


def refusal_message(name: str, retry_after: int) -> str:
    """The `detail` of a 429: what was limited and how long to wait."""
    return f"Too many {LIMITS[name][1]}. Please try again in {_human_wait(retry_after)}."
