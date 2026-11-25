"""Fixed-window rate limiting with an in-memory (single process) or Redis (shared) backend."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from fastapi import HTTPException, Request, status


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> int:
        """Count one hit. Return 0 if allowed, else seconds until the window resets."""
        ...


class MemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, float]] = {}
        self._clock = clock

    def hit(self, key: str, limit: int, window_seconds: int) -> int:
        now = self._clock()
        with self._lock:
            count, reset = self._hits.get(key, (0, now + window_seconds))
            if now >= reset:
                count, reset = 0, now + window_seconds
            count += 1
            self._hits[key] = (count, reset)
            if len(self._hits) > 100_000:  # bound memory: drop expired windows
                self._hits = {k: v for k, v in self._hits.items() if v[1] > now}
            return 0 if count <= limit else max(1, int(reset - now + 0.999))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url)

    def hit(self, key: str, limit: int, window_seconds: int) -> int:
        k = f"rl:{key}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_seconds, nx=True)
        pipe.ttl(k)
        count, _, ttl = pipe.execute()
        return 0 if int(count) <= limit else max(1, int(ttl))


def client_ip(request: Request) -> str:
    if getattr(request.app.state, "trust_forwarded_for", False):
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "unknown")[:45]


def enforce(request: Request, key: str, limit: int, window_seconds: int) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    retry = limiter.hit(key, limit, window_seconds)
    if retry:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again shortly.",
            headers={"Retry-After": str(retry)},
        )


def per_ip(scope: str, limit: int, window_seconds: int):
    """Dependency: limit requests per client IP for one route scope."""

    def dep(request: Request) -> None:
        enforce(request, f"{scope}:ip:{client_ip(request)}", limit, window_seconds)

    return dep
