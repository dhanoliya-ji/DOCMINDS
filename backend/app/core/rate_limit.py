"""
rate_limit.py
=============
WHAT THIS FILE DOES
-------------------
Counts how often each caller hits an endpoint, and says "no" -- 429 Too Many
Requests, with a Retry-After header -- once they go over the limit.

    sign-in, sign-up, password reset   counted per IP ADDRESS
    chat, search, upload               counted per USER

WHY EACH LIMIT EXISTS
---------------------
    login           Password guessing. 20 tries a minute is plenty for a person
                    who mistyped; it is hopeless for a script working through a
                    dictionary against a 10-character password.
    signup          Every sign-up creates an organization. Without a limit, a
                    script can create thousands.
    password reset  Each request SENDS AN EMAIL -- otherwise a free way to
                    flood somebody's inbox from our mail server.
    chat            Every question costs model tokens, which cost money and
                    count against the provider's own rate limit for EVERYONE.
    search, upload  Embedding, OCR and extraction cost CPU that other users
                    are waiting on.

The limits themselves are settings (RATE_LIMIT_* in core/config.py), so an
operator can loosen or tighten them without touching code.

THE ALGORITHM: A SLIDING WINDOW COUNTER
---------------------------------------
The simplest limiter counts requests per clock minute ("fixed window") and
resets at :00. Its flaw is the boundary: 20 requests at 12:00:59 and 20 more
at 12:01:00 are 40 requests in two seconds, all allowed.

A sliding window fixes that cheaply. Keep TWO counters -- this window and the
previous one -- and estimate the last full window's worth of requests by
assuming the previous window's requests were spread evenly across it:

        estimate = previous x (fraction of the previous window still in view)
                 + current

    window = 60s, limit = 20, now = 15s into the current minute
    previous minute: 20 requests, current minute so far: 4
    estimate = 20 x (45/60) + 4 = 19      -> this request (the 20th) is allowed

Two integers per caller per limit, one round trip to Redis, and no boundary
burst. (Cloudflare uses the same scheme for its edge rate limiting.)

WHERE THE COUNTERS LIVE
-----------------------
    Redis      when reachable. Every API process shares the same counters, so
               the limit holds however many processes or machines serve the API.
    in memory  otherwise (no Redis, as in the free-tier demo, or Redis down).
               Correct for one process; with several, each counts on its own.

If Redis stops answering, the limiter does not fail the request -- a broken
rate limiter must not take the API down -- it counts in memory instead, logs
the switch once, and tries Redis again 30 seconds later.

A request that is REFUSED is not counted (its increment is undone). So a
client that ignores Retry-After and keeps hammering is still let through at
exactly the configured rate, never faster -- and one that waits as told is
not punished for having asked.
"""

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

# Every key this module writes starts with this, so they can never collide
# with Celery's keys in the same Redis database (and are easy to find).
KEY_PREFIX = "docminds:ratelimit"

# How long to stop trying Redis after it failed, and how long one Redis call
# may take. Short on purpose: these are paid on the request path.
REDIS_RETRY_AFTER_SECONDS = 30.0
REDIS_TIMEOUT_SECONDS = 0.5

# The limits that exist, the setting that configures each, and how the 429
# message names what was limited.
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


# ======================================================================
# THE ARITHMETIC (pure functions -- see the module docstring)
# ======================================================================


@dataclass(frozen=True)
class Decision:
    """The verdict on one request."""

    allowed: bool
    limit: int
    # Requests left in the current window, as the estimate stands now.
    remaining: int
    # Whole seconds until a request would be allowed; 0 when allowed now.
    retry_after: int


def weighted_count(previous: int, current: int, elapsed: float, window: int) -> float:
    """The sliding-window estimate of requests in the last `window` seconds."""
    still_in_view = max(0.0, (window - elapsed) / window)
    return previous * still_in_view + current


def seconds_until_allowed(
    previous: int, current: int, elapsed: float, window: int, limit: int
) -> float:
    """
    How long until ONE more request fits, if nothing else arrives meanwhile.

    `current` counts admitted requests only (a refused one is undone).
    Two cases:

      * The current window still has room (current < limit): wait until the
        previous window's share has faded enough --
            previous x (window - t) / window + current + 1 <= limit
        solved for t. If that point is past the end of this window, the next
        window starts with previous' = current <= limit - 1, which fits at once.

      * The current window is full (current >= limit): wait for the next
        window, and then until this window's share has faded --
            current x (window - t) / window + 1 <= limit.
    """
    if current < limit:
        if previous <= 0:
            return 0.0
        needed = window * (1 - (limit - 1 - current) / previous)
        if needed < window:
            return max(0.0, needed - elapsed)
        return window - elapsed
    return (window - elapsed) + window * (1 - (limit - 1) / current)


# ======================================================================
# THE COUNTER STORES
# ======================================================================


class MemoryCounterStore:
    """
    Counters in a dictionary, for when there is no Redis.

    Each entry is (count, expires_at). Expired entries are swept out every
    so often, so a server that has seen a million distinct IPs does not keep
    a million counters forever.
    """

    name = "memory"
    _SWEEP_EVERY = 1000

    def __init__(self) -> None:
        self._counts: Dict[str, Tuple[int, float]] = {}
        # The event loop runs one coroutine at a time, but a lock costs
        # nothing and keeps this correct if it is ever called from threads.
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
    """
    Counters in Redis, shared by every API process.

    INCR is atomic, so two processes counting the same caller at the same
    instant both get the right number. The three commands go in one
    pipeline: one network round trip per request, not three. Each key
    expires after two windows -- by then it is not even the "previous"
    window any more -- so Redis cleans up after us.
    """

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
    """A Redis client with short timeouts. Imported lazily: memory-only
    deployments never load it."""
    import redis.asyncio as aioredis

    return aioredis.Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )


# ======================================================================
# THE LIMITER
# ======================================================================


class RateLimiter:
    """
    Applies a limit to one caller, choosing Redis or memory as it goes.

    `clock` and `redis_factory` exist for the tests: a fake clock makes the
    window arithmetic deterministic, and a fake Redis exercises the shared
    path without a server.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        redis_factory: Callable[[], object] = _default_redis_client,
    ) -> None:
        self.clock = clock
        self.memory = MemoryCounterStore()
        self._redis_factory = redis_factory
        # (event loop, store): a Redis client belongs to the event loop it
        # was created on, so a different loop (tests run one per test) gets
        # a client of its own.
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
        # Logged on the transition only. While Redis stays down, requests
        # use memory without trying -- and without a log line each.
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
        # The window length is part of the key, so changing a limit from
        # "/minute" to "/hour" starts fresh counters instead of misreading old ones.
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

        # Refused: undo this request's increment, so only admitted requests
        # are ever counted. If even that fails, the count is one high for one
        # window -- harmless.
        try:
            await store.decrement(current_key, now)
        except Exception:
            pass
        wait = seconds_until_allowed(previous, current - 1, elapsed, window, limit)
        return Decision(False, limit, 0, max(1, math.ceil(wait)))


# The one limiter the API uses.
limiter = RateLimiter()


async def check(name: str, identity: str) -> Optional[Decision]:
    """
    Count one request against the named limit.

    Returns None when rate limiting (or this one limit) is switched off.
    """
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
