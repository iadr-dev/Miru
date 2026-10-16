"""US macro statistics adapter (St. Louis Fed FRED web service, API v1; v2 bulk method unverified).

Ingest-only: the nightly job (`ingest/jobs/fred.py`) is the sole caller, through
`dataaccess/fred.py`. The request path never reaches this module; the tools read the Parquet store
the job writes. Like `sources/financecalendar.py` it is a plain class (a series query has no
symbol to route), not a `(capability, symbol)` adapter.

Behaviour that matters to callers:

- **No key, no call.** Without `FRED_API_KEY` every method answers a `SourceResult` whose health is
  `UNAVAILABLE` ("FRED API key not configured") instead of sending a request the service would
  refuse with an opaque 400.
- **Rate limit.** The service allows 120 requests a minute and answers 429 beyond it. One
  token bucket shared by the whole process holds the sustained rate to 60 a minute (a burst of
  10), so several adapters, or the job's concurrent metadata fetches, share one budget.
- **Retry.** 429, 5xx and transport failures are retried with exponential backoff and jitter,
  honouring `Retry-After`. Anything else (a bad series id, a rejected key) is not retried. The
  typed `FredApiError.retryable` carries that decision; public methods turn it into a degraded
  `SourceResult` (`UNREACHABLE` when retryable, `UNAVAILABLE` when not) and never raise.
- **Missing values.** The service writes a missing observation as ".", parsed to `None`.
- **No secrets in logs.** The key travels as the `api_key` query parameter; it is never logged and
  is scrubbed from any upstream error text before it is surfaced.

Field names used below are the ones documented at fred.stlouisfed.org/docs/api/fred/ for
`series/observations`, `series`, `series/release`, `releases/dates`, `release/dates`, `release`,
`series/updates` and `series/vintagedates`. Nothing in this module was exercised against the live
service (no key was available when it was written); tests replay recorded-shape payloads.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog
from pydantic import SecretStr

from config.settings import Settings, get_settings
from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.fred_catalog import US_MACRO_CAVEAT
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_SOURCE_ID = "FRED"

NOT_CONFIGURED_DETAIL = "FRED API key not configured"

_CAVEAT = US_MACRO_CAVEAT

type _Json = dict[str, object]

# The service's own sentinels for "the whole history" real-time period.
EARLIEST_REALTIME = date(1776, 7, 4)
LATEST_REALTIME = date(9999, 12, 31)

_MISSING = "."
_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500
_UPDATES_PAGE_SIZE = 1000
_MAX_PAGES = 50

# --- v2 (UNVERIFIED) ---------------------------------------------------------------------------
# The v2 documentation page says only that the key goes "in an HTTP header" and does not name it.
# It is therefore sent as the `api_key` query parameter (what v1 accepts) and, when this constant
# is set to a header name, in that header as well. Change it here once the header is confirmed
# against the live service. Nothing on the default path depends on v2.
_V2_KEY_HEADER: str | None = None


class FredApiError(Exception):
    """One failed upstream call. `retryable` is the retry decision (429, 5xx, transport errors);
    `status_code` is `None` for a transport failure."""

    def __init__(self, message: str, *, status_code: int | None, retryable: bool) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class FredNotConfiguredError(FredApiError):
    def __init__(self) -> None:
        super().__init__(NOT_CONFIGURED_DETAIL, status_code=None, retryable=False)


class TokenBucket:
    """A token bucket whose `acquire` reserves a token synchronously (no await between reading and
    updating the balance, so concurrent tasks cannot oversubscribe it) and then sleeps off any
    deficit. Not thread-safe; it is shared by tasks on one event loop."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0 or capacity < 1:
            raise ValueError("rate_per_second must be positive and capacity at least 1")
        self._rate = rate_per_second
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()

    async def acquire(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        self._tokens -= 1.0
        if self._tokens < 0:
            await self._sleep(-self._tokens / self._rate)


# 60 requests a minute sustained (half the service's 120), in bursts of up to 10.
PROCESS_BUCKET = TokenBucket(rate_per_second=1.0, capacity=10.0)


def parse_value(raw: object) -> float | None:
    """An observation value: "." (missing), blanks and non-finite numbers are `None`."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == _MISSING:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _parse_date(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_dict(raw: object) -> _Json:
    if not isinstance(raw, dict):
        raise FredApiError("unexpected response shape", status_code=None, retryable=False)
    return cast("_Json", raw)


def _as_list(raw: object) -> list[object]:
    return cast("list[object]", raw) if isinstance(raw, list) else []


def _as_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _optional_text(raw: object) -> str | None:
    return str(raw) if raw is not None and str(raw) != "" else None


def _result(payload: _Json, health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=_SOURCE_ID,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


def _degraded(error: FredApiError) -> SourceResult:
    if isinstance(error, FredNotConfiguredError) or not error.retryable:
        status = HealthStatus.UNAVAILABLE
    else:
        status = HealthStatus.UNREACHABLE
    return _result({}, SourceHealth(status=status, detail=str(error)))


def _ok(payload: _Json, *, empty: bool = False, empty_detail: str = "no data") -> SourceResult:
    if empty:
        return _result(payload, SourceHealth(status=HealthStatus.STALE, detail=empty_detail))
    return _result(payload, SourceHealth(status=HealthStatus.OK, detail="ok"))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None  # an HTTP-date form: fall back to the computed backoff
    return seconds if seconds >= 0 else None


class FredAdapter:
    """The `FRED` source adapter. Every public method is one (paged) call family that returns a
    `SourceResult`; none raises for an upstream problem."""

    source_id = _SOURCE_ID
    timing_class = TimingClass.EVENT_DRIVEN

    def __init__(  # noqa: PLR0913 - collaborators the tests replace; all keyword-only
        self,
        settings: Settings | None = None,
        *,
        bucket: TokenBucket | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] | None = None,
        max_attempts: int = 4,
        timeout: float = 20.0,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
    ) -> None:
        resolved = settings or get_settings()
        self._api_key: SecretStr | None = resolved.fred_api_key
        self._base_url = resolved.fred_base_url.rstrip("/")
        self._bucket = bucket or PROCESS_BUCKET
        self._sleep = sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.5))  # noqa: S311 - not security
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    @property
    def is_configured(self) -> bool:
        key = self._api_key.get_secret_value().strip() if self._api_key is not None else ""
        return bool(key)

    # -- transport ----------------------------------------------------------------------------

    async def _get_json(
        self,
        url: str,
        params: dict[str, str],
        *,
        tls_policy: TLSPolicy,
        extra_headers: dict[str, str] | None = None,
    ) -> _Json:
        if not self.is_configured or self._api_key is None:
            raise FredNotConfiguredError
        key = self._api_key.get_secret_value().strip()
        query = {**params, "api_key": key, "file_type": "json"}
        last: FredApiError | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._bucket.acquire()
            retry_after: float | None = None
            try:
                async with build_async_client(
                    tls_policy=tls_policy, timeout=self._timeout, headers=extra_headers
                ) as client:
                    response = await client.get(url, params=query)
            except httpx.TransportError as exc:
                # The type name only: an httpx message can carry the request URL (and the key).
                last = FredApiError(
                    f"transport failure ({type(exc).__name__})", status_code=None, retryable=True
                )
            else:
                if response.status_code == _HTTP_OK:
                    return self._decode(response)
                retry_after = _retry_after_seconds(response)
                last = self._error_for(response, key)
            if not last.retryable or attempt == self._max_attempts:
                raise last
            delay = self._delay(attempt, retry_after)
            logger.warning(
                "fred_request_retry",
                attempt=attempt,
                status=last.status_code,
                delay_seconds=round(delay, 2),
            )
            await self._sleep(delay)
        raise last or FredApiError("no attempt made", status_code=None, retryable=False)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        backoff = min(self._backoff_cap, self._backoff_base * float(2 ** (attempt - 1)))
        if retry_after is not None:
            return min(max(retry_after, 0.0), 60.0) + self._jitter()
        return backoff + self._jitter()

    @staticmethod
    def _decode(response: httpx.Response) -> _Json:
        try:
            return _as_dict(response.json())
        except ValueError as exc:
            raise FredApiError(
                "response was not valid JSON", status_code=response.status_code, retryable=False
            ) from exc

    @staticmethod
    def _error_for(response: httpx.Response, key: str) -> FredApiError:
        status = response.status_code
        message = response.reason_phrase or "error"
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            upstream = cast("_Json", body).get("error_message")
            if isinstance(upstream, str) and upstream.strip():
                message = upstream.strip()
        message = message.replace(key, "***")
        retryable = status == _HTTP_TOO_MANY_REQUESTS or status >= _HTTP_SERVER_ERROR
        return FredApiError(f"HTTP {status}: {message}", status_code=status, retryable=retryable)

    async def _v1(
        self, path: str, params: dict[str, str], *, tls_policy: TLSPolicy
    ) -> _Json | SourceResult:
        """The decoded body, or the degraded `SourceResult` to hand straight back."""
        try:
            return await self._get_json(f"{self._base_url}{path}", params, tls_policy=tls_policy)
        except FredApiError as error:
            if not isinstance(error, FredNotConfiguredError):
                logger.warning("fred_request_failed", path=path, detail=str(error))
            return _degraded(error)

    # -- series/observations ------------------------------------------------------------------

    async def series_observations(  # noqa: PLR0913 - one keyword per documented parameter
        self,
        series_id: str,
        *,
        units: str | None = None,
        frequency: str | None = None,
        aggregation_method: str | None = None,
        observation_start: date | None = None,
        observation_end: date | None = None,
        realtime_start: date | None = None,
        realtime_end: date | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort_order: str = "asc",
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """One page of `series/observations`. `payload`: `series_id`, `units`, `count` (total
        matching), `offset`, `limit`, `observations` (`date`, `value` float or `None`,
        `realtime_start`, `realtime_end`)."""
        params: dict[str, str] = {
            "series_id": series_id,
            "offset": str(offset),
            "sort_order": sort_order,
        }
        optional = {
            "units": units,
            "frequency": frequency,
            "aggregation_method": aggregation_method,
            "observation_start": _iso(observation_start),
            "observation_end": _iso(observation_end),
            "realtime_start": _iso(realtime_start),
            "realtime_end": _iso(realtime_end),
            "limit": str(limit) if limit is not None else None,
        }
        params.update({name: value for name, value in optional.items() if value is not None})
        body = await self._v1("/series/observations", params, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        observations: list[_Json] = []
        for raw in _as_list(body.get("observations")):
            row = _as_dict(raw)
            observation_date = _parse_date(row.get("date"))
            if observation_date is None:
                continue
            observations.append(
                {
                    "date": observation_date.isoformat(),
                    "value": parse_value(row.get("value")),
                    "realtime_start": _iso(_parse_date(row.get("realtime_start"))),
                    "realtime_end": _iso(_parse_date(row.get("realtime_end"))),
                }
            )
        payload: _Json = {
            "series_id": series_id,
            "units": str(body.get("units", units or "lin")),
            "count": _as_int(body.get("count")) or len(observations),
            "offset": _as_int(body.get("offset")) or 0,
            "limit": _as_int(body.get("limit")),
            "observations": observations,
        }
        return _ok(payload, empty=not observations, empty_detail="no observations in this window")

    async def fetch_all_observations(  # noqa: PLR0913 - mirrors `series_observations`
        self,
        series_id: str,
        *,
        units: str | None = None,
        observation_start: date | None = None,
        observation_end: date | None = None,
        realtime_start: date | None = None,
        realtime_end: date | None = None,
        page_size: int = 10_000,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """Every page of `series/observations`, concatenated. A page that fails ends the walk and
        its degraded result is returned as is, so a caller never mistakes half a series for all
        of it."""
        merged: list[object] = []
        offset = 0
        last: SourceResult | None = None
        for _ in range(_MAX_PAGES):
            page = await self.series_observations(
                series_id,
                units=units,
                observation_start=observation_start,
                observation_end=observation_end,
                realtime_start=realtime_start,
                realtime_end=realtime_end,
                limit=page_size,
                offset=offset,
                tls_policy=tls_policy,
            )
            last = page
            if page.health.status not in (HealthStatus.OK, HealthStatus.STALE):
                return page
            rows = _as_list(page.payload.get("observations"))
            merged.extend(rows)
            total = _as_int(page.payload.get("count")) or 0
            offset += len(rows)
            if not rows or offset >= total:
                break
        else:
            return _result(
                {},
                SourceHealth(
                    status=HealthStatus.UNREACHABLE,
                    detail=f"{series_id}: more than {_MAX_PAGES} pages of observations",
                ),
            )
        payload: _Json = {
            "series_id": series_id,
            "units": last.payload.get("units", units or "lin") if last else units or "lin",
            "count": len(merged),
            "observations": merged,
        }
        return _ok(payload, empty=not merged, empty_detail="no observations in this window")

    # -- series metadata ----------------------------------------------------------------------

    async def series_meta(
        self, series_id: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """`series`: `payload["series"]` holds `id`, `title`, `observation_start`,
        `observation_end`, `frequency`, `frequency_short`, `units`, `units_short`,
        `seasonal_adjustment`, `seasonal_adjustment_short`, `last_updated`, `popularity`,
        `notes`."""
        body = await self._v1("/series", {"series_id": series_id}, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        entries = _as_list(body.get("seriess"))
        if not entries:
            return _ok({"series": None}, empty=True, empty_detail=f"{series_id}: series not found")
        return _ok({"series": _series_entry(_as_dict(entries[0]))})

    async def series_release(
        self, series_id: str, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """`series/release`: `payload["release"]` is the release the series belongs to (`id`,
        `name`, `link`, `press_release`) or `None`."""
        body = await self._v1("/series/release", {"series_id": series_id}, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        entries = _as_list(body.get("releases"))
        if not entries:
            return _ok({"release": None}, empty=True, empty_detail=f"{series_id}: no release")
        return _ok({"release": _release_entry(_as_dict(entries[0]))})

    async def series_metas(
        self,
        series_ids: Sequence[str],
        *,
        concurrency: int = 6,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> dict[str, SourceResult]:
        """Best-effort metadata for many series at once: `series` plus `series/release` each, a
        bounded number in flight (the shared token bucket still sets the request rate). A series
        whose metadata call fails maps to that degraded result; a failing release lookup only
        leaves `payload["release"]` as `None` (and `payload["release_detail"]` says why)."""
        gate = asyncio.Semaphore(max(1, concurrency))

        async def one(series_id: str) -> tuple[str, SourceResult]:
            async with gate:
                meta = await self.series_meta(series_id, tls_policy=tls_policy)
                if meta.health.status is not HealthStatus.OK:
                    return series_id, meta
                release = await self.series_release(series_id, tls_policy=tls_policy)
            merged: _Json = {
                **meta.payload,
                "release": release.payload.get("release"),
                "release_detail": release.health.detail,
            }
            return series_id, _ok(merged)

        pairs = await asyncio.gather(*(one(series_id) for series_id in series_ids))
        return dict(pairs)

    # -- releases -----------------------------------------------------------------------------

    async def release_dates(
        self,
        *,
        from_date: date,
        to_date: date,
        include_future: bool = True,
        page_size: int = 1000,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """`releases/dates` for every release between the two dates, paged. With `include_future`
        the service's release calendar entries that have no data yet are included (documented as
        `include_release_dates_with_no_data`). `payload["release_dates"]`: `release_id`,
        `release_name`, `date`."""
        merged: list[_Json] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            params = {
                "realtime_start": from_date.isoformat(),
                "realtime_end": to_date.isoformat(),
                "include_release_dates_with_no_data": "true" if include_future else "false",
                "order_by": "release_date",
                "sort_order": "asc",
                "limit": str(page_size),
                "offset": str(offset),
            }
            body = await self._v1("/releases/dates", params, tls_policy=tls_policy)
            if isinstance(body, SourceResult):
                return body
            rows = _as_list(body.get("release_dates"))
            merged.extend(_release_date_entry(_as_dict(raw)) for raw in rows)
            total = _as_int(body.get("count")) or 0
            offset += len(rows)
            if not rows or offset >= total:
                break
        merged = [row for row in merged if row["date"] is not None]
        return _ok(
            {"release_dates": merged},
            empty=not merged,
            empty_detail="no release dates in this window",
        )

    async def release_dates_for(  # noqa: PLR0913 - one keyword per documented parameter
        self,
        release_id: int,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        include_future: bool = True,
        sort_order: str = "asc",
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """`release/dates` for one release."""
        params = {
            "release_id": str(release_id),
            "include_release_dates_with_no_data": "true" if include_future else "false",
            "sort_order": sort_order,
        }
        if from_date is not None:
            params["realtime_start"] = from_date.isoformat()
        if to_date is not None:
            params["realtime_end"] = to_date.isoformat()
        body = await self._v1("/release/dates", params, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        rows = [
            {"release_id": release_id, "date": _iso(_parse_date(_as_dict(raw).get("date")))}
            for raw in _as_list(body.get("release_dates"))
        ]
        rows = [row for row in rows if row["date"] is not None]
        return _ok({"release_dates": rows}, empty=not rows, empty_detail="no release dates")

    async def release_info(
        self, release_id: int, *, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """`release`: `payload["release"]` has `id`, `name`, `link`, `press_release`."""
        body = await self._v1("/release", {"release_id": str(release_id)}, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        entries = _as_list(body.get("releases"))
        if not entries:
            return _ok({"release": None}, empty=True, empty_detail=f"release {release_id} unknown")
        return _ok({"release": _release_entry(_as_dict(entries[0]))})

    # -- change feeds -------------------------------------------------------------------------

    async def series_updates(  # noqa: PLR0913 - one keyword per documented parameter
        self,
        *,
        filter_value: str = "macro",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """One page of `series/updates` (series sorted by when their observations last changed).
        `start_time`/`end_time` must be given together; the documented format is YYYYMMDDHHmm and
        its time zone is not stated in the page this was written from, so the ingest job does not
        use them and relies on the default ordering instead."""
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time and end_time must be given together")
        params = {"filter_value": filter_value, "limit": str(limit), "offset": str(offset)}
        if start_time is not None and end_time is not None:
            params["start_time"] = start_time.strftime("%Y%m%d%H%M")
            params["end_time"] = end_time.strftime("%Y%m%d%H%M")
        body = await self._v1("/series/updates", params, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        series = [
            {"id": entry.get("id"), "last_updated": entry.get("last_updated")}
            for entry in (_as_dict(raw) for raw in _as_list(body.get("seriess")))
            if isinstance(entry.get("id"), str)
        ]
        return _ok(
            {"series": series, "count": _as_int(body.get("count")) or len(series)},
            empty=not series,
            empty_detail="no updates",
        )

    async def recent_series_updates(
        self, *, pages: int = 3, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        """The first `pages` pages of `series/updates` merged: `payload["series"]` maps each
        recently-updated series id to its `last_updated` text."""
        updated: dict[str, object] = {}
        for page_index in range(pages):
            page = await self.series_updates(
                offset=page_index * _UPDATES_PAGE_SIZE, tls_policy=tls_policy
            )
            if page.health.status is HealthStatus.STALE:
                break
            if page.health.status is not HealthStatus.OK:
                return page
            for entry in _as_list(page.payload.get("series")):
                row = _as_dict(entry)
                updated[str(row["id"])] = row.get("last_updated")
            if len(_as_list(page.payload.get("series"))) < _UPDATES_PAGE_SIZE:
                break
        return _ok({"series": updated}, empty=not updated, empty_detail="no updates")

    async def vintage_dates(  # noqa: PLR0913 - one keyword per documented parameter
        self,
        series_id: str,
        *,
        realtime_start: date | None = None,
        realtime_end: date | None = None,
        sort_order: str = "asc",
        limit: int = 10_000,
        offset: int = 0,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """`series/vintagedates`: the dates on which the series' data was revised or newly
        published. `payload["vintage_dates"]` is a list of ISO dates, `count` the total."""
        params = {
            "series_id": series_id,
            "sort_order": sort_order,
            "limit": str(limit),
            "offset": str(offset),
        }
        if realtime_start is not None:
            params["realtime_start"] = realtime_start.isoformat()
        if realtime_end is not None:
            params["realtime_end"] = realtime_end.isoformat()
        body = await self._v1("/series/vintagedates", params, tls_policy=tls_policy)
        if isinstance(body, SourceResult):
            return body
        dates = [
            parsed.isoformat()
            for parsed in (_parse_date(raw) for raw in _as_list(body.get("vintage_dates")))
            if parsed is not None
        ]
        return _ok(
            {"series_id": series_id, "vintage_dates": dates, "count": _as_int(body.get("count"))},
            empty=not dates,
            empty_detail="no vintage dates",
        )

    # -- v2 bulk (UNVERIFIED) -----------------------------------------------------------------

    async def bulk_release_observations(
        self,
        release_id: int,
        *,
        cursor: str | None = None,
        limit: int = 500_000,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """UNVERIFIED: one page of `GET {base}/v2/release/observations` (every series of a release
        with its observations, cursor-paged by `next_cursor`/`has_more`).

        Written from the v2 documentation page alone: the response field names (`has_more`,
        `next_cursor`, `release`, `series[].series_id/title/frequency/units/seasonal_adjustment/
        last_updated/copyright_id/notes/observations[].date/value`) are the documented ones, but
        the authentication header is not named there, so see `_V2_KEY_HEADER`. Never called by the
        ingest job; v1 `series/observations` is the default path."""
        params = {"release_id": str(release_id), "limit": str(limit), "format": "json"}
        if cursor is not None:
            params["next_cursor"] = cursor
        headers = None
        if _V2_KEY_HEADER is not None and self._api_key is not None:
            headers = {_V2_KEY_HEADER: self._api_key.get_secret_value().strip()}
        try:
            body = await self._get_json(
                f"{self._base_url}/v2/release/observations",
                params,
                tls_policy=tls_policy,
                extra_headers=headers,
            )
        except FredApiError as error:
            return _degraded(error)
        series: list[_Json] = []
        for raw in _as_list(body.get("series")):
            entry = _as_dict(raw)
            series.append(
                {
                    "series_id": entry.get("series_id"),
                    "title": entry.get("title"),
                    "frequency": entry.get("frequency"),
                    "units": entry.get("units"),
                    "seasonal_adjustment": entry.get("seasonal_adjustment"),
                    "last_updated": entry.get("last_updated"),
                    "copyright_id": entry.get("copyright_id"),
                    "notes": entry.get("notes"),
                    "observations": [
                        {
                            "date": _iso(_parse_date(o.get("date"))),
                            "value": parse_value(o.get("value")),
                        }
                        for o in (_as_dict(item) for item in _as_list(entry.get("observations")))
                    ],
                }
            )
        has_more = body.get("has_more") in (True, "true")
        next_cursor = _optional_text(body.get("next_cursor"))
        return _ok(
            {
                "release": body.get("release"),
                "series": series,
                "has_more": has_more,
                "next_cursor": next_cursor if has_more else None,
            },
            empty=not series,
            empty_detail="no series in this page",
        )


def _series_entry(raw: _Json) -> _Json:
    return {
        "id": raw.get("id"),
        "title": _optional_text(raw.get("title")),
        "observation_start": _iso(_parse_date(raw.get("observation_start"))),
        "observation_end": _iso(_parse_date(raw.get("observation_end"))),
        "frequency": _optional_text(raw.get("frequency")),
        "frequency_short": _optional_text(raw.get("frequency_short")),
        "units": _optional_text(raw.get("units")),
        "units_short": _optional_text(raw.get("units_short")),
        "seasonal_adjustment": _optional_text(raw.get("seasonal_adjustment")),
        "seasonal_adjustment_short": _optional_text(raw.get("seasonal_adjustment_short")),
        "last_updated": _optional_text(raw.get("last_updated")),
        "popularity": _as_int(raw.get("popularity")),
        "notes": _optional_text(raw.get("notes")),
    }


def _release_entry(raw: _Json) -> _Json:
    return {
        "id": _as_int(raw.get("id")),
        "name": _optional_text(raw.get("name")),
        "link": _optional_text(raw.get("link")),
        "press_release": raw.get("press_release") is True,
    }


def _release_date_entry(raw: _Json) -> _Json:
    return {
        "release_id": _as_int(raw.get("release_id")),
        "release_name": _optional_text(raw.get("release_name")),
        "date": _iso(_parse_date(raw.get("date"))),
    }


__all__ = [
    "EARLIEST_REALTIME",
    "LATEST_REALTIME",
    "NOT_CONFIGURED_DETAIL",
    "PROCESS_BUCKET",
    "FredAdapter",
    "FredApiError",
    "FredNotConfiguredError",
    "TokenBucket",
    "parse_value",
]
