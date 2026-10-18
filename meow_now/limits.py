"""Per-client rate limiting and a cap on concurrent synthesis, for public traffic.

Both are in-process: each replica keeps its own counts, which is enough to stop
one client from monopolizing a replica without adding a shared store.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request

# How often a request waiting for a render slot looks again. Polling keeps the
# slots independent of any one event loop (the test client runs several).
_POLL_S = 0.02


class RateLimiter:
    """A token bucket per client key: `per_minute` tokens, refilled continuously.

    A client may burst up to `per_minute` requests, then gets one more every
    60 / `per_minute` seconds. At most `max_clients` buckets are kept; the least
    recently seen client is forgotten first, so memory stays bounded under a
    flood of distinct addresses. `per_minute <= 0` disables the limiter.
    """

    def __init__(
        self,
        per_minute: int,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def __len__(self) -> int:
        return len(self._buckets)

    def hit(self, key: str) -> float:
        """Spend one token for `key`. Returns 0 if allowed, else seconds until a token frees up."""
        if not self.enabled:
            return 0.0
        capacity, rate = float(self.per_minute), self.per_minute / 60.0
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.pop(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * rate)
            if tokens >= 1.0:
                tokens -= 1.0
                wait = 0.0
            else:
                wait = (1.0 - tokens) / rate
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_clients:
                self._buckets.popitem(last=False)
        return wait


def client_ip(request: Request, trust_proxy_headers: bool) -> str:
    """The address to rate-limit on.

    Behind a reverse proxy every request arrives from the proxy, so with
    `trust_proxy_headers` the last X-Forwarded-For entry is used: the address
    the nearest proxy saw, which a client cannot forge by sending its own header.
    Only enable it when a proxy you run always sets that header.
    """
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def rate_limit(limiter: RateLimiter, trust_proxy_headers: bool):
    """A FastAPI dependency that answers 429 with Retry-After once a client is over its limit."""

    async def check(request: Request) -> None:
        wait = limiter.hit(client_ip(request, trust_proxy_headers))
        if wait > 0:
            raise HTTPException(
                429,
                "too many requests; the cat needs a breather",
                headers={"Retry-After": str(math.ceil(wait))},
            )

    return check


class RenderSlots:
    """At most `limit` syntheses at once. Extra requests wait up to `wait_s`, then get 503.

    Synthesis is CPU-bound numpy work in the thread pool; without a cap a burst
    piles up threads that all finish late. `limit <= 0` means no cap.
    """

    def __init__(self, limit: int, wait_s: float = 5.0) -> None:
        self.limit = limit
        self.wait_s = wait_s
        self._sem = threading.BoundedSemaphore(limit) if limit > 0 else None

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._sem is None:
            yield
            return
        deadline = time.monotonic() + self.wait_s
        while not self._sem.acquire(blocking=False):
            if time.monotonic() >= deadline:
                raise HTTPException(
                    503,
                    "every cat is busy; try again in a moment",
                    headers={"Retry-After": "2"},
                )
            await asyncio.sleep(_POLL_S)
        try:
            yield
        finally:
            self._sem.release()
