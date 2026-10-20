"""Discovery/market-overview capability domain — the capabilities that never fetch a
per-symbol `Ticker` frame bundle: `symbol_search`, `symbol_lookup`, `earnings_calendar`/
`ipo_calendar`/`economic_calendar`, `sector_overview`, `industry_overview`, and
`market_status`. Each has its own dedicated market-wide blocking call rather than going
through the shared `Ticker` frame fetch.
"""

from __future__ import annotations

from typing import cast

import anyio.to_thread
import pandas as pd
import structlog

from schemas.enums import Capability, Market
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._blocking import (
    _fetch_calendars_blocking,
    _fetch_industry_blocking,
    _fetch_lookup_blocking,
    _fetch_market_status_blocking,
    _fetch_search_blocking,
    _fetch_sector_blocking,
)
from ._shared import (
    _MARKET_STATUS_REGION,
    _as_date,
    _as_datetime,
    _as_float,
    _as_int,
    _as_str,
    _dt_iso,
    _dt_isoformat,
    _get,
    _isoformat_datetimes,
    _result,
)

_MARKET_WEIGHT_COLUMN = "market weight"

logger = structlog.get_logger(__name__)


async def _symbol_search(sym: Symbol, params: dict[str, object]) -> SourceResult:
    query = str(params.get("query") or sym.id)
    try:
        quotes = await anyio.to_thread.run_sync(_fetch_search_blocking, query)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_search_failed", query=query, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    if not quotes:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no search matches"),
        )
    results = [
        {
            "query": query,
            "symbol": str(_get(q, "symbol", "")),
            "exchange": str(_get(q, "exchange", "")),
            "short_name": _get(q, "shortname"),
            "long_name": _get(q, "longname"),
            "quote_type": _get(q, "quoteType"),
            "sector": _get(q, "sector"),
            "industry": _get(q, "industry"),
            "score": _as_float(_get(q, "score")),
        }
        for q in quotes
    ]
    return _result(
        payload={"results": results}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


async def _symbol_lookup(sym: Symbol, params: dict[str, object]) -> SourceResult:
    query = str(params.get("query") or sym.id)
    try:
        frame = await anyio.to_thread.run_sync(_fetch_lookup_blocking, query)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_lookup_failed", query=query, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    if frame.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no lookup matches"),
        )
    results = [
        {
            "query": query,
            "symbol": str(idx),
            "instrument_type": str(_get(row, "quoteType", "")).lower() or "unknown",
            "exchange": _as_str(_get(row, "exchange")),
            "short_name": _as_str(_get(row, "shortName")),
        }
        for idx, row in frame.iterrows()
    ]
    return _result(
        payload={"results": results}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


async def _earnings_calendar(sym: Symbol) -> SourceResult:
    return await _calendars(sym, "earnings", Capability.EARNINGS_CALENDAR)


async def _ipo_calendar(sym: Symbol) -> SourceResult:
    return await _calendars(sym, "ipo", Capability.IPO_CALENDAR)


async def _economic_calendar(sym: Symbol) -> SourceResult:
    return await _calendars(sym, "economic", Capability.ECONOMIC_CALENDAR)


async def _calendars(sym: Symbol, slice_key: str, cap: Capability) -> SourceResult:
    try:
        calendars = await anyio.to_thread.run_sync(_fetch_calendars_blocking)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_calendars_failed", capability=cap.value, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    frame = calendars.get(slice_key)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail=f"no {slice_key} events"),
        )
    rows: list[dict[str, object]]
    if cap is Capability.EARNINGS_CALENDAR:
        rows = [
            {
                "market": sym.market.value,
                "symbol": str(idx),
                "company": str(_get(row, "Company", "")),
                "event_start": _as_datetime(_get(row, "Event Start Date")),
                "timing": _as_str(_get(row, "Timing")),
                "eps_estimate": _as_float(_get(row, "EPS Estimate")),
                "reported_eps": _as_float(_get(row, "Reported EPS")),
                "surprise_pct": _as_float(_get(row, "Surprise(%)")),
            }
            for idx, row in frame.iterrows()
        ]
    elif cap is Capability.IPO_CALENDAR:
        rows = [
            {
                "market": sym.market.value,
                "symbol": str(idx),
                "company": str(_get(row, "Company", "")),
                "exchange": _as_str(_get(row, "Exchange")),
                "filing_date": _dt_iso(_as_date(_get(row, "Filing Date"))),
                "offer_date": _dt_iso(_as_date(_get(row, "Date"))),
                "price_from": _as_float(_get(row, "Price From")),
                "price_to": _as_float(_get(row, "Price To")),
                "price": _as_float(_get(row, "Price")),
                "currency": _as_str(_get(row, "Currency")),
                "shares": _as_float(_get(row, "Shares")),
                "action": _as_str(_get(row, "Action")),
            }
            for idx, row in frame.iterrows()
        ]
    else:
        rows = [
            {
                "market": Market.GLOBAL.value,
                "event": str(idx),
                "region": _as_str(_get(row, "Region")),
                "event_time": _as_datetime(_get(row, "Event Time")),
                "for_period": _as_str(_get(row, "For")),
                "actual": _as_float(_get(row, "Actual")),
                "expected": _as_float(_get(row, "Expected")),
                "last": _as_float(_get(row, "Last")),
                "revised": _as_float(_get(row, "Revised")),
            }
            for idx, row in frame.iterrows()
        ]
    rows = [_isoformat_datetimes(r) for r in rows]
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail=f"no {slice_key} events"),
        )
    return _result(payload={"rows": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


async def _sector_overview(sym: Symbol, params: dict[str, object]) -> SourceResult:
    sector_key = str(params.get("sector_key") or "")
    if not sector_key:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="sector_key param is required"),
        )
    region = str(params.get("region") or sym.market.value)
    try:
        frames = await anyio.to_thread.run_sync(_fetch_sector_blocking, sector_key, region)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_sector_failed", sector_key=sector_key, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    rows = _sector_rows(sym, sector_key, frames)
    # `stats`/`industries` are best-effort additions to the same payload — a sector aggregate
    # with real top-companies/etfs/funds data but a missing/malformed `overview`/`industries`
    # frame (should not happen live, but yfinance's own dict/DataFrame shape isn't contractually
    # guaranteed) still returns its `rows` successfully; `stats`/`industries` just come back
    # empty/`None` for the caller to disclose, never blocking the whole fetch.
    stats = _sector_overview_stats(sym, sector_key, frames.get("overview"))
    industries = _sector_industry_rows(sym, sector_key, frames.get("industries"))
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE,
                detail="sector aggregate has no top-companies/etfs/funds data",
            ),
        )
    return _result(
        payload={"rows": rows, "stats": stats, "industries": industries},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _sector_overview_stats(
    sym: Symbol, sector_key: str, overview: object
) -> dict[str, object] | None:
    """Maps `yf.Sector.overview`'s dict 1:1 into `SectorOverviewStats`'s field shape. `None`
    (not an empty dict) when the frame itself isn't a dict — the caller (`GetSectorOverviewTool`)
    treats `None` as "no stats available" rather than trying to validate a garbage payload."""
    if not isinstance(overview, dict):
        return None
    overview_dict = cast("dict[str, object]", overview)
    return {
        "market": sym.market.value,
        "sector_key": sector_key,
        "companies_count": _as_int(_get(overview_dict, "companies_count")),
        "market_cap": _as_float(_get(overview_dict, "market_cap")),
        "market_weight": _as_float(_get(overview_dict, "market_weight")),
        "employee_count": _as_int(_get(overview_dict, "employee_count")),
        "description": _as_str(_get(overview_dict, "description")),
    }


def _sector_industry_rows(
    sym: Symbol, sector_key: str, industries: object
) -> list[dict[str, object]]:
    """Maps `yf.Sector.industries` (a DataFrame indexed by industry `key`, columns `name`/
    `market weight`) into `SectorIndustryWeightRow` rows — this sector's own industry breakdown,
    the data source for a sector-industries composition treemap. A row with no resolvable
    `market weight` is dropped (never a fabricated 0, which would silently understate that
    industry's real share when a treemap sizes tiles by this field)."""
    if not isinstance(industries, pd.DataFrame) or industries.empty:
        return []
    rows: list[dict[str, object]] = []
    for industry_key, row in industries.iterrows():
        weight = _as_float(_get(row, _MARKET_WEIGHT_COLUMN))
        if weight is None:
            continue
        rows.append(
            {
                "market": sym.market.value,
                "sector_key": sector_key,
                "industry_key": str(industry_key),
                "name": str(_get(row, "name", "")),
                "market_weight": weight,
            }
        )
    return rows


def _sector_rows(
    sym: Symbol, sector_key: str, frames: dict[str, pd.DataFrame | None]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    top_companies = frames.get("top_companies")
    if isinstance(top_companies, pd.DataFrame):
        for idx, row in top_companies.iterrows():
            rows.append(
                {
                    "market": sym.market.value,
                    "sector_key": sector_key,
                    "row_kind": "company",
                    "symbol": str(idx),
                    "name": str(_get(row, "name", "")),
                    "rating": _as_str(_get(row, "rating")),
                    "market_weight": _as_float(_get(row, _MARKET_WEIGHT_COLUMN)),
                }
            )
    for row_kind, frame_key in (("etf", "top_etfs"), ("mutual_fund", "top_mutual_funds")):
        frame = frames.get(frame_key)
        if not isinstance(frame, pd.DataFrame):
            continue
        for idx, row in frame.iterrows():
            rows.append(
                {
                    "market": sym.market.value,
                    "sector_key": sector_key,
                    "row_kind": row_kind,
                    "symbol": str(idx),
                    "name": str(_get(row, "name", "")),
                    "rating": None,
                    "market_weight": None,
                }
            )
    return rows


async def _industry_overview(sym: Symbol, params: dict[str, object]) -> SourceResult:
    industry_key = str(params.get("industry_key") or "")
    if not industry_key:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="industry_key param is required"),
        )
    region = str(params.get("region") or sym.market.value)
    try:
        frames, sector_key = await anyio.to_thread.run_sync(
            _fetch_industry_blocking, industry_key, region
        )
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_industry_failed", industry_key=industry_key, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    rows: list[dict[str, object]] = []
    for row_kind, frame_key in (
        ("company", "top_companies"),
        ("growth_company", "top_growth_companies"),
        ("performing_company", "top_performing_companies"),
    ):
        frame = frames.get(frame_key)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for idx, row in frame.iterrows():
            rows.append(
                {
                    "market": sym.market.value,
                    "industry_key": industry_key,
                    "sector_key": sector_key,
                    "row_kind": row_kind,
                    "symbol": str(idx),
                    "name": str(_get(row, "name", "")),
                    "rating": _as_str(_get(row, "rating")),
                    "market_weight": _as_float(_get(row, _MARKET_WEIGHT_COLUMN)),
                    # `ytd_return` is shared by both `top_growth_companies` and
                    # `top_performing_companies`; `last_price`/`target_price` are
                    # performing-only, `growth_estimate` is growth-only — a plain `top_companies`
                    # row (row_kind="company") has none of these four columns at all, so every
                    # `_get` below returns `None` for it, never fabricated.
                    "ytd_return": _as_float(_get(row, "ytd return")),
                    "last_price": _as_float(_get(row, "last price")),
                    "target_price": _as_float(_get(row, "target price")),
                    "growth_estimate": _as_float(_get(row, "growth estimate")),
                }
            )
    if not rows:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.STALE, detail="industry aggregate has no top-companies data"
            ),
        )
    return _result(payload={"rows": rows}, health=SourceHealth(status=HealthStatus.OK, detail="ok"))


async def _market_status(sym: Symbol) -> SourceResult:
    region = _MARKET_STATUS_REGION.get(sym.market)
    if region is None:
        return _result(
            payload={},
            health=SourceHealth(
                status=HealthStatus.UNAVAILABLE_TIER,
                detail=f"no yfinance Market region mapping for {sym.market.value}",
            ),
        )
    try:
        status = await anyio.to_thread.run_sync(_fetch_market_status_blocking, region)
    except Exception as exc:  # degrade via health, never raise
        logger.warning("yfinance_market_status_failed", region=region, error=str(exc))
        return _result(
            payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
        )
    if not status:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="empty market status"),
        )
    info = {
        "market": sym.market.value,
        "region": region,
        "status": str(_get(status, "status", "")),
        "message": _as_str(_get(status, "message")),
        "open_at": _dt_isoformat(_get(status, "open")),
        "close_at": _dt_isoformat(_get(status, "close")),
    }
    return _result(
        payload={"status": info}, health=SourceHealth(status=HealthStatus.OK, detail="ok")
    )


__all__ = [
    "_calendars",
    "_earnings_calendar",
    "_economic_calendar",
    "_industry_overview",
    "_ipo_calendar",
    "_market_status",
    "_sector_overview",
    "_sector_rows",
    "_symbol_lookup",
    "_symbol_search",
]
