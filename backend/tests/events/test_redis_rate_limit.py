"""`RedisRateLimiter` against fakeredis: the atomic `INCR`+`EXPIRE` check-and-increment, and the
`build_rate_limiter` config selection. No live Redis in the default profile."""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings
from events.rate_limit import (
    InMemoryAsyncRateLimiter,
    RedisRateLimiter,
    ResilientRateLimiter,
    build_rate_limiter,
)


async def test_allows_up_to_max_then_blocks(fake_redis) -> None:
    limiter = RedisRateLimiter(fake_redis, key_prefix="ratelimit")

    first = await limiter.check("caller-1", max_requests=2, window_seconds=60)
    second = await limiter.check("caller-1", max_requests=2, window_seconds=60)
    third = await limiter.check("caller-1", max_requests=2, window_seconds=60)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.retry_after_seconds is not None


async def test_separate_callers_independent(fake_redis) -> None:
    limiter = RedisRateLimiter(fake_redis, key_prefix="ratelimit")

    assert (await limiter.check("a", max_requests=1, window_seconds=60)).allowed is True
    assert (await limiter.check("b", max_requests=1, window_seconds=60)).allowed is True
    assert (await limiter.check("a", max_requests=1, window_seconds=60)).allowed is False


async def test_bucket_key_expires(fake_redis) -> None:
    limiter = RedisRateLimiter(fake_redis, key_prefix="rl")
    await limiter.check("c", max_requests=5, window_seconds=30)
    keys = [k async for k in fake_redis.scan_iter(match="rl:c:*")]
    assert keys, "the increment must have written a window-bucket key"
    for key in keys:
        assert await fake_redis.ttl(key) > 0  # TTL set in the same op, so an idle bucket evicts


@pytest.mark.concurrency
async def test_concurrent_boundary_never_double_admits(fake_redis) -> None:
    """The whole point of the atomic check-and-increment: two requests racing at the boundary of a
    max=1 window must not both be admitted. A read-then-write would let both pass."""
    limiter = RedisRateLimiter(fake_redis, key_prefix="rl")
    results = await asyncio.gather(
        *(limiter.check("racer", max_requests=1, window_seconds=60) for _ in range(20))
    )
    admitted = [r for r in results if r.allowed]
    assert len(admitted) == 1


def test_build_selects_memory_by_default() -> None:
    limiter = build_rate_limiter(Settings())
    assert isinstance(limiter, InMemoryAsyncRateLimiter)


def test_build_selects_redis_when_configured(fake_redis) -> None:
    limiter = build_rate_limiter(Settings(rate_limiter_backend="redis"), redis=fake_redis)
    # The shared Redis limiter, behind the fallback that keeps requests limited while Redis is down.
    assert isinstance(limiter, ResilientRateLimiter)
    assert isinstance(limiter._primary, RedisRateLimiter)
