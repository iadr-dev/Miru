"""TPEX OpenAPI adapter — official OTC (上櫃) + emerging (興櫃) market EOD, latest snapshot
only, no auth, but requires a `User-Agent` header (upstream rejects UA-less requests).

Mirrors the TWSE OpenAPI adapter's shape: the upstream endpoint has no query parameters — it
dumps the whole current day's OTC-market EOD table in one response. This adapter still serves
**one symbol per `fetch()` call** — it makes exactly one upstream GET and filters the response
to the requested symbol client-side. There is no history here: FinMind is the backfill engine.

The endpoint path below is confirmed correct against the live server (verified with `curl`: a
real 200 with the full current-day OTC dump). **Formerly known issue (now resolved, re-verified
live this session): TLS-strict verification used to fail for this server under Python's `ssl`
module / OpenSSL 3.x** — the upstream's certificate chain was missing a Subject Key Identifier
extension, which OpenSSL 3.x's stricter X.509 validation rejected
(`CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier`) even though Windows' native TLS
stack (and therefore `curl` on Windows) tolerated the same certificate. `tpex_mainboard_daily_
close_quotes` and the sibling 注意股/處置 endpoints below (`tpex_trading_warning_information`/
`tpex_disposal_information`) all now succeed under `TLSPolicy.STRICT` with no certificate error
— the upstream appears to have fixed its certificate chain since this was first documented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.parse import optional_float, to_float
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://www.tpex.org.tw/openapi/v1"
_MAINBOARD_DAILY_CLOSE_QUOTES = f"{_BASE_URL}/tpex_mainboard_daily_close_quotes"
_ATTENTION_STOCKS = f"{_BASE_URL}/tpex_trading_warning_information"
_DISPOSITION_EVENTS = f"{_BASE_URL}/tpex_disposal_information"

# Upstream rejects requests with no User-Agent (the "needs UA header" quirk this family is
# known for) — a plain, honest UA identifying this client, not a browser-spoofing one.
_HEADERS = {"User-Agent": "Miro-Backend/1.0 (+governed TW-stock/ETF context agent)"}

_CAVEAT = (
    "TPEX OpenAPI is the official latest end-of-day OTC/emerging-market snapshot only — no "
    "parameters, no backfill; it reflects the most recent published trading day, not a live "
    "quote."
)
_ATTENTION_STOCKS_CAVEAT = (
    "TPEX 注意股 (attention-stock) announcement dump is the current whole-market feed as "
    "published today — no date parameter, not a historical archive; a day's flags roll off "
    "the upstream dataset over time, so absence here is not evidence a stock was never flagged."
)
_DISPOSITION_EVENTS_CAVEAT = (
    "TPEX 處置 (disposition) announcement dump is the current whole-market feed as published "
    "today — no date parameter, not a historical archive; same forward-only shape as the "
    "attention-stock feed above."
)

_ROC_DATE_LEN = 7
_ROC_DATE_RANGE_PARTS = 2


class TpexOpenApiAdapter:
    """The `TPEX_OPENAPI` source adapter."""

    source_id = "TPEX_OPENAPI"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.PRICE_EOD, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("TPEX mainboard daily close quotes response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("TPEX mainboard daily close quotes 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("TPEX mainboard daily close quotes row is not an object")
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
            async with build_async_client(tls_policy=tls_policy, headers=_HEADERS) as client:
                response = await client.get(_MAINBOARD_DAILY_CLOSE_QUOTES)
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
        match = next((row for row in rows if row.get("SecuritiesCompanyCode") == sym.id), None)
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
            "open": to_float(match.get("Open")),
            "high": to_float(match.get("High")),
            "low": to_float(match.get("Low")),
            "close": to_float(match.get("Close")),
            "volume": _to_int(match.get("TradingShares")),
            "turnover": to_float(match.get("TransactionAmount")),
        }
        return _result(
            payload={"bar": bar}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
        )


def _to_int(value: object) -> int:
    return int(to_float(value))


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"TPEX_OPENAPI does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=TpexOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


def _roc_to_iso_date(roc: str) -> date | None:
    """7-digit ROC date string (e.g. "1150625") -> Gregorian `date` (2026-06-25). Duplicated
    from `sources/mops.py`'s own private helper — see `sources/twse_openapi.py`'s identical
    duplication for why (each `sources/*.py` module already duplicates its own small parsers)."""
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


def _split_roc_date_range(raw: object) -> tuple[date | None, date | None]:
    """TPEX `tpex_disposal_information`'s `DispositionPeriod` field is `"1150907~1150915"` — two
    bare 7-digit ROC dates joined by a plain ASCII `~`, the same shape MOPS's own
    `有效轉讓期間` field uses (unlike TWSE OpenAPI's `announcement/punish`, which uses
    slashed dates joined by a fullwidth tilde; see
    `sources/twse_openapi.py::_split_slash_roc_date_range` for that variant)."""
    text = str(raw) if raw is not None else ""
    parts = text.split("~")
    if len(parts) != _ROC_DATE_RANGE_PARTS:
        return None, None
    return _roc_to_iso_date(parts[0]), _roc_to_iso_date(parts[1])


def _map_attention_stock_row(row: dict[str, object]) -> dict[str, object] | None:
    """One raw `tpex_trading_warning_information` row -> the mapped dict for `AttentionStockRow`,
    or `None` when the row is missing its symbol/date (both required fields on that model)."""
    symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
    if not symbol:
        return None
    flagged_date = _roc_to_iso_date(str(row.get("Date", "")))
    if flagged_date is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "company_name": str(row.get("CompanyName", "")),
        "flagged_date": flagged_date.isoformat(),
        "reason": str(row.get("TradingInformation", "")),
        "close": optional_float(row.get("ClosePrice")),
        "pe": optional_float(row.get("PriceEarningRatio")),
        "security_type": "tpex",
    }


def _map_disposition_event_row(row: dict[str, object]) -> dict[str, object] | None:
    """One raw `tpex_disposal_information` row -> the mapped dict for `DispositionEventRow`, or
    `None` when the row's `DispositionPeriod` doesn't parse (both halves required on the model).
    This dataset has no separate `NumberOfAnnouncement`/`DispositionMeasures` split the way
    TWSE's `announcement/punish` does — `measure` carries `DisposalCondition`'s own free text."""
    symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
    if not symbol:
        return None
    announced_date = _roc_to_iso_date(str(row.get("Date", "")))
    period_start, period_end = _split_roc_date_range(row.get("DispositionPeriod"))
    if announced_date is None or period_start is None or period_end is None:
        return None
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "company_name": str(row.get("CompanyName", "")),
        "announced_date": announced_date.isoformat(),
        "reason": str(row.get("DispositionReasons", "")),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "measure": str(row.get("DisposalCondition", "")),
        "detail": None,
        "security_type": "tpex",
    }


async def _fetch_whole_market_rows(
    url: str, *, tls_policy: TLSPolicy
) -> tuple[list[dict[str, object]], SourceHealth | None]:
    """One GET + shape check, callable by the whole-market ingest functions below without
    constructing a `TpexOpenApiAdapter` instance (ingest-only, never router-dispatched)."""
    try:
        async with build_async_client(tls_policy=tls_policy, headers=_HEADERS) as client:
            response = await client.get(url)
            response.raise_for_status()
            body: object = response.json()
    except httpx.HTTPError as exc:
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
        )

    if not isinstance(body, list):
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail="TPEX OpenAPI response is not a list"
        )
    untyped_rows = cast("list[object]", body)
    for row in untyped_rows:
        if not isinstance(row, dict):
            return [], SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="TPEX OpenAPI row is not an object"
            )
    return cast("list[dict[str, object]]", untyped_rows), None


async def fetch_whole_market_attention_stocks_tpex(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every 注意股 (attention-stock) flag in today's TPEX dump — the
    ingest layer's sole legitimate caller. Distinct symbol namespace/field shape from
    `sources/twse_openapi.py::fetch_whole_market_attention_stocks`; the ingest job that calls both
    concatenates their rows into one `attention_stocks` partition, distinguished by
    `security_type`."""
    rows, error = await _fetch_whole_market_rows(_ATTENTION_STOCKS, tls_policy=tls_policy)
    if error is not None:
        return SourceResult(
            source_id=TpexOpenApiAdapter.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.EVENT_DRIVEN,
            fetched_at=datetime.now(UTC),
            currentness_caveat=_ATTENTION_STOCKS_CAVEAT,
            payload={"rows": []},
            health=error,
        )
    mapped_rows = [mapped for row in rows if (mapped := _map_attention_stock_row(row)) is not None]
    return SourceResult(
        source_id=TpexOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_ATTENTION_STOCKS_CAVEAT,
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def fetch_whole_market_disposition_events_tpex(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every 處置 (disposition) announcement in today's TPEX dump — same
    ingest-layer whole-market shape as `fetch_whole_market_attention_stocks_tpex` above."""
    rows, error = await _fetch_whole_market_rows(_DISPOSITION_EVENTS, tls_policy=tls_policy)
    if error is not None:
        return SourceResult(
            source_id=TpexOpenApiAdapter.source_id,
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
        source_id=TpexOpenApiAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_DISPOSITION_EVENTS_CAVEAT,
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


__all__ = [
    "TpexOpenApiAdapter",
    "fetch_whole_market_attention_stocks_tpex",
    "fetch_whole_market_disposition_events_tpex",
]
