"""`CachingSourceAdapter`: read-through hit/miss, TTL-by-`timing_class`, single-flight-on-miss,
and lossless `SourceResult` round-trip (caveat/health preserved) — against `fakeredis`, matching
`tests/usage/test_finmind_pool.py`'s real-Redis-shaped-but-fake-backed pattern. The genuinely
concurrent stampede-guard proof is `@pytest.mark.concurrency`-marked per this codebase's
existing convention.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest
from redis.asyncio import Redis

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.caching_adapter import CachingSourceAdapter


class _CountingStubAdapter:
    """A minimal `SourceAdapter` that counts real fetches and returns a fixed, caveated result."""

    source_id = "stub"
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(self, timing_class: TimingClass = TimingClass.OFFICIAL_EOD) -> None:
        self.calls = 0
        self._timing_class = timing_class

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        self.calls += 1
        # Simulate real upstream latency so concurrent misses genuinely overlap.
        await asyncio.sleep(0.05)
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=self._timing_class,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub caveat — never empty",
            payload={"price": 123.45, "calls": self.calls},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


@pytest.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


def _sym() -> Symbol:
    return Symbol(id="2330", market=Market.TW)


_TEST_TTL_BY_CLASS = {
    TimingClass.REFERENCE: 300,
    TimingClass.LIVE_ISH: 5,
    TimingClass.OFFICIAL_EOD: 43_200,
    TimingClass.EVENT_DRIVEN: 300,
}


def _caching_adapter(wrapped: _CountingStubAdapter, redis: Redis) -> CachingSourceAdapter:
    return CachingSourceAdapter(
        wrapped, redis, key_prefix="marketdata:cache:test", ttl_by_class=_TEST_TTL_BY_CLASS
    )


async def test_cache_miss_calls_through_and_populates(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    result = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    assert wrapped.calls == 1
    assert result.payload["calls"] == 1
    key = "marketdata:cache:test:TW:price_eod:2330"
    assert await fake_redis.get(key) is not None


async def test_cache_hit_avoids_wrapped_fetch(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    first = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    second = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    assert wrapped.calls == 1  # second call was a cache hit — no second real fetch
    assert second == first


async def test_ttl_set_per_timing_class(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter(timing_class=TimingClass.LIVE_ISH)
    adapter = _caching_adapter(wrapped, fake_redis)
    await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    key = "marketdata:cache:test:TW:price_eod:2330"
    ttl = await fake_redis.ttl(key)
    assert 0 < ttl <= 5


async def test_official_eod_gets_long_ttl(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter(timing_class=TimingClass.OFFICIAL_EOD)
    adapter = _caching_adapter(wrapped, fake_redis)
    await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    key = "marketdata:cache:test:TW:price_eod:2330"
    ttl = await fake_redis.ttl(key)
    assert ttl > 3600  # comfortably beyond a `reference`/`live-ish` TTL


async def test_result_round_trips_with_caveat_and_health_intact(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    live = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    cached = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    assert cached.currentness_caveat == live.currentness_caveat
    assert cached.health == live.health
    assert cached.source_id == live.source_id
    assert cached.payload == live.payload


async def test_date_param_participates_in_cache_key(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    sym = _sym()
    await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT, date="2026-08-01")
    await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT, date="2026-08-02")
    assert wrapped.calls == 2  # different dates never collide on one cache key


async def test_different_markets_never_collide(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    tw = Symbol(id="AAA", market=Market.TW)
    us = Symbol(id="AAA", market=Market.US)
    await adapter.fetch(Capability.PRICE_EOD, tw, tls_policy=TLSPolicy.STRICT)
    await adapter.fetch(Capability.PRICE_EOD, us, tls_policy=TLSPolicy.STRICT)
    assert wrapped.calls == 2


async def test_supports_and_source_id_passthrough(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    assert adapter.source_id == wrapped.source_id
    assert adapter.supports() == wrapped.supports()


@pytest.mark.concurrency
async def test_single_flight_guard_deduplicates_concurrent_misses(fake_redis: Redis) -> None:
    """N concurrent misses for the same key must result in exactly one real upstream fetch —
    the rest await the lock-holder's result instead of racing the source."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    sym = _sym()
    results = await asyncio.gather(
        *(adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT) for _ in range(10))
    )
    assert wrapped.calls == 1
    assert all(r.payload == results[0].payload for r in results)


@pytest.mark.concurrency
async def test_single_flight_guard_against_real_redis() -> None:
    """The same stampede-guard proof, but against a real Redis instance rather than fakeredis —
    exercises the actual `SET NX EX` acquire path against real server semantics, not an
    in-memory emulation. Requires the dev-parity Redis service (`docker compose up -d redis`) or
    a real `REDIS_URL` reachable from the test runner; skipped if neither is available."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = Redis.from_url(redis_url)
    try:
        await client.ping()
    except Exception:
        pytest.skip("no real Redis reachable for the concurrency-marked real-Redis proof")
    try:
        wrapped = _CountingStubAdapter()
        adapter = CachingSourceAdapter(
            wrapped,
            client,
            key_prefix="marketdata:cache:test:real",
            ttl_by_class=_TEST_TTL_BY_CLASS,
        )
        sym = _sym()
        results = await asyncio.gather(
            *(
                adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)
                for _ in range(10)
            )
        )
        assert wrapped.calls == 1
        assert all(r.payload == results[0].payload for r in results)
    finally:
        key = "marketdata:cache:test:real:TW:price_eod:2330"
        await client.delete(key, f"{key}:lock")
        await client.aclose()
