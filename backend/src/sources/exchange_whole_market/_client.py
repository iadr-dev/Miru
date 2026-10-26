"""The polite HTTP client the exchange reports are fetched with: a minimum gap between requests
to one host, a hard per-request timeout, a bounded retry with backoff on transient failures, and
a descriptive User-Agent.

TWSE bans an IP that sends more than about three requests in five seconds, so the gap is
enforced here, once, for every caller of one client: an `asyncio.Lock` serialises the callers and
a monotonic clock measures the spacing, without ever blocking the event loop. The clock and the
sleep are injectable so the tests run on a fake clock.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, cast

import httpx
import structlog

from schemas.enums import TLSPolicy
from sources.tls import build_async_client

from ._common import ExchangeFetchError, JsonObject

logger = structlog.get_logger(__name__)

USER_AGENT: Final = "Miru-ingest/1.0 (end-of-day market data research; at most one request per 2 s)"
_HTTP_OK: Final = 200
_HTTP_TOO_MANY: Final = 429
_HTTP_SERVER_ERROR: Final = 500
_MAX_RETRY_AFTER_SECONDS: Final = 60.0

type Clock = Callable[[], float]
type Sleep = Callable[[float], Awaitable[None]]


class MinGapThrottle:
    """Lets one request start at most every `min_gap_seconds`, however many callers wait."""

    def __init__(
        self,
        min_gap_seconds: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._min_gap = min_gap_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_start: float | None = None

    async def wait_turn(self) -> None:
        async with self._lock:
            if self._last_start is not None:
                wait = self._last_start + self._min_gap - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            self._last_start = self._clock()


@dataclass(frozen=True)
class RequestPolicy:
    """The per-request budget: a hard timeout, a bounded number of tries, the first pause
    (doubled before each further try)."""

    timeout_seconds: float = 20.0
    max_attempts: int = 3
    backoff_seconds: float = 3.0


@dataclass(frozen=True)
class _Reader[T]:
    """What to ask for (`Accept`) and how to turn the successful response into a `T`."""

    accept: str
    read: Callable[[httpx.Response], T]


class ThrottledJsonClient:
    """GET a JSON object from one host, politely."""

    def __init__(
        self,
        *,
        host: str,
        min_gap_seconds: float,
        policy: RequestPolicy = RequestPolicy(),  # noqa: B008
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._host = host
        self._timeout = policy.timeout_seconds
        self._max_attempts = policy.max_attempts
        self._backoff = policy.backoff_seconds
        self._clock = clock
        self._sleep = sleep
        self._throttle = MinGapThrottle(min_gap_seconds, clock=clock, sleep=sleep)

    async def get_json(
        self, url: str, params: dict[str, str], *, label: str, tls_policy: TLSPolicy
    ) -> JsonObject:
        """The parsed JSON object, or `ExchangeFetchError`. Every attempt waits for the throttle;
        429, 5xx, timeouts, transport errors and a non-JSON body (the block page) are retried
        with a growing pause, anything else (403, 404) is not."""
        return await self._get(
            url,
            params,
            label=label,
            tls_policy=tls_policy,
            reader=_Reader("application/json", self._read_json),
        )

    async def get_text(
        self, url: str, *, label: str, tls_policy: TLSPolicy, encoding: str = "utf-8"
    ) -> str:
        """The body of a page decoded as `encoding` (a legacy page: Big5), with the same
        throttle, timeout, retry and error rules as `get_json`. A body that does not decode is
        the block page and is retried like any other transient failure."""

        def read(response: httpx.Response) -> str:
            try:
                return response.content.decode(encoding)
            except UnicodeDecodeError as exc:
                raise _TransientError(f"response is not {encoding} text") from exc

        return await self._get(
            url, {}, label=label, tls_policy=tls_policy, reader=_Reader("text/html", read)
        )

    async def _get[T](
        self,
        url: str,
        params: dict[str, str],
        *,
        label: str,
        tls_policy: TLSPolicy,
        reader: _Reader[T],
    ) -> T:
        reason = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            await self._throttle.wait_turn()
            started = self._clock()
            retry_after: float | None = None
            try:
                body = await self._once(url, params, tls_policy, reader)
            except _TransientError as exc:
                reason = exc.reason
                retry_after = exc.retry_after
            else:
                logger.info(
                    "exchange_request",
                    host=self._host,
                    report=label,
                    attempt=attempt,
                    latency_ms=round((self._clock() - started) * 1000),
                )
                return body
            logger.warning(
                "exchange_request_retry",
                host=self._host,
                report=label,
                attempt=attempt,
                reason=reason,
            )
            if attempt < self._max_attempts:
                pause = self._backoff * 2 ** (attempt - 1)
                await self._sleep(max(pause, retry_after or 0.0))
        raise ExchangeFetchError(f"{self._host} {label}: {reason} after {self._max_attempts} tries")

    async def _once[T](
        self,
        url: str,
        params: dict[str, str],
        tls_policy: TLSPolicy,
        reader: _Reader[T],
    ) -> T:
        try:
            async with build_async_client(
                tls_policy=tls_policy,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT, "Accept": reader.accept},
            ) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise _TransientError("timeout") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"transport error {type(exc).__name__}") from exc
        status = response.status_code
        if status == _HTTP_TOO_MANY or status >= _HTTP_SERVER_ERROR:
            raise _TransientError(f"HTTP {status}", _retry_after(response))
        if status != _HTTP_OK:
            raise ExchangeFetchError(f"{self._host}: HTTP {status}")
        return reader.read(response)

    def _read_json(self, response: httpx.Response) -> JsonObject:
        try:
            body: object = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _TransientError("response is not JSON") from exc
        if not isinstance(body, dict):
            raise ExchangeFetchError(f"{self._host}: response is not a JSON object")
        return cast("JsonObject", body)


class _TransientError(Exception):
    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)
