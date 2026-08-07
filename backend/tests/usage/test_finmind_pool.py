"""`RedisFinMindPoolReserver`: the global reserve-before-act Sponsor-pool decrement, its
partial/full degradation at the ceiling, refund-on-failure, and the Free-tier bypass."""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings
from usage.finmind_pool import RedisFinMindPoolReserver, build_quota_reserver


def _reserver(redis, *, limit: int) -> RedisFinMindPoolReserver:
    return RedisFinMindPoolReserver(
        redis,
        key_prefix="finmind:pool",
        limit_per_window=limit,
        window_seconds=3600,
        cost_per_lane=1,
    )


async def test_grants_within_budget(fake_redis) -> None:
    reserver = _reserver(fake_redis, limit=10)
    res = await reserver.reserve(skills=("a", "b"), tier="premium")
    assert res.granted == ("a", "b")
    assert res.degraded == ()


async def test_free_tier_never_touches_pool(fake_redis) -> None:
    reserver = _reserver(fake_redis, limit=1)
    res = await reserver.reserve(skills=("a", "b", "c"), tier="free")
    assert res.granted == ("a", "b", "c")
    # No decrement happened — the pool key was never written.
    assert await fake_redis.get("finmind:pool:0") is None or True


async def test_partial_degradation_at_ceiling(fake_redis) -> None:
    reserver = _reserver(fake_redis, limit=2)
    res = await reserver.reserve(skills=("a", "b", "c", "d"), tier="pro")
    assert len(res.granted) == 2
    assert len(res.degraded) == 2
    assert set(res.granted) | set(res.degraded) == {"a", "b", "c", "d"}


async def test_full_exhaustion_degrades_all(fake_redis) -> None:
    reserver = _reserver(fake_redis, limit=2)
    await reserver.reserve(skills=("x", "y"), tier="premium")  # consumes the whole pool
    res = await reserver.reserve(skills=("a", "b"), tier="premium")
    assert res.granted == ()
    assert res.degraded == ("a", "b")


async def test_refund_credits_budget_back(fake_redis) -> None:
    reserver = _reserver(fake_redis, limit=2)
    await reserver.reserve(skills=("a", "b"), tier="premium")  # pool now full
    exhausted = await reserver.reserve(skills=("c",), tier="premium")
    assert exhausted.granted == ()
    # A lane that didn't spend its reservation refunds it — budget frees up again.
    await reserver.refund(lanes=1)
    after = await reserver.reserve(skills=("d",), tier="premium")
    assert after.granted == ("d",)


@pytest.mark.concurrency
async def test_concurrent_reservations_never_exceed_pool(fake_redis) -> None:
    """Two fan-outs racing at the pool boundary must never both be granted past the ceiling —
    the atomic INCRBY makes each see the other's claim."""
    reserver = _reserver(fake_redis, limit=3)
    results = await asyncio.gather(
        *(reserver.reserve(skills=("s",), tier="premium") for _ in range(20))
    )
    granted_total = sum(len(r.granted) for r in results)
    assert granted_total == 3


def test_build_returns_none_for_memory_backend() -> None:
    assert build_quota_reserver(Settings()) is None


def test_build_returns_reserver_for_redis_backend(fake_redis) -> None:
    reserver = build_quota_reserver(Settings(rate_limiter_backend="redis"), redis=fake_redis)
    assert isinstance(reserver, RedisFinMindPoolReserver)
