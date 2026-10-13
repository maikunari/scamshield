"""Per-client sliding-window rate limit for the analyze endpoint (in memory, one process)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, *, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float:
        """Record a request from ``key``. Returns 0.0 when allowed, else the seconds until it would be."""
        if self.limit <= 0:
            return 0.0
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.001, hits[0] + self.window - now)
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle clients so the table cannot grow without bound
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]
            return 0.0


def client_ip(peer: str | None, forwarded_for: str | None, *, trust_forwarded: bool) -> str:
    """The address to rate-limit on. ``X-Forwarded-For`` is only believed behind a proxy you control
    (TRUST_FORWARDED_FOR=1); then the last entry is used, the one that proxy appended itself."""
    if trust_forwarded and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return peer or "unknown"
