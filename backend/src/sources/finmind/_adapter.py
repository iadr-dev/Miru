"""FinMind adapter — the history/backfill engine.

The only token-bearing adapter. Wraps `GET /data` (and the special
`taiwan_stock_trading_daily_report` endpoint for `chip_broker`).
Bearer token comes from `Settings().finmind_api_key` — never
logged, never returned in any payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import structlog

from config.settings import Settings, get_settings
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._registry import _DATASETS
from ._shared import _DatasetConfig, _result

logger = structlog.get_logger(__name__)

_DATA_ENDPOINT = "/data"
_HTTP_OK = 200
_HTTP_PAYMENT_REQUIRED = 402  # FinMind's rate-limit-exceeded status

# FinMind's `/data` endpoint 400s outright when `start_date` (or `date`, for the day-scoped
# endpoints) is omitted — it is not truly optional upstream even though callers may reasonably
# not name a range (a tool's `DateRangeInput.start_date` is `None`-able so the LLM isn't forced
# to guess one). Rather than push a mandatory range onto every tool/input model, the adapter
# fills in a sane default here, once, for every dataset this package serves — sized per-dataset
# via `_DatasetConfig.default_lookback_days`, since a quarterly-filed dataset and a daily-EOD one
# need very different "how far back" windows to reliably contain at least one real row.
_DEFAULT_SINGLE_DAY_LOOKBACK_DAYS = 1


def _default_date_range(config: _DatasetConfig) -> tuple[str, str]:
    today = datetime.now(UTC).date()
    return (today - timedelta(days=config.default_lookback_days)).isoformat(), today.isoformat()


def _default_single_date() -> str:
    lookback = timedelta(days=_DEFAULT_SINGLE_DAY_LOOKBACK_DAYS)
    return (datetime.now(UTC).date() - lookback).isoformat()


def _build_query(
    config: _DatasetConfig, *, data_id: str | None, params: dict[str, object]
) -> dict[str, str]:
    """The `dataset`/`data_id`/date-range query params for one `/data` call — pulled out of
    `FinMindAdapter._call` purely to keep that method's branching shallow; identical logic,
    just named."""
    query: dict[str, str] = {"dataset": config.dataset}
    if data_id is not None:
        query["data_id"] = data_id
    if config.no_start_date_param:
        return query
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    if config.date_param == "date":
        date_value = params.get("date") or start_date
        query["date"] = str(date_value) if date_value is not None else _default_single_date()
    else:
        if start_date is None:
            start_date, default_end = _default_date_range(config)
            if end_date is None and not config.no_end_date_param:
                end_date = default_end
        query["start_date"] = str(start_date)
        if end_date is not None and not config.no_end_date_param:
            query["end_date"] = str(end_date)
    return query


class FinMindAdapter:
    """The `FINMIND` source adapter."""

    source_id = "FINMIND"
    timing_class = TimingClass.OFFICIAL_EOD  # per-dataset override happens in fetch()

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(cap, config.market) for cap, config in _DATASETS.items()}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        """Validate the FinMind `/data` envelope shape. Raises `ValueError` on violation."""
        if "data" not in raw:
            raise ValueError("FinMind response missing 'data' key")
        data = raw["data"]
        if not isinstance(data, list):
            raise ValueError("FinMind 'data' field is not a list")
        rows = cast("list[object]", data)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("FinMind data row is not an object")
        status = raw.get("status")
        if status is not None and status != _HTTP_OK:
            raise ValueError(f"FinMind response status={status} msg={raw.get('msg')!r}")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        config = _DATASETS.get(cap)
        if config is None or sym.market is not config.market:
            return _result(
                data_class=DataClass.PUBLIC,
                timing_class=TimingClass.REFERENCE,
                caveat=f"FinMind does not serve capability {cap.value} for market "
                f"{sym.market.value}.",
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail="capability not declared by this adapter for this market",
                ),
            )

        # A `no_data_id_param` dataset's upstream endpoint 400s if `data_id` is present at all,
        # even on this per-symbol path — the request goes out whole-market and the row_mapper's
        # own `sym` argument (still passed) attributes rows to the caller's symbol instead.
        data_id = None if config.no_data_id_param else (sym.finmind or sym.id)
        return await self._call(config, data_id=data_id, sym=sym, tls_policy=tls_policy, **params)

    async def fetch_whole_market(
        self,
        cap: Capability,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        """Whole-market-by-date (omit `data_id`) — **the ingest layer's sole legitimate
        caller**, using FinMind's documented omit-data_id-for-the-whole-table form. Requires
        >= Backer; every other layer must stay per-symbol.

        Not part of `SourceAdapter` — deliberately not reachable through `dataaccess/router.py`.
        No full-market scan may happen anywhere except this one carve-out; ingest is the
        *only* whole-market path. Only `ingest/` may call this method.
        """
        config = _DATASETS.get(cap)
        if config is None or not config.whole_market_ok:
            return _result(
                data_class=DataClass.PUBLIC,
                timing_class=TimingClass.REFERENCE,
                caveat=f"FinMind does not document a whole-market-by-date form for "
                f"capability {cap.value}.",
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail="dataset not marked whole_market_ok",
                ),
            )
        return await self._call(config, data_id=None, sym=None, tls_policy=tls_policy, **params)

    def _error_result(self, config: _DatasetConfig, *, detail: str) -> SourceResult:
        return _result(
            data_class=config.data_class,
            timing_class=config.timing_class,
            caveat=config.caveat,
            payload={},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=detail),
        )

    async def _fetch_raw(
        self, config: _DatasetConfig, *, query: dict[str, str], tls_policy: TLSPolicy
    ) -> object | SourceResult:
        """The HTTP round trip + its two error mappings, pulled out of `_call` purely to keep
        that method's branching shallow. Returns the parsed JSON body on success, or an
        already-built error `SourceResult` the caller should return as-is."""
        base_url = self._settings.finmind_base_url.rstrip("/")
        endpoint = config.special_endpoint or _DATA_ENDPOINT
        url = f"{base_url}{endpoint}"
        headers: dict[str, str] = {}
        token = self._settings.finmind_api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"  # never logged

        # Resolved through the package namespace (not a module-level import) so that tests can
        # monkeypatch the public `sources.finmind.build_async_client` re-export and have that
        # substitution observed here. A deferred import here also sidesteps the circular
        # import that a module-level `import sources.finmind` would create (this module is
        # itself imported by `sources/finmind/__init__.py`).
        import sources.finmind as _finmind_pkg  # noqa: PLC0415

        try:
            async with _finmind_pkg.build_async_client(
                tls_policy=tls_policy, headers=headers
            ) as client:
                response = await client.get(url, params=query)
                response.raise_for_status()
                return cast("object", response.json())
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = (
                "FinMind rate limit exceeded (402)"
                if status_code == _HTTP_PAYMENT_REQUIRED
                else (f"FinMind HTTP {status_code}")
            )
            return self._error_result(config, detail=detail)
        except httpx.HTTPError as exc:
            return self._error_result(config, detail=f"transport error: {type(exc).__name__}")

    async def _call(
        self,
        config: _DatasetConfig,
        *,
        data_id: str | None,
        sym: Symbol | None,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        query = _build_query(config, data_id=data_id, params=params)
        raw = await self._fetch_raw(config, query=query, tls_policy=tls_policy)
        if isinstance(raw, SourceResult):
            return raw
        if not isinstance(raw, dict):
            return self._error_result(config, detail="FinMind response is not an object")

        try:
            validated = self.validate(cast("dict[str, object]", raw))
        except ValueError as exc:
            return _result(
                data_class=config.data_class,
                timing_class=config.timing_class,
                caveat=config.caveat,
                payload={},
                health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc)),
            )

        raw_rows = validated["data"]
        if not isinstance(raw_rows, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'data' is not a list")
        typed_rows = cast("list[dict[str, object]]", raw_rows)
        normalized = config.row_mapper(typed_rows, sym)
        status = HealthStatus.OK if normalized else HealthStatus.STALE
        detail = "ok" if normalized else "no rows returned for the requested range"
        return _result(
            data_class=config.data_class,
            timing_class=config.timing_class,
            caveat=config.caveat,
            payload={config.row_key: normalized},
            health=SourceHealth(status=status, detail=detail),
        )


__all__ = ["FinMindAdapter"]
