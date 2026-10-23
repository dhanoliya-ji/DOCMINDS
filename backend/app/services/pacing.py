"""
pacing.py
=========
WHAT THIS FILE DOES
-------------------
It slows us down ON PURPOSE, so a free API quota is never exceeded.

Google's free Gemini tier allows a fixed number of requests and tokens per
MINUTE. The old approach was "send as fast as possible, retry on HTTP 429".
For a small file that works. For a 300-page PDF it does not: the first minute's
quota is gone in two seconds, every later request is refused, and a handful of
short retries ran out long before the minute reset -- which is exactly how a
real user's 2 MB PDF failed with "still failing after retries (HTTP 429)".

So we keep our own count instead. Before each call we ask the pacer "may I send
25 texts and 12,000 tokens now?". If the last 60 seconds already hold too much,
the pacer SLEEPS until enough of that window has rolled off, then says yes.

THE SLIDING WINDOW
------------------
Every call is written down as (when, texts, tokens). Anything older than 60
seconds is forgotten. The sum of what is left is "what we have used in the
last minute", and a new call is allowed only if it fits under both limits.

    time ->   [t-60s ............................ now]
               call A (30 texts)   call B (40)    new call (25)?
               30 + 40 + 25 = 95 > 90 per minute   -> wait until A rolls off

COOLDOWN
--------
When the server says "429, retry in 37 s" anyway (another process shares the
key, or our token estimate was low), `cool_down(37)` makes EVERY caller of this
pacer wait out those 37 seconds -- not just the one that was refused. Sending
more requests into a quota that is known to be empty only burns the retries.

TESTABLE TIME
-------------
`clock` and `sleep` can be passed in. The tests use a fake clock whose sleep
just moves the time forward, so a "wait two minutes" test runs instantly and
checks exact numbers.
"""

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

logger = logging.getLogger(__name__)


class RatePacer:
    """A thread-safe, rolling 60-second budget of requests and tokens."""

    def __init__(
        self,
        window_seconds: float = 60.0,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.window = window_seconds
        # Looked up at CALL time (the lambdas), not bound now, so a test that
        # monkeypatches time.sleep is honoured by a pacer built at import.
        self._clock = clock or (lambda: time.monotonic())
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))
        self._log: Deque[Tuple[float, int, int]] = deque()
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    # ---- bookkeeping --------------------------------------------------
    def _forget_old(self, now: float) -> None:
        while self._log and self._log[0][0] <= now - self.window:
            self._log.popleft()

    def used(self) -> Tuple[int, int]:
        """(texts, tokens) sent in the current window."""
        with self._lock:
            self._forget_old(self._clock())
            return sum(e[1] for e in self._log), sum(e[2] for e in self._log)

    def cool_down(self, seconds: float) -> None:
        """Everyone waits at least `seconds` before the next call."""
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + max(0.0, seconds))

    def reset(self) -> None:
        with self._lock:
            self._log.clear()
            self._cooldown_until = 0.0

    # ---- the one question callers ask -----------------------------------
    def wait_time(self, requests: int, tokens: int, rpm: int, tpm: int) -> float:
        """
        Seconds until a call of this size would fit (0 means now).

        A call bigger than a whole minute's budget is let through once the
        window is EMPTY -- otherwise it could never be sent at all and would
        wait forever. The server may still refuse it; that is the retry
        loop's business, not a deadlock here.
        """
        now = self._clock()
        self._forget_old(now)
        if self._cooldown_until > now:
            return self._cooldown_until - now
        used_requests = sum(e[1] for e in self._log)
        used_tokens = sum(e[2] for e in self._log)
        fits = (rpm <= 0 or used_requests + requests <= rpm) and (
            tpm <= 0 or used_tokens + tokens <= tpm
        )
        if fits or not self._log:
            return 0.0
        # Not yet: find the earliest moment enough of the window rolls off.
        freed_requests, freed_tokens = 0, 0
        for stamp, entry_requests, entry_tokens in self._log:
            freed_requests += entry_requests
            freed_tokens += entry_tokens
            ok_requests = rpm <= 0 or used_requests - freed_requests + requests <= rpm
            ok_tokens = tpm <= 0 or used_tokens - freed_tokens + tokens <= tpm
            if ok_requests and ok_tokens:
                return max(0.0, stamp + self.window - now) + 0.05
        # Only an empty window fits it (see the docstring).
        return max(0.0, self._log[-1][0] + self.window - now) + 0.05

    def acquire(
        self,
        requests: int,
        tokens: int,
        rpm: int,
        tpm: int,
        *,
        block: bool = True,
        on_wait: Optional[Callable[[float], None]] = None,
        max_wait: Optional[float] = None,
    ) -> float:
        """
        Wait until the call fits, record it, and return how long we waited.

        `block=False` records the call without waiting -- used for a user's
        question, which must not queue behind a document being embedded.
        `on_wait(seconds)` is told about each wait of a second or more, which
        is how a document's status comes to read "waiting for rate limit".
        `max_wait` stops waiting and raises TimeoutError instead.
        """
        waited = 0.0
        while True:
            with self._lock:
                delay = 0.0 if not block else self.wait_time(requests, tokens, rpm, tpm)
                if delay <= 0:
                    self._log.append((self._clock(), requests, tokens))
                    return waited
            if max_wait is not None and waited + delay > max_wait:
                raise TimeoutError(f"Rate pacing would wait {delay:.0f}s more.")
            if on_wait is not None and delay >= 1:
                try:
                    on_wait(delay)
                except Exception:  # pragma: no cover - a status update must never break pacing
                    logger.debug("pacing listener failed", exc_info=True)
            # Sleep OUTSIDE the lock, so other threads can still read usage.
            self._sleep(delay)
            waited += delay
