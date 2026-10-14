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
from redis.exceptions import RedisError

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


_TEST_FAILURE_TTL_SECONDS = 30


def _caching_adapter(wrapped: _CountingStubAdapter, redis: Redis) -> CachingSourceAdapter:
    return CachingSourceAdapter(
        wrapped,
        redis,
        key_prefix="marketdata:cache:test",
        ttl_by_class=_TEST_TTL_BY_CLASS,
        failure_ttl_seconds=_TEST_FAILURE_TTL_SECONDS,
    )


async def test_cache_miss_calls_through_and_populates(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    result = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    assert wrapped.calls == 1
    assert result.payload["calls"] == 1
    key = "marketdata:cache:test:stub:TW:price_eod:2330"
    assert await fake_redis.get(key) is not None


async def test_two_adapters_same_capability_and_symbol_do_not_collide(fake_redis: Redis) -> None:
    """Real bug this guards against: `calendar_events` for TW is served by two adapters
    (`ncdr_closure` + `twse_openapi`) merged together (`calendar/events.py::fetch_feed_events`),
    each wrapped in its own `CachingSourceAdapter`. Before `source_id` joined the cache key, both
    wrappers read/wrote the identical Redis key for `(TW, calendar_events, 2330)`, so the second
    adapter's `fetch()` silently returned the first adapter's cached payload."""

    class _OtherStubAdapter(_CountingStubAdapter):
        source_id = "stub_2"

    first = _CountingStubAdapter()
    second = _OtherStubAdapter()
    first_wrapper = _caching_adapter(first, fake_redis)
    second_wrapper = _caching_adapter(second, fake_redis)

    first_result = await first_wrapper.fetch(
        Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT
    )
    second_result = await second_wrapper.fetch(
        Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT
    )

    assert first.calls == 1
    assert second.calls == 1  # not served from the first adapter's cache entry
    assert first_result.source_id == "stub"
    assert second_result.source_id == "stub_2"


async def test_redis_unreachable_on_get_falls_through_to_live_fetch(
    fake_redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces a real incident: an unguarded `redis.get()` turned a few seconds of Redis
    connection jitter into an unhandled `TimeoutError` surfacing as a 500 on every route that
    happened to poll during the window. A Redis-tier outage on the read must degrade to a direct
    live fetch (the same outcome as a cache miss), never propagate to the caller."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RedisError("Timeout reading from redis:6379")

    monkeypatch.setattr(fake_redis, "get", _boom)

    result = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)

    assert wrapped.calls == 1
    assert result.payload["calls"] == 1


async def test_redis_unreachable_on_single_flight_falls_through_to_live_fetch(
    fake_redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same degrade-to-live-fetch contract, but for a Redis failure during the single-flight
    lock acquire/store path (not just the initial `get`) — e.g. Redis drops mid-request after
    the cache-miss check already succeeded."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RedisError("Timeout connecting to server")

    monkeypatch.setattr(fake_redis, "set", _boom)

    result = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)

    assert wrapped.calls == 1
    assert result.payload["calls"] == 1


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
    key = "marketdata:cache:test:stub:TW:price_eod:2330"
    ttl = await fake_redis.ttl(key)
    assert 0 < ttl <= 5


async def test_official_eod_gets_long_ttl(fake_redis: Redis) -> None:
    wrapped = _CountingStubAdapter(timing_class=TimingClass.OFFICIAL_EOD)
    adapter = _caching_adapter(wrapped, fake_redis)
    await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)
    key = "marketdata:cache:test:stub:TW:price_eod:2330"
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


async def test_query_param_participates_in_cache_key_for_symbol_search(
    fake_redis: Redis,
) -> None:
    """Regression guard: `SYMBOL_SEARCH`/`SYMBOL_LOOKUP` are query-shaped, not symbol-shaped —
    every call passes the same empty `Symbol(id="")`. Confirmed live: searching "2330" right
    after "AAPL" returned AAPL's cached results because both calls collapsed onto one Redis key
    without `query` in it."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    sym = Symbol(id="", market=Market.US)
    await adapter.fetch(Capability.SYMBOL_SEARCH, sym, tls_policy=TLSPolicy.STRICT, query="AAPL")
    await adapter.fetch(Capability.SYMBOL_SEARCH, sym, tls_policy=TLSPolicy.STRICT, query="2330")
    assert wrapped.calls == 2  # different queries never collide on one cache key


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
            failure_ttl_seconds=_TEST_FAILURE_TTL_SECONDS,
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


async def test_a_failed_cache_write_after_the_fetch_does_not_fetch_again(
    fake_redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is taken and the source answers, then storing the result fails (Redis dropped
    mid-request). That used to raise into `fetch`'s fall-through, which fetched the same data from
    the source a second time: twice the FinMind quota for one answer."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    real_set = fake_redis.set

    async def _set(key: str, value: object, **kwargs: object) -> object:
        if kwargs.get("nx"):
            return await real_set(key, value, **kwargs)  # the single-flight lock
        raise RedisError("Connection reset by peer")  # storing the result

    monkeypatch.setattr(fake_redis, "set", _set)

    result = await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)

    assert wrapped.calls == 1
    assert result.payload["calls"] == 1


async def test_during_an_outage_the_cache_is_not_tried_on_every_request(
    fake_redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each attempt against a Redis that does not answer waits the full connect timeout (2 s);
    trying it on every request made every read 2 s slower for as long as the outage lasted."""
    wrapped = _CountingStubAdapter()
    adapter = _caching_adapter(wrapped, fake_redis)
    attempts = 0

    async def _down(*_args: object, **_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise RedisError("Timeout connecting to server")

    monkeypatch.setattr(fake_redis, "get", _down)

    for _ in range(10):
        await adapter.fetch(Capability.PRICE_EOD, _sym(), tls_policy=TLSPolicy.STRICT)

    assert attempts == 1  # then skipped for the pause
    assert wrapped.calls == 10  # every request still answered from the source
