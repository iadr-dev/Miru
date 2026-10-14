"""Every FinMind call is counted against the token's own cap, shared by the API and ingest.

Before: only orchestrator skill lanes were counted, and ingest counted nothing. A three-year
backfill (about 11,000 calls) could spend the whole hourly allowance while the lane budget still
showed full headroom; user requests then failed with 402, and ingest kept sending calls that were
all refused."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import fakeredis.aioredis
import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

import sources.finmind as finmind_module
from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter, FinMindQuotaRefusedError
from sources.finmind._quota import InMemoryFinMindQuota, RedisFinMindQuota
from tests.sources.finmind._helpers import settings

_OK = {"msg": "success", "status": 200, "data": []}


@pytest.fixture
async def fake_redis() -> AsyncIterator[Redis]:
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


_LIMIT = {"msg": "Requests reach the upper limit.", "status": 402}


def _redis_quota(redis: Redis, role: str, *, limit: int = 10, share: float = 0.5):  # type: ignore[no-untyped-def]
    return RedisFinMindQuota(
        redis,
        role=role,  # type: ignore[arg-type]
        key_prefix="finmind:calls",
        limit=limit,
        window_seconds=3600,
        share=share,
    )


async def _take(quota, n: int) -> int:  # type: ignore[no-untyped-def]
    return sum([await quota.acquire() is None for _ in range(n)])


# ---- the shared count ---------------------------------------------------------------------------


async def test_ingest_stops_at_its_share_and_users_keep_the_rest(fake_redis: Redis) -> None:
    ingest = _redis_quota(fake_redis, "ingest")  # the ingest process
    api = _redis_quota(fake_redis, "interactive")  # an API process, same Redis

    assert await _take(ingest, 20) == 5  # half of 10
    assert await _take(api, 20) == 5  # the other half is still there for users
    assert await api.remaining() == 0


async def test_ingest_yields_to_users(fake_redis: Redis) -> None:
    ingest = _redis_quota(fake_redis, "ingest")
    api = _redis_quota(fake_redis, "interactive")

    assert await _take(api, 4) == 4  # a busy hour for users
    assert await _take(ingest, 20) == 1  # ingest only gets what is left of its share


async def test_a_402_closes_the_window_for_every_process(fake_redis: Redis) -> None:
    ingest = _redis_quota(fake_redis, "ingest")
    api = _redis_quota(fake_redis, "interactive")

    await ingest.mark_exhausted()

    assert await api.acquire() is not None  # refused: seconds until the window resets
    assert await ingest.acquire() is not None
    assert await api.remaining() == 0


async def test_concurrent_calls_never_pass_the_cap(fake_redis: Redis) -> None:
    api = _redis_quota(fake_redis, "interactive", limit=5)

    granted = await asyncio.gather(*(api.acquire() for _ in range(30)))

    assert sum(g is None for g in granted) == 5


async def test_while_redis_is_down_calls_go_ahead_uncounted() -> None:
    class _Down:
        attempts = 0

        def pipeline(self, **_kwargs: object) -> object:
            _Down.attempts += 1
            raise RedisConnectionError("Connection refused")

    quota = _redis_quota(_Down(), "interactive")  # type: ignore[arg-type]

    assert await _take(quota, 10) == 10  # FinMind's own cap still applies
    assert _Down.attempts == 1  # then Redis is left alone for a few seconds


def test_without_redis_one_process_counts_on_its_own() -> None:
    quota = InMemoryFinMindQuota(role="ingest", limit=10, window_seconds=3600, share=0.3)

    assert asyncio.run(_take(quota, 10)) == 3


# ---- the adapter: count before calling, stop after a 402 ----------------------------------------


class _Upstream:
    """FinMind: answers each request with the next scripted status (then 200)."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests = 0

    def factory(self, **_kwargs: object) -> httpx.AsyncClient:
        def handler(_request: httpx.Request) -> httpx.Response:
            self.requests += 1
            status = self.statuses.pop(0) if self.statuses else 200
            return httpx.Response(status, json=_OK if status == 200 else _LIMIT)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _adapter(monkeypatch: pytest.MonkeyPatch, upstream: _Upstream, quota) -> FinMindAdapter:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(finmind_module, "build_async_client", upstream.factory)
    return FinMindAdapter(settings=settings(), quota=quota)


async def _price(adapter: FinMindAdapter):  # type: ignore[no-untyped-def]
    return await adapter.fetch(
        Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW), tls_policy=TLSPolicy.STRICT
    )


async def test_a_user_request_over_the_cap_is_refused_without_calling_finmind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _Upstream()
    quota = InMemoryFinMindQuota(role="interactive", limit=2, window_seconds=3600, share=0.5)
    adapter = _adapter(monkeypatch, upstream, quota)

    results = [await _price(adapter) for _ in range(3)]

    assert upstream.requests == 2
    assert results[-1].health.status is HealthStatus.UNREACHABLE
    assert "quota" in results[-1].health.detail


async def test_after_a_402_no_more_requests_are_sent_this_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _Upstream(402)
    quota = InMemoryFinMindQuota(role="interactive", limit=100, window_seconds=3600, share=0.5)
    adapter = _adapter(monkeypatch, upstream, quota)

    first = await _price(adapter)
    second = await _price(adapter)

    assert "402" in first.health.detail
    assert second.health.status is HealthStatus.UNREACHABLE
    assert upstream.requests == 1  # the second never reached FinMind


class _ScriptedQuota:
    """Ingest's view: over its share once (wait 0 s), then go ahead."""

    role = "ingest"

    def __init__(self, waits: list[float | None]) -> None:
        self.waits = waits
        self.exhausted = 0

    async def acquire(self) -> float | None:
        return self.waits.pop(0) if self.waits else None

    async def release(self) -> None:
        return None

    async def mark_exhausted(self) -> None:
        self.exhausted += 1

    async def remaining(self) -> int | None:
        return None


async def test_ingest_over_its_share_waits_for_the_window_then_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _Upstream()
    adapter = _adapter(monkeypatch, upstream, _ScriptedQuota([0.0, None]))

    result = await _price(adapter)

    assert result.health.status is not HealthStatus.UNREACHABLE  # FinMind answered
    assert upstream.requests == 1


async def test_ingest_after_a_402_waits_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = _Upstream(402)
    quota = _ScriptedQuota([None, 0.0, None])
    adapter = _adapter(monkeypatch, upstream, quota)

    result = await _price(adapter)

    assert result.health.status is not HealthStatus.UNREACHABLE  # FinMind answered
    assert upstream.requests == 2
    assert quota.exhausted == 1


async def test_ingest_refused_again_after_the_reset_stops_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan or token that lapsed answers 402 every window; waiting an hour per date forever
    would hide that."""
    upstream = _Upstream(402, 402)
    adapter = _adapter(monkeypatch, upstream, _ScriptedQuota([None, 0.0, None]))

    with pytest.raises(FinMindQuotaRefusedError):
        await _price(adapter)
    assert upstream.requests == 2


@pytest.mark.concurrency
async def test_on_a_real_redis_the_share_the_cap_and_a_402_hold_under_concurrency() -> None:
    import os  # noqa: PLC0415 - only this real-Redis case needs it

    from config.settings import Settings  # noqa: PLC0415
    from netclient.redis_clients import request_path_redis  # noqa: PLC0415

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not set - real Redis check skipped")
    redis = request_path_redis(Settings(redis_url=url))
    prefix = f"finmind:calls:test:{os.getpid()}"

    def make(role: str) -> RedisFinMindQuota:
        return RedisFinMindQuota(
            redis,
            role=role,
            key_prefix=prefix,
            limit=100,
            window_seconds=3600,
            share=0.5,  # type: ignore[arg-type]
        )

    ingest, api = make("ingest"), make("interactive")
    try:
        ingest_granted = await asyncio.gather(*(ingest.acquire() for _ in range(200)))
        api_granted = await asyncio.gather(*(api.acquire() for _ in range(200)))
        assert sum(g is None for g in ingest_granted) == 50
        assert sum(g is None for g in api_granted) == 50
        await api.mark_exhausted()
        assert await ingest.acquire() is not None
    finally:
        await redis.delete(*[k async for k in redis.scan_iter(f"{prefix}*")])
        await redis.aclose()
