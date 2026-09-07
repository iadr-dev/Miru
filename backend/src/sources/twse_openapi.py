"""TWSE OpenAPI adapter — official listed-market EOD, latest snapshot only, no auth, no params.

The upstream endpoint (`STOCK_DAY_ALL`) has no query parameters — it dumps the whole current
day's listed-market EOD table in one response. This adapter still serves **one symbol per
`fetch()` call** (never a caller-facing whole-market method) — it makes exactly one upstream
GET and filters the response to the requested symbol client-side. There is no history: the
FinMind adapter is the backfill engine.

Also home to two ingest-only whole-market fetch functions (`fetch_whole_market_attention_stocks`/
`fetch_whole_market_disposition_events`) for the 處置王 board — 注意股 (attention-stock) and 處置
(disposition) announcement feeds, live-verified against `announcement/notice`/`announcement/punish`
this session. Same forward-only-no-backfill shape as `sources/mops.py`'s whole-market functions;
not `TwseOpenApiAdapter` methods since they're never router-dispatched (ingest-only, mirroring
`fetch_whole_market_material_announcements`'s own module-level-function precedent)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://openapi.twse.com.tw/v1"
_STOCK_DAY_ALL = f"{_BASE_URL}/exchangeReport/STOCK_DAY_ALL"
_ATTENTION_STOCKS = f"{_BASE_URL}/announcement/notice"
_DISPOSITION_EVENTS = f"{_BASE_URL}/announcement/punish"

_CAVEAT = (
    "TWSE OpenAPI is the official latest end-of-day snapshot only — no parameters, no "
    "backfill; it reflects the most recent published trading day, not a live quote."
)
_ATTENTION_STOCKS_CAVEAT = (
    "TWSE 注意股 (attention-stock) announcement dump is the current whole-market feed as "
    "published today — no date parameter, not a historical archive; a day's flags roll off "
    "the upstream dataset over time, so absence here is not evidence a stock was never flagged."
)
_DISPOSITION_EVENTS_CAVEAT = (
    "TWSE 處置 (disposition) announcement dump is the current whole-market feed as published "
    "today — no date parameter, not a historical archive; same forward-only shape as the "
    "attention-stock feed above."
)

_ROC_DATE_LEN = 7
_ROC_DATE_RANGE_PARTS = 2


class TwseOpenApiAdapter:
    """The `TWSE_OPENAPI` source adapter."""

    source_id = "TWSE_OPENAPI"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("TWSE STOCK_DAY_ALL response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TWSE STOCK_DAY_ALL 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TWSE STOCK_DAY_ALL row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.PRICE_EOD or sym.market is not Market.TW:
            return _unavailable(cap, sym)

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_STOCK_DAY_ALL)
                response.raise_for_status()
                body: object = response.json()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        try:
            validated = self.validate({"rows": body})
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        rows_field = validated["rows"]
        if not isinstance(rows_field, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'rows' is not a list")
        rows = cast("list[dict[str, object]]", rows_field)
        match = next((row for row in rows if row.get("Code") == sym.id), None)
        if match is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE, detail=f"symbol {sym.id} not found in today's dump"
                ),
            )

        bar: dict[str, object] = {
            "symbol": sym.id,
            "market": sym.market.value,
            "trade_date": match.get("Date"),
            "open": _to_float(match.get("OpeningPrice")),
            "high": _to_float(match.get("HighestPrice")),
            "low": _to_float(match.get("LowestPrice")),
            "close": _to_float(match.get("ClosingPrice")),
            "volume": _to_int(match.get("TradeVolume")),
            "turnover": _to_float(match.get("TradeValue")),
        }
        return _result(
            payload={"bar": bar}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
        )


def _to_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def _to_int(value: object) -> int:
    return int(_to_float(value))


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"TWSE_OPENAPI does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TwseOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


def _roc_to_iso_date(roc: str) -> date | None:
    """7-digit ROC date string (e.g. "1150625") -> Gregorian `date` (2026-06-25). Duplicated
    from `sources/mops.py`'s own private helper of the same name — both live/announcement-style
    TWSE OpenAPI feeds use the same ROC-offset convention, but `sources/mops.py` doesn't export
    its version, and each `sources/*.py` module in this package already duplicates its own small
    parsing helpers (`_to_float`/`_to_int` are duplicated verbatim between this module and
    `sources/tpex_openapi.py`) rather than reaching across modules for a private name."""
    digits = roc.strip()
    if len(digits) != _ROC_DATE_LEN or not digits.isdigit():
        return None
    year = int(digits[:-4]) + 1911
    month = digits[-4:-2]
    day = digits[-2:]
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _split_slash_roc_date_range(raw: object) -> tuple[date | None, date | None]:
    """TWSE `announcement/punish`'s `DispositionPeriod` field is two `YYY/MM/DD` ROC dates
    joined by U+FF5E (fullwidth tilde), NOT the plain ASCII `~` MOPS's own `有效轉讓期間` field
    uses — confirmed live, a real upstream formatting difference between these two TWSE OpenAPI
    feeds, not a typo. Each half is `YYY/MM/DD`, not the bare 7-digit `YYYMMDD`
    `_roc_to_iso_date` parses, so this splits on `/` too."""
    text = str(raw) if raw is not None else ""
    parts = text.split("～")  # noqa: RUF001 — the real upstream separator, U+FF5E
    if len(parts) != _ROC_DATE_RANGE_PARTS:
        return None, None
    return _parse_slash_roc_date(parts[0]), _parse_slash_roc_date(parts[1])


def _parse_slash_roc_date(raw: str) -> date | None:
    segments = raw.strip().split("/")
    if len(segments) != 3:  # noqa: PLR2004 — year/month/day, not a magic threshold to name
        return None
    year_str, month_str, day_str = segments
    if not (year_str.isdigit() and month_str.isdigit() and day_str.isdigit()):
        return None
    try:
        return date(int(year_str) + 1911, int(month_str), int(day_str))
    except ValueError:
        return None


def _map_attention_stock_row(row: dict[str, object]) -> dict[str, object] | None:
    """One raw `announcement/notice` row -> the mapped dict for `AttentionStockRow`, or `None`
    when the row is the upstream's own "no flags today" sentinel (`Code` blank — confirmed live:
    a day with zero attention-stock announcements returns one all-blank/all-zero placeholder row,
    not an empty list)."""
    symbol = str(row.get("Code", "")).strip()
    if not symbol:
        return None
    flagged_date = _roc_to_iso_date(str(row.get("Date", "")))
    if flagged_date is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "company_name": str(row.get("Name", "")),
        "flagged_date": flagged_date.isoformat(),
        "reason": str(row.get("TradingInfoForAttention", "")),
        "close": _to_float(row.get("ClosingPrice")) or None,
        "pe": _optional_float(row.get("PriceEarningRatio") or row.get("PE")),
        "security_type": "twse",
    }


def _map_disposition_event_row(row: dict[str, object]) -> dict[str, object] | None:
    """One raw `announcement/punish` row -> the mapped dict for `DispositionEventRow`, or `None`
    when the row's `DispositionPeriod` doesn't parse (both halves required on the model)."""
    symbol = str(row.get("Code", "")).strip()
    if not symbol:
        return None
    announced_date = _roc_to_iso_date(str(row.get("Date", "")))
    period_start, period_end = _split_slash_roc_date_range(row.get("DispositionPeriod"))
    if announced_date is None or period_start is None or period_end is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "company_name": str(row.get("Name", "")),
        "announced_date": announced_date.isoformat(),
        "reason": str(row.get("ReasonsOfDisposition", "")),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "measure": str(row.get("DispositionMeasures", "")),
        "detail": str(row.get("Detail", "")) or None,
        "security_type": "twse",
    }


def _optional_float(value: object) -> float | None:
    if value is None or str(value).strip().upper() == "N/A":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


async def _fetch_whole_market_rows(
    url: str, *, tls_policy: TLSPolicy
) -> tuple[list[dict[str, object]], SourceHealth | None]:
    """One GET + shape check, callable by the whole-market ingest functions below without
    constructing a `TwseOpenApiAdapter` instance (ingest-only, never router-dispatched) — mirrors
    `sources/mops.py::_fetch_whole_market_rows`'s exact shape."""
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(url)
            response.raise_for_status()
            body: object = response.json()
    except httpx.HTTPError as exc:
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
        )

    if not isinstance(body, list):
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail="TWSE OpenAPI response is not a list"
        )
    untyped_rows = cast("list[object]", body)
    for row in untyped_rows:
        if not isinstance(row, dict):
            return [], SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="TWSE OpenAPI row is not an object"
            )
    return cast("list[dict[str, object]]", untyped_rows), None


async def fetch_whole_market_attention_stocks(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every 注意股 (attention-stock) flag in today's TWSE dump — the
    ingest layer's sole legitimate caller (never a per-symbol fan-out). Rows with a blank `Code`
    (the upstream's own "no flags today" sentinel) are dropped, never mapped."""
    rows, error = await _fetch_whole_market_rows(_ATTENTION_STOCKS, tls_policy=tls_policy)
    if error is not None:
        return SourceResult(
            source_id=TwseOpenApiAdapter.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.EVENT_DRIVEN,
            fetched_at=datetime.now(UTC),
            currentness_caveat=_ATTENTION_STOCKS_CAVEAT,
            payload={"rows": []},
            health=error,
        )
    mapped_rows = [mapped for row in rows if (mapped := _map_attention_stock_row(row)) is not None]
    return SourceResult(
        source_id=TwseOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_ATTENTION_STOCKS_CAVEAT,
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def fetch_whole_market_disposition_events(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every 處置 (disposition) announcement in today's TWSE dump — same
    ingest-layer whole-market shape as `fetch_whole_market_attention_stocks` above."""
    rows, error = await _fetch_whole_market_rows(_DISPOSITION_EVENTS, tls_policy=tls_policy)
    if error is not None:
        return SourceResult(
            source_id=TwseOpenApiAdapter.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.EVENT_DRIVEN,
            fetched_at=datetime.now(UTC),
            currentness_caveat=_DISPOSITION_EVENTS_CAVEAT,
            payload={"rows": []},
            health=error,
        )
    mapped_rows = [
        mapped for row in rows if (mapped := _map_disposition_event_row(row)) is not None
    ]
    return SourceResult(
        source_id=TwseOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_DISPOSITION_EVENTS_CAVEAT,
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


__all__ = [
    "TwseOpenApiAdapter",
    "fetch_whole_market_attention_stocks",
    "fetch_whole_market_disposition_events",
]
