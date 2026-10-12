"""A bounded retry for safe reads."""

from __future__ import annotations

import asyncio
import random

import httpx

# Transient server answers worth one more try for a read; everything else is returned as is.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_TRANSIENT_ERRORS = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)
_MAX_RETRY_AFTER_SECONDS = 5.0


class RetryingTransport(httpx.AsyncBaseTransport):
    """Retries a GET/HEAD up to `attempts - 1` times on a transient error or a 429/502/503/504.

    Backoff is exponential with full jitter, and a server's `Retry-After` is honoured up to a
    small cap, so a retry never turns into hammering. Writes are never retried: one may already
    have taken effect."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        attempts: int = 3,
        base_delay: float = 0.3,
    ) -> None:
        self._inner = inner
        self._attempts = attempts
        self._base_delay = base_delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method not in _SAFE_METHODS:
            return await self._inner.handle_async_request(request)
        for attempt in range(1, self._attempts + 1):
            last = attempt == self._attempts
            try:
                response = await self._inner.handle_async_request(request)
            except _TRANSIENT_ERRORS:
                if last:
                    raise
                await self._pause(attempt, None)
                continue
            if response.status_code in _RETRY_STATUSES and not last:
                retry_after = _retry_after(response)
                await response.aclose()
                await self._pause(attempt, retry_after)
                continue
            return response
        raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover

    async def _pause(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            delay = min(retry_after, _MAX_RETRY_AFTER_SECONDS)
        else:
            delay = random.uniform(0, self._base_delay * 2 ** (attempt - 1))  # noqa: S311 - jitter
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None


__all__ = ["RetryingTransport"]
