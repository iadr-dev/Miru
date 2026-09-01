"""The caching `SourceAdapter` wrapper — the `MarketDataStore` hot-cache tier's Redis half
(infra §3/§4). Wraps any concrete `SourceAdapter` (`FinMindAdapter` first, but generic —
nothing here is FinMind-specific) so `dataaccess/` can use a cache-wrapped adapter as a
drop-in replacement for an unwrapped one, keeping "only `sources/` does I/O" (ADR-0002) true:
this wrapper lives inside `sources/`, never in `context/` or `dataaccess/`.

Read-through, not write-through: `fetch()` checks Redis first (hit -> return, no network call);
on miss it calls through to the wrapped adapter's real `fetch()`, writes the result back with a
TTL keyed off the *result's own* `timing_class` (not the adapter's class attribute — several
adapters, e.g. `FinMindAdapter`/`MopsAdapter`, return a different `timing_class` per dataset than
their own class-level default), then returns it. A result whose `health.status` isn't `OK` (a
transient upstream failure, e.g. `ConnectTimeout`) is cached with a short fixed TTL instead of
the timing_class TTL — otherwise a few-second network blip gets replayed as "no data" for a full
timing_class TTL (hours, for `OFFICIAL_EOD`) even after the upstream recovers.

Single-flight-on-miss (stampede guard): the first request to miss a key takes a per-key Redis
lock (`SET key value NX EX ttl`) and fetches; concurrent misses for the same key poll for either
the lock-holder's cached result or the lock's release, so a hot symbol costs the source exactly
one upstream fetch, not N (infra §3/§10's "Cache fill (market data)" row).

Governance note (verified against `tools/base.py::dispatch_tool`, per the task's own §8): this
cache sits *below* the entitlement gate. `dispatch_tool` resolves `is_entitled` and only ever
substitutes a `MaskedTeaser` for an unentitled `sponsor` tool *before* `tool.run()` — a call that
never reaches this adapter at all when masked. When a call *does* reach here (entitled, or the
tool itself is `public`), the caveated/health-tagged `SourceResult` this adapter returns is
exactly what a live fetch would have produced — masking is applied downstream, per-call, on
every dispatch regardless of whether the underlying fetch was a cache hit or miss. So caching
the raw `SourceResult` here never lets a stale masked payload leak, and never bypasses the gate:
the gate runs on every call, cached or not.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config.settings import Settings
from schemas.enums import Capability, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult
from sources.base import SourceAdapter

logger = structlog.get_logger(__name__)

# Params that participate in the cache key beyond (capability, market, symbol) — per spec's
# "(capability, market, symbol[, date])". Only `date`-shaped params are folded in: other
# **params (e.g. free-text filters) are rare on the per-symbol path and would fragment the key
# space per-caller; a cache miss on those simply falls through to the source, never wrong data.
_DATE_PARAM_NAMES = ("date", "start_date", "end_date")

# Single-flight poll cadence while waiting on another worker's in-flight fetch for the same key.
_LOCK_POLL_INTERVAL_SECONDS = 0.05
# Upper bound on how long a waiter polls before giving up and fetching itself — protects against
# a lock-holder that crashed mid-fetch and never released/wrote the result (Redis `EX` on the
# lock key still expires it, but a waiter shouldn't block indefinitely on that alone).
_LOCK_WAIT_TIMEOUT_SECONDS = 10.0


def _cache_key(
    prefix: str, cap: Capability, market: Market, sym: Symbol, params: dict[str, object]
) -> str:
    """`(capability, market, symbol[, date])` per spec — multi-market data never collides."""
    parts = [prefix, market.value, cap.value, sym.id]
    for name in _DATE_PARAM_NAMES:
        value = params.get(name)
        if value is not None:
            parts.append(f"{name}={value}")
    return ":".join(parts)


def _serialize(result: SourceResult) -> str:
    return result.model_dump_json()


def _deserialize(raw: bytes | str) -> SourceResult:
    return SourceResult.model_validate_json(raw)


class CachingSourceAdapter:
    """Read-through Redis cache in front of a real `SourceAdapter`. Implements the same
    `SourceAdapter` protocol (`source_id`/`timing_class`/`supports`/`fetch`/`validate`) so it is
    a drop-in replacement for the adapter it wraps wherever one is registered
    (`dataaccess/composition.py`)."""

    def __init__(
        self,
        wrapped: SourceAdapter,
        redis: Redis,
        *,
        key_prefix: str,
        ttl_by_class: dict[TimingClass, int],
        failure_ttl_seconds: int,
    ) -> None:
        self._wrapped = wrapped
        self._redis = redis
        self._prefix = key_prefix
        self._ttl_by_class = ttl_by_class
        self._failure_ttl_seconds = failure_ttl_seconds
        # Plain attributes (not properties) — `SourceAdapter`'s Protocol declares these mutable,
        # which makes pyright treat them invariantly; a `property` isn't assignable to that.
        # Mirroring the wrapped adapter's values once at construction is equivalent (adapters
        # never mutate these after init) and keeps this a true structural drop-in.
        self.source_id = wrapped.source_id
        self.timing_class = wrapped.timing_class

    # -- SourceAdapter protocol -----------------------------------------------------------

    def supports(self) -> set[tuple[Capability, Market]]:
        return self._wrapped.supports()

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return self._wrapped.validate(raw)

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        key = _cache_key(self._prefix, cap, sym.market, sym, params)
        try:
            cached = await self._redis.get(key)
        except RedisError:
            # Redis being unreachable/slow is a cache-tier outage, not a request failure — the
            # whole point of this wrapper is to sit in front of a real source, so a broken cache
            # degrades to "always miss" (a direct live fetch, no single-flight lock since that
            # itself needs Redis) rather than propagating as a 500 to every caller. Confirmed real
            # incident: an unguarded `redis.get()` here turned a few seconds of Redis connection
            # jitter into unhandled `TimeoutError`s surfacing as 500s on every route that happened
            # to poll during the window, while routes lucky enough to hit an already-warm
            # connection kept working — same code path, outcome purely down to timing.
            logger.warning("cache_unavailable_falling_through_to_live_fetch", cap=cap.value)
            return await self._wrapped.fetch(cap, sym, tls_policy=tls_policy, **params)
        if cached is not None:
            return _deserialize(cached)
        try:
            return await self._fetch_with_single_flight(
                key, cap, sym, tls_policy=tls_policy, **params
            )
        except RedisError:
            logger.warning("cache_unavailable_falling_through_to_live_fetch", cap=cap.value)
            return await self._wrapped.fetch(cap, sym, tls_policy=tls_policy, **params)

    # -- single-flight-on-miss -------------------------------------------------------------

    def _lock_key(self, key: str) -> str:
        return f"{key}:lock"

    async def _fetch_with_single_flight(
        self,
        key: str,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        lock_key = self._lock_key(key)
        token = secrets.token_hex(16)
        # `SET NX EX` is the atomic acquire: only the first miss for this key gets `True`; every
        # concurrent miss gets `False` and falls into the poll-and-wait loop below instead of
        # each hitting the source — a hot symbol costs one upstream fetch, not N.
        lock_ttl = int(_LOCK_WAIT_TIMEOUT_SECONDS)
        acquired = await self._redis.set(lock_key, token, nx=True, ex=lock_ttl)
        if not acquired:
            waited = await self._await_concurrent_fetch(key)
            if waited is not None:
                return waited
            # The lock-holder never wrote a result within the wait budget (e.g. it crashed
            # mid-fetch) — fetch ourselves rather than block forever on a dead lock.
            return await self._fetch_and_store(key, cap, sym, tls_policy=tls_policy, **params)
        try:
            return await self._fetch_and_store(key, cap, sym, tls_policy=tls_policy, **params)
        finally:
            await self._release_lock(lock_key, token)

    async def _await_concurrent_fetch(self, key: str) -> SourceResult | None:
        deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(_LOCK_POLL_INTERVAL_SECONDS)
            cached = await self._redis.get(key)
            if cached is not None:
                return _deserialize(cached)
        return None

    async def _release_lock(self, lock_key: str, token: str) -> None:
        # Only release a lock this call actually holds (compare-then-delete via the token) —
        # never clear another waiter's freshly-acquired lock after our own `ex` expired us out.
        current = await self._redis.get(lock_key)
        current_str = current.decode() if isinstance(current, bytes) else current
        if current_str == token:
            await self._redis.delete(lock_key)

    async def _fetch_and_store(
        self,
        key: str,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        result = await self._wrapped.fetch(cap, sym, tls_policy=tls_policy, **params)
        if result.health.status is HealthStatus.OK:
            ttl = self._ttl_by_class.get(
                result.timing_class, self._ttl_by_class[TimingClass.REFERENCE]
            )
        else:
            # A failed/unreachable result is cached only briefly — long enough to absorb a
            # thundering-herd retry, short enough that the next real request re-tries the
            # source instead of replaying the same failure for a full timing_class TTL.
            ttl = self._failure_ttl_seconds
        await self._redis.set(key, _serialize(result), ex=ttl)
        return result


def build_caching_adapter(
    wrapped: SourceAdapter, redis: Redis, settings: Settings
) -> CachingSourceAdapter:
    """Construct the cache wrapper from config — the same "config selects the implementation"
    pattern `event_bus_backend`/`rate_limiter_backend` use elsewhere."""
    return CachingSourceAdapter(
        wrapped,
        redis,
        key_prefix=settings.market_data_cache_key_prefix,
        ttl_by_class={
            TimingClass.REFERENCE: settings.market_data_cache_ttl_reference_seconds,
            TimingClass.LIVE_ISH: settings.market_data_cache_ttl_live_ish_seconds,
            TimingClass.OFFICIAL_EOD: settings.market_data_cache_ttl_official_eod_seconds,
            TimingClass.EVENT_DRIVEN: settings.market_data_cache_ttl_event_driven_seconds,
        },
        failure_ttl_seconds=settings.market_data_cache_ttl_failure_seconds,
    )


__all__ = ["CachingSourceAdapter", "build_caching_adapter"]
