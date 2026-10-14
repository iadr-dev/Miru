"""`pre_turn` rate limiting: a per-caller fixed-window request budget, config-driven.

Two implementations sit behind the seam, chosen at wiring time:

- `InMemoryRateLimiter` (sync `RateLimiter`) — the single-process reference, correct for dev
  SQLite where "one process at a time" holds.
- `RedisRateLimiter` (async `AsyncRateLimiter`) — the shared multi-worker backend. The
  check-and-increment is a single atomic Redis `INCR`+`EXPIRE` executed in one `MULTI`/`EXEC`
  pipeline (Redis guarantees the queued commands run with no other client interleaved), so two
  concurrent workers can never both pass a boundary check the way a separate read-then-write
  would let them. `EVAL`/Lua would be equally atomic, but the test profile's `fakeredis` does
  not ship a Lua interpreter, whereas `INCR`/`EXPIRE`-in-a-transaction is fully emulated — so the
  pipeline form is what is both correct in prod and real-testable in the default suite.

The request-path gate depends on `AsyncRateLimiter`; `build_rate_limiter` returns whichever the
config selects (wrapping the sync in-memory limiter in a trivial async adapter for the default
backend, since its check is a pure non-blocking in-process dict update).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config.settings import Settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RateLimitVerdict:
    allowed: bool
    remaining: int
    retry_after_seconds: float | None = None


@runtime_checkable
class RateLimiter(Protocol):
    def check(self, caller_id: str) -> RateLimitVerdict: ...


@runtime_checkable
class AsyncRateLimiter(Protocol):
    """The async request-path seam. The rate limit is per-caller and configured per call, not on
    the limiter, so one shared limiter can serve every tier (each caller's cap comes from its own
    tier)."""

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict: ...


@dataclass
class InMemoryRateLimiter:
    """A fixed-window counter per caller: `max_requests` per `window_seconds`. Not atomic
    across processes — see module docstring."""

    max_requests: int
    window_seconds: float
    _windows: dict[str, tuple[float, int]] = field(
        default_factory=dict[str, tuple[float, int]], init=False
    )

    def check(self, caller_id: str) -> RateLimitVerdict:
        now = time.monotonic()
        window_start, count = self._windows.get(caller_id, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        count += 1
        self._windows[caller_id] = (window_start, count)
        if count > self.max_requests:
            retry_after = self.window_seconds - (now - window_start)
            return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=retry_after)
        return RateLimitVerdict(allowed=True, remaining=self.max_requests - count)


class InMemoryAsyncRateLimiter:
    """Presents the async seam over a per-caller in-process fixed-window count — the default,
    single-process backend. Its increment is a pure in-memory dict update, so the `async` is only
    interface-shaping (no real await); it exists so the request-path gate can depend on one
    seam regardless of backend."""

    # Expired windows are dropped once this many callers are held, so the map stays bounded.
    _PRUNE_AT = 10_000

    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}

    def _prune(self, now: float, window_seconds: int) -> None:
        self._windows = {
            caller: entry
            for caller, entry in self._windows.items()
            if now - entry[0] < window_seconds
        }

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        now = time.monotonic()
        if len(self._windows) >= self._PRUNE_AT:
            self._prune(now, window_seconds)
        window_start, count = self._windows.get(caller_id, (now, 0))
        if now - window_start >= window_seconds:
            window_start, count = now, 0
        count += 1
        self._windows[caller_id] = (window_start, count)
        if count > max_requests:
            return RateLimitVerdict(
                allowed=False,
                remaining=0,
                retry_after_seconds=window_seconds - (now - window_start),
            )
        return RateLimitVerdict(allowed=True, remaining=max_requests - count)


class RedisRateLimiter:
    """`AsyncRateLimiter` backed by an atomic Redis `INCR`+`EXPIRE` in one `MULTI`/`EXEC`
    pipeline. The key is `{prefix}:{caller_id}:{window_seconds}:{bucket}` where `bucket` is the
    integer window index — a fresh window uses a fresh key, so no explicit reset is needed and an
    expired bucket key simply vanishes."""

    def __init__(self, redis: Redis, *, key_prefix: str) -> None:
        self._redis = redis
        self._prefix = key_prefix

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        now = time.time()
        bucket = int(now // window_seconds)
        key = f"{self._prefix}:{caller_id}:{window_seconds}:{bucket}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # Refreshing the TTL on each increment keeps the bucket alive for the whole window;
            # since the key already carries the window index, this never bleeds one window's count
            # into the next — it only bounds how long an idle bucket lingers before eviction.
            pipe.expire(key, window_seconds + 1)
            results = await pipe.execute()
        count = int(results[0])
        if count > max_requests:
            retry_after = float(window_seconds) - (now - bucket * window_seconds)
            return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=retry_after)
        return RateLimitVerdict(allowed=True, remaining=max_requests - count)


class ResilientRateLimiter:
    """The shared Redis limiter while Redis answers; this process's own in-memory limiter while it
    does not.

    Every read route and every chat turn checks the rate limit first, so an unguarded Redis error
    here turned a Redis outage into a 500 on almost every request. Failing open would leave the
    service unprotected for the length of the outage; failing closed would refuse every request.
    Falling back to a per-process limit keeps requests flowing and still limited: each API process
    enforces the same per-caller cap on its own (so a caller spread over N processes gets at most N
    times the cap until Redis is back). After a failure Redis is not tried again for
    `retry_after_seconds`, so an outage costs one timeout, not one per request."""

    def __init__(
        self,
        primary: AsyncRateLimiter,
        fallback: AsyncRateLimiter,
        *,
        retry_after_seconds: float = 5.0,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._retry_after = retry_after_seconds
        self._skip_until = 0.0
        self._degraded = False

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        now = time.monotonic()
        if now >= self._skip_until:
            try:
                verdict = await self._primary.check(
                    caller_id, max_requests=max_requests, window_seconds=window_seconds
                )
            except (RedisError, OSError) as exc:
                self._skip_until = now + self._retry_after
                if not self._degraded:
                    self._degraded = True
                    logger.warning("rate_limiter_degraded_to_local", error=repr(exc))
            else:
                if self._degraded:
                    self._degraded = False
                    logger.info("rate_limiter_recovered")
                return verdict
        return await self._fallback.check(
            caller_id, max_requests=max_requests, window_seconds=window_seconds
        )


def build_rate_limiter(settings: Settings, redis: Redis | None = None) -> AsyncRateLimiter:
    """Select the request-rate backend from config. `redis` is injected (tests pass fakeredis);
    when omitted for the `redis` backend, a client is opened from `redis_url` with the
    request-path timeouts. The Redis backend degrades to a local limit while Redis is down
    (`ResilientRateLimiter`)."""
    if settings.rate_limiter_backend == "redis":
        timeout = settings.redis_request_timeout_seconds
        client: Redis = (
            redis
            if redis is not None
            else Redis.from_url(
                settings.redis_url, socket_timeout=timeout, socket_connect_timeout=timeout
            )
        )
        return ResilientRateLimiter(
            RedisRateLimiter(client, key_prefix=settings.rate_limit_key_prefix),
            InMemoryAsyncRateLimiter(),
        )
    return InMemoryAsyncRateLimiter()


__all__ = [
    "AsyncRateLimiter",
    "InMemoryAsyncRateLimiter",
    "InMemoryRateLimiter",
    "RateLimitVerdict",
    "RateLimiter",
    "RedisRateLimiter",
    "ResilientRateLimiter",
    "build_rate_limiter",
]
