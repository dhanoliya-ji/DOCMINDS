"""It slows us down ON PURPOSE, so a free API quota is never exceeded."""
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
        self._clock = clock or (lambda: time.monotonic())
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))
        self._log: Deque[Tuple[float, int, int]] = deque()
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

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

    def wait_time(self, requests: int, tokens: int, rpm: int, tpm: int) -> float:
        """Seconds until a call of this size would fit (0 means now)."""
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
        freed_requests, freed_tokens = 0, 0
        for stamp, entry_requests, entry_tokens in self._log:
            freed_requests += entry_requests
            freed_tokens += entry_tokens
            ok_requests = rpm <= 0 or used_requests - freed_requests + requests <= rpm
            ok_tokens = tpm <= 0 or used_tokens - freed_tokens + tokens <= tpm
            if ok_requests and ok_tokens:
                return max(0.0, stamp + self.window - now) + 0.05
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
        """Wait until the call fits, record it, and return how long we waited."""
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
            self._sleep(delay)
            waited += delay
