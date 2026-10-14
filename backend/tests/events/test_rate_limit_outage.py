"""A Redis outage degrades the request path instead of failing it.

Before: the rate limiter runs first on every read route and every chat turn, and a Redis error in
it was unhandled, so a Redis outage became a 500 on almost every request. Readiness also failed on
Redis, which (every replica sharing the one Redis) took every replica out of the load balancer."""

from __future__ import annotations

import asyncio
import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.lifecycle import ShutdownSignal
from api.routers import probes
from config.settings import Settings
from events.rate_limit import (
    InMemoryAsyncRateLimiter,
    RateLimitVerdict,
    ResilientRateLimiter,
    build_rate_limiter,
)


class _DownRedisLimiter:
    def __init__(self) -> None:
        self.calls = 0
        self.down = True

    async def check(
        self, caller_id: str, *, max_requests: int, window_seconds: int
    ) -> RateLimitVerdict:
        self.calls += 1
        if self.down:
            raise RedisConnectionError("Connection refused")
        return RateLimitVerdict(allowed=True, remaining=max_requests - 1)


async def test_while_redis_is_down_requests_are_still_served_and_still_limited() -> None:
    limiter = ResilientRateLimiter(_DownRedisLimiter(), InMemoryAsyncRateLimiter())

    verdicts = [await limiter.check("user-1", max_requests=3, window_seconds=60) for _ in range(5)]

    assert [v.allowed for v in verdicts] == [True, True, True, False, False]


async def test_an_outage_costs_one_attempt_not_one_per_request() -> None:
    primary = _DownRedisLimiter()
    limiter = ResilientRateLimiter(primary, InMemoryAsyncRateLimiter(), retry_after_seconds=60)

    for _ in range(20):
        await limiter.check("user-1", max_requests=100, window_seconds=60)

    assert primary.calls == 1


async def test_the_shared_limit_is_used_again_once_redis_is_back() -> None:
    primary = _DownRedisLimiter()
    limiter = ResilientRateLimiter(primary, InMemoryAsyncRateLimiter(), retry_after_seconds=0.05)
    await limiter.check("user-1", max_requests=100, window_seconds=60)

    primary.down = False
    await asyncio.sleep(0.06)
    await limiter.check("user-1", max_requests=100, window_seconds=60)

    assert primary.calls == 2  # tried again after the pause, and answered


async def test_a_real_unreachable_redis_answers_within_the_timeout() -> None:
    settings = Settings(
        rate_limiter_backend="redis",
        redis_url="redis://127.0.0.1:1/0",  # nothing listens here
        redis_request_timeout_seconds=1.0,
    )
    limiter = build_rate_limiter(settings)

    started = time.monotonic()
    verdict = await limiter.check("user-1", max_requests=5, window_seconds=60)

    assert verdict.allowed
    assert time.monotonic() - started < 3


class _State:
    def __init__(self, settings: Settings, session_factory: object) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.shutdown = ShutdownSignal()


class _Response:
    status_code = 200


@pytest.mark.parametrize("redis_up", [False])
async def test_readiness_does_not_fail_on_redis_but_reports_it(redis_up: bool) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",  # never the developer's .env database
            event_bus_backend="redis",
            rate_limiter_backend="redis",
            redis_url="redis://127.0.0.1:1/0",
            redis_request_timeout_seconds=1.0,
        )
        response = _Response()
        body = await probes._readiness(
            _State(settings, async_sessionmaker(engine)),  # type: ignore[arg-type]
            response,  # type: ignore[arg-type]
        )
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert body["status"] == "ready"
    assert body["checks"] == {"database": True, "redis": redis_up}


@pytest.mark.concurrency
async def test_a_burst_of_concurrent_checks_does_not_drop_to_the_local_limit() -> None:
    """The default Redis pool fails at once past its size (`MaxConnectionsError`), which this
    limiter read as "Redis is down": a burst of concurrent requests silently switched every
    process to its own local limit. The request-path pool waits for a free connection instead."""
    import os  # noqa: PLC0415 - only this real-Redis case needs it

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not set - real Redis check skipped")
    limiter = build_rate_limiter(Settings(rate_limiter_backend="redis", redis_url=url))

    verdicts = await asyncio.gather(
        *(limiter.check(f"burst-{i % 7}", max_requests=1000, window_seconds=60) for i in range(300))
    )

    assert all(v.allowed for v in verdicts)
    assert limiter._degraded is False  # type: ignore[attr-defined]


async def test_a_draining_replica_reports_it_so_the_load_balancer_stops_routing() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        state = _State(
            Settings(database_url="sqlite+aiosqlite:///:memory:"), async_sessionmaker(engine)
        )
        state.shutdown.begin()
        response = _Response()
        body = await probes._readiness(state, response)  # type: ignore[arg-type]
    finally:
        await engine.dispose()

    assert response.status_code == 503  # type: ignore[attr-defined]
    assert body["status"] == "draining"
