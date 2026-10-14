"""The FinMind Sponsor pool reserver — the real `QuotaReserver` behind the orchestrator's
pre-flight fan-out reservation.

A single Sponsor token is a hard ceiling of a fixed number of requests per rolling window, pooled
across ALL paid users — a *global* shared budget, not a per-user one. Before a `subagent`/`team`
fan-out spends Sponsor-tier tool calls, the orchestrator reserves the fan-out's expected FinMind
cost against this pool; lanes that fit are granted, lanes that would overrun the pool are degraded
to a cached/`unavailable_tier` posture before they spawn, so a fan-out never fires on budget the
pool does not have.

Concurrency: the reservation is a reserve-before-act atomic decrement, not a read-then-decide.
`reserve` does a single atomic `INCRBY` of the used-count (the `MULTI`/`EXEC` also sets the window
TTL on a fresh key), then grants only up to whatever headroom that increment revealed and
immediately refunds any over-claim with a compensating `DECRBY`. So two concurrent fan-outs
racing at the pool boundary can never both be granted past the ceiling — each sees the other's
increment. `EVAL`/Lua would express the clamp in one server-side step, but the default test
profile's `fakeredis` has no Lua interpreter while `INCRBY`/`DECRBY` are fully emulated and each
individually atomic — so the increment-then-refund form is both correct in prod and real-testable.

A Free user (FinMind class Free) never touches this pool — its skills use the free FinMind token,
so `reserve` grants every lane without decrementing the Sponsor budget.
"""

from __future__ import annotations

import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config.settings import Settings
from orchestrator.ceilings import QuotaReservation, QuotaReserver

logger = structlog.get_logger(__name__)

# Only these data-licensing tiers draw on the shared Sponsor pool (Free uses its own free token).
_SPONSOR_TIERS = frozenset({"premium", "pro"})


class RedisFinMindPoolReserver:
    """`QuotaReserver` backed by the global Redis Sponsor-pool budget. Constructed once and shared
    across turns; the injected `Redis` client is the same one the rest of the infra layer uses."""

    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str,
        limit_per_window: int,
        window_seconds: int,
        cost_per_lane: int,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._limit = limit_per_window
        self._window_seconds = window_seconds
        self._cost_per_lane = cost_per_lane

    def _key(self, now: float) -> str:
        bucket = int(now // self._window_seconds)
        return f"{self._prefix}:{bucket}"

    async def reserve(self, *, skills: tuple[str, ...], tier: str) -> QuotaReservation:
        if not skills or tier.lower() not in _SPONSOR_TIERS:
            return QuotaReservation(granted=skills, degraded=())

        want = len(skills) * self._cost_per_lane
        now = time.time()
        key = self._key(now)

        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incrby(key, want)
                pipe.expire(key, self._window_seconds + 1)
                results = await pipe.execute()
        except (RedisError, OSError) as exc:
            # The pool cannot count while Redis is down. Granting keeps paid turns working; the
            # provider's own cap still applies, and a call over it comes back as an unavailable
            # source (402), not an error. Refusing would degrade every paid turn for the outage.
            logger.warning("finmind_pool_unavailable_granting", error=repr(exc))
            return QuotaReservation(granted=skills, degraded=())
        used_after = int(results[0])

        overage = used_after - self._limit
        if overage <= 0:
            return QuotaReservation(granted=skills, degraded=())

        # Refund the part of this claim that overran the ceiling, then degrade exactly that many
        # lanes — never permanently burning budget the fan-out won't be allowed to spend.
        degraded_lanes = min(len(skills), -(-overage // self._cost_per_lane))
        refund = degraded_lanes * self._cost_per_lane
        if refund > 0:
            await self._redis.decrby(key, refund)
        if degraded_lanes >= len(skills):
            logger.info("finmind_pool_exhausted", requested=len(skills), tier=tier)
            return QuotaReservation(granted=(), degraded=skills)
        granted = skills[: len(skills) - degraded_lanes]
        degraded = skills[len(skills) - degraded_lanes :]
        logger.info("finmind_pool_partial", granted=len(granted), degraded=len(degraded), tier=tier)
        return QuotaReservation(granted=granted, degraded=degraded)

    async def refund(self, *, lanes: int) -> None:
        """Return unspent budget for lanes that ultimately consumed less Sponsor quota than
        reserved (or failed entirely). Best-effort: a refund that races the window rollover simply
        credits the current bucket, which is harmless (it can only *loosen* a throttle, never
        tighten it past the true spend)."""
        credit = lanes * self._cost_per_lane
        if credit <= 0:
            return
        try:
            await self._redis.decrby(self._key(time.time()), credit)
        except (RedisError, OSError):
            logger.warning("finmind_pool_refund_failed", lanes=lanes)


def build_quota_reserver(settings: Settings, redis: Redis | None = None) -> QuotaReserver | None:
    """Select the FinMind reserver from config. Returns the Redis pool reserver when the rate/quota
    backend is Redis (injecting the shared client; tests pass fakeredis), else `None` so the
    caller keeps the permissive `NoQuotaReserver` default — the same config-selected pattern the
    event-bus factory uses, never a hardcoded choice at the construction site."""
    if settings.rate_limiter_backend != "redis":
        return None
    timeout = settings.redis_request_timeout_seconds
    client: Redis = (
        redis
        if redis is not None
        else Redis.from_url(
            settings.redis_url, socket_timeout=timeout, socket_connect_timeout=timeout
        )
    )
    return RedisFinMindPoolReserver(
        client,
        key_prefix=settings.finmind_pool_key_prefix,
        limit_per_window=settings.finmind_pool_limit_per_window,
        window_seconds=settings.finmind_pool_window_seconds,
        cost_per_lane=settings.finmind_reserve_per_lane,
    )


__all__ = ["RedisFinMindPoolReserver", "build_quota_reserver"]
