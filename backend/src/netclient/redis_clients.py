"""The Redis client for calls a request waits on: the rate limiter, the market-data cache, the
FinMind quota and the readiness probe. Not for the long-lived pub/sub subscriptions, which sit idle
on purpose and must not time out.

Two limits make a Redis problem cost a bounded wait instead of an outage:

- connect and reply timeouts (`REDIS_REQUEST_TIMEOUT_SECONDS`), so a Redis that stops answering,
  rather than refusing, does not hold a request for the OS TCP timeout;
- a **blocking** pool of `REDIS_REQUEST_MAX_CONNECTIONS`, so a burst of concurrent calls waits (up
  to the same timeout) for a free connection. The default pool raises `MaxConnectionsError` at
  once past its size, which the callers read as "Redis is down": under a burst of more than 100
  concurrent calls per process they stopped sharing the rate limit and stopped counting FinMind
  calls for a few seconds at a time."""

from __future__ import annotations

from redis.asyncio import BlockingConnectionPool, Redis

from config.settings import Settings


def request_path_redis(settings: Settings) -> Redis:
    timeout = settings.redis_request_timeout_seconds
    pool = BlockingConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_request_max_connections,
        timeout=timeout,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
    )
    return Redis(connection_pool=pool)


__all__ = ["request_path_redis"]
