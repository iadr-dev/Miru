"""Every FinMind HTTP call, counted against the token's own cap, shared by the API and ingest.

FinMind caps one token at a number of requests per hour and answers 402 past it. The API's
requests and the ingest process use the same token, and before this only the orchestrator counted
anything (skill lanes, not calls) while ingest counted nothing: a three-year backfill makes about
11,000 calls, enough to use the whole allowance on its own, after which every user request failed
with 402 while ingest kept sending calls that were all refused.

Here each call is counted once, before it is made, in one counter per window that every process
shares (Redis, or this process alone without it):

- **ingest** may use up to `share` of the cap; past that it waits for the next window, so the rest
  stays for users. It yields to users too: their calls count against it, so a busy hour leaves it
  less.
- **interactive** callers may use the whole cap; past it the call is refused at once (no request is
  sent, so no 402 round trip).
- A 402 from FinMind (its own count, which this cannot see exactly) closes the window for everyone
  until it ends.

If Redis does not answer, calls are not counted (FinMind's own cap still applies) and Redis is not
tried again for a few seconds, so an outage costs one timeout, not one per call."""

from __future__ import annotations

import time
from typing import Literal, Protocol

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config.settings import Settings
from netclient.redis_clients import request_path_redis

logger = structlog.get_logger(__name__)

type QuotaRole = Literal["interactive", "ingest"]

_REDIS_RETRY_AFTER_SECONDS = 5.0


class FinMindQuota(Protocol):
    role: QuotaRole

    async def acquire(self) -> float | None:
        """Count one call. `None`: go ahead. A number: over the cap, seconds until the window
        resets (nothing was counted)."""
        ...

    async def release(self) -> None:
        """Give back one counted call that was not made."""
        ...

    async def mark_exhausted(self) -> None:
        """FinMind answered 402: no caller sends another call until the window ends."""
        ...

    async def remaining(self) -> int | None:
        """Calls left in this window for an interactive caller, or `None` if unknown."""
        ...


def _cap(limit: int, share: float, role: QuotaRole) -> int:
    return limit if role == "interactive" else max(1, int(limit * share))


def _window(now: float, window_seconds: int) -> tuple[int, float]:
    bucket = int(now // window_seconds)
    return bucket, window_seconds - (now - bucket * window_seconds)


class UnmeteredFinMindQuota:
    """Counts nothing: the default for an adapter built without a quota (tests, one-off tools)."""

    def __init__(self, role: QuotaRole = "interactive") -> None:
        self.role: QuotaRole = role

    async def acquire(self) -> float | None:
        return None

    async def release(self) -> None:
        return None

    async def mark_exhausted(self) -> None:
        return None

    async def remaining(self) -> int | None:
        return None


class InMemoryFinMindQuota:
    """This process's own count: for a deployment without Redis (one process)."""

    def __init__(self, *, role: QuotaRole, limit: int, window_seconds: int, share: float) -> None:
        self.role: QuotaRole = role
        self._limit = limit
        self._window_seconds = window_seconds
        self._cap = _cap(limit, share, role)
        self._bucket = -1
        self._used = 0
        self._exhausted = False

    def _roll(self) -> float:
        bucket, left = _window(time.time(), self._window_seconds)
        if bucket != self._bucket:
            self._bucket, self._used, self._exhausted = bucket, 0, False
        return left

    async def acquire(self) -> float | None:
        left = self._roll()
        if self._exhausted or self._used >= self._cap:
            return left
        self._used += 1
        return None

    async def release(self) -> None:
        self._roll()
        self._used = max(0, self._used - 1)

    async def mark_exhausted(self) -> None:
        self._roll()
        self._exhausted = True

    async def remaining(self) -> int | None:
        self._roll()
        return 0 if self._exhausted else max(0, self._limit - self._used)


class RedisFinMindQuota:
    """The count shared by every process: one Redis key per window, incremented atomically before
    each call (a refused call is given back), plus a flag set when FinMind answered 402. A refused
    call is counted for the instant before it is given back, so many concurrent refusals can make
    a caller near the cap be refused slightly early, never late: the cap is never exceeded."""

    def __init__(  # noqa: PLR0913 - one keyword per quota dimension
        self,
        redis: Redis,
        *,
        role: QuotaRole,
        key_prefix: str,
        limit: int,
        window_seconds: int,
        share: float,
    ) -> None:
        self.role: QuotaRole = role
        self._redis = redis
        self._prefix = key_prefix
        self._limit = limit
        self._window_seconds = window_seconds
        self._cap = _cap(limit, share, role)
        self._skip_until = 0.0
        self._degraded = False

    def _keys(self, bucket: int) -> tuple[str, str]:
        return f"{self._prefix}:{bucket}", f"{self._prefix}:exhausted:{bucket}"

    def _unavailable(self, exc: BaseException) -> None:
        self._skip_until = time.monotonic() + _REDIS_RETRY_AFTER_SECONDS
        if not self._degraded:
            self._degraded = True
            logger.warning("finmind_quota_uncounted_redis_down", error=repr(exc))

    def _redis_skipped(self) -> bool:
        return time.monotonic() < self._skip_until

    async def acquire(self) -> float | None:
        if self._redis_skipped():
            return None
        bucket, left = _window(time.time(), self._window_seconds)
        calls, exhausted = self._keys(bucket)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.exists(exhausted)
                pipe.incr(calls)
                pipe.expire(calls, self._window_seconds + 1)
                closed, used, _ = await pipe.execute()
            if closed or int(used) > self._cap:
                await self._redis.decr(calls)
                return left
        except (RedisError, OSError) as exc:
            self._unavailable(exc)
            return None
        if self._degraded:
            self._degraded = False
            logger.info("finmind_quota_counting_again")
        return None

    async def release(self) -> None:
        if self._redis_skipped():
            return
        bucket, _ = _window(time.time(), self._window_seconds)
        try:
            await self._redis.decr(self._keys(bucket)[0])
        except (RedisError, OSError) as exc:
            self._unavailable(exc)

    async def mark_exhausted(self) -> None:
        bucket, left = _window(time.time(), self._window_seconds)
        logger.warning("finmind_quota_exhausted_402", resets_in_seconds=round(left))
        if self._redis_skipped():
            return
        try:
            await self._redis.set(self._keys(bucket)[1], "1", ex=int(left) + 1)
        except (RedisError, OSError) as exc:
            self._unavailable(exc)

    async def remaining(self) -> int | None:
        if self._redis_skipped():
            return None
        bucket, _ = _window(time.time(), self._window_seconds)
        calls, exhausted = self._keys(bucket)
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.exists(exhausted)
                pipe.get(calls)
                closed, used = await pipe.execute()
        except (RedisError, OSError) as exc:
            self._unavailable(exc)
            return None
        if closed:
            return 0
        return max(0, self._limit - int(used or 0))


def build_finmind_quota(
    settings: Settings, *, role: QuotaRole, redis: Redis | None = None
) -> FinMindQuota:
    """The shared Redis count when the deployment has Redis (`rate_limiter_backend=redis`), else
    this process's own."""
    if settings.rate_limiter_backend != "redis":
        return InMemoryFinMindQuota(
            role=role,
            limit=settings.finmind_calls_per_window,
            window_seconds=settings.finmind_call_window_seconds,
            share=settings.finmind_ingest_share,
        )
    client: Redis = redis if redis is not None else request_path_redis(settings)
    return RedisFinMindQuota(
        client,
        role=role,
        key_prefix=settings.finmind_quota_key_prefix,
        limit=settings.finmind_calls_per_window,
        window_seconds=settings.finmind_call_window_seconds,
        share=settings.finmind_ingest_share,
    )


__all__ = [
    "FinMindQuota",
    "InMemoryFinMindQuota",
    "QuotaRole",
    "RedisFinMindQuota",
    "UnmeteredFinMindQuota",
    "build_finmind_quota",
]
