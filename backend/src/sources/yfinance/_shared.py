"""Shared building blocks used across every yfinance capability-domain module.

Module-wide constants describing what this adapter serves per market, the one-time timezone
cache initializer, the Yahoo-symbol resolver, the typed coercion helpers that isolate
yfinance's dynamically-shaped pandas/dict return values at a single narrow edge, the
date/datetime ISO-string helpers, and the `SourceResult` envelope constructor every handler
builds its response through.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from config.settings import get_settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import SourceHealth, SourceResult

_CAVEAT = (
    "structured third-party market data — reference-only, attributed content, not our "
    "recommendation; unofficial API, coverage varies by symbol/market; never called "
    "synchronously on the request hot path in production (batch-fetched for the covered "
    "symbol list; per-symbol calls here run in a threadpool for isolated fetch)."
)

# TW keeps the original narrow set — analyst/ESG/sector/AUM gap-filling only.
_TW_SUPPORTED = {
    Capability.ANALYST_CONSENSUS,
    Capability.ESG,
    Capability.SECTOR_CLASS,
    Capability.ETF_AUM,
}

# Non-TW markets: yfinance is primary for the shared capabilities other adapters also serve,
# plus every yfinance-native capability with no FinMind/gov equivalent.
_NON_TW_SUPPORTED = _TW_SUPPORTED | {
    Capability.PRICE_EOD,
    Capability.FINANCIALS,
    Capability.BALANCE_SHEET,
    Capability.CASH_FLOW_STATEMENT,
    Capability.DIVIDENDS,
    Capability.SPLIT_PRICE,
    Capability.OPTIONS_CHAIN,
    Capability.INSTITUTIONAL_HOLDERS,
    Capability.INSIDER_ACTIVITY,
    Capability.SYMBOL_SEARCH,
    Capability.SYMBOL_LOOKUP,
    Capability.EARNINGS_CALENDAR,
    Capability.IPO_CALENDAR,
    Capability.ECONOMIC_CALENDAR,
    Capability.SECTOR_OVERVIEW,
    Capability.INDUSTRY_OVERVIEW,
    Capability.MARKET_STATUS,
    Capability.FUND_HOLDINGS,
}

_NON_TW_MARKETS = (Market.US, Market.GB, Market.JP, Market.EU)

# `yfinance.Market`'s own region taxonomy (`MarketRegion`) doesn't map 1:1 onto this codebase's
# `Market` enum (e.g. its "ASIA"/"EUROPE" regions span multiple of our markets) — this table
# picks the one representative yfinance region for each of our non-TW markets' `market_status`
# capability; it is a deliberate simplification, not a claim of taxonomic equivalence. In live
# verification, Yahoo's underlying markettime endpoint only actually returns data for `US` —
# `GB`/`ASIA`/`EUROPE` come back empty (a documented upstream gap, logged by the yfinance SDK
# itself) — the coverage gate below degrades those to `STALE` rather than fabricating a status.
_MARKET_STATUS_REGION: dict[Market, str] = {
    Market.US: "US",
    Market.GB: "GB",
    Market.JP: "ASIA",
    Market.EU: "EUROPE",
}

_tz_cache_initialized = False


def _init_tz_cache() -> None:
    """Point yfinance's timezone cache at a writable, persistent directory, once, at first
    use. The library's own default path can be read-only/ephemeral in a container (permission
    error, or a re-fetch every process start) — this must happen before the first `Ticker`/
    `download` call, but importing this module must stay cheap, so it's deferred to first use
    rather than done at import time."""
    # One-time process-lifetime init, not shared mutable request state.
    global _tz_cache_initialized  # noqa: PLW0603
    if _tz_cache_initialized:
        return
    # Lazy import — see `_fetch_info_blocking`'s own comment for why.
    import yfinance as yf  # noqa: PLC0415

    settings = get_settings()
    yf.set_tz_cache_location(settings.yf_tz_cache_dir)
    _tz_cache_initialized = True


def _yahoo_symbol(sym: Symbol) -> str:
    if sym.yahoo:
        return sym.yahoo
    if sym.market is Market.TW:
        return f"{sym.id}.TW"
    return sym.id


def _get(mapping: object, key: str, default: object = None) -> object:
    """Typed `.get()` over a dynamically-shaped 3rd-party row (a pandas `Series`, or a plain
    `dict` from `Ticker.get_info()`/`Search`/`Lookup`) — one narrow cast site so every call
    site below stays a plain `object`, never `Unknown`, without repeating a cast per line."""
    getter = cast("Callable[[str, object], object]", mapping.get)  # type: ignore[attr-defined]
    return getter(key, default)


def _as_float(value: object) -> float | None:
    """Coerce an untyped upstream pandas/numpy scalar to a plain float, treating NaN/NaT/None
    as missing rather than as zero — this is the isolated 3rd-party edge for yfinance's
    dynamically-shaped `DataFrame`/`Series` values."""
    if value is None:
        return None
    try:
        if pd.isna(cast("Any", value)):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    as_float = _as_float(value)
    return None if as_float is None else int(as_float)


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(cast("Any", value)):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _as_date(value: object) -> date | None:
    """Coerce a pandas `Timestamp`/date-like/ISO-string value to a plain `date`."""
    if value is None:
        return None
    try:
        if pd.isna(cast("Any", value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    ts = pd.Timestamp(cast("Any", value))
    if pd.isna(ts):
        return None
    dt = cast("datetime", ts.to_pydatetime())
    return dt.date()


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        if pd.isna(cast("Any", value)):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.Timestamp(cast("Any", value))
    if pd.isna(ts):
        return None
    dt = cast("datetime", ts.to_pydatetime())
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _dt_iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _dt_isoformat(value: object) -> str | None:
    dt = _as_datetime(value)
    return dt.isoformat() if dt else None


def _isoformat_datetimes(row: dict[str, object]) -> dict[str, object]:
    """Payload dicts must be JSON/pydantic-friendly plain values — convert any raw `datetime`
    field this module built up (via `_as_datetime`) to an ISO string before it leaves the
    adapter."""
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id="YFINANCE",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = [
    "_CAVEAT",
    "_MARKET_STATUS_REGION",
    "_NON_TW_MARKETS",
    "_NON_TW_SUPPORTED",
    "_TW_SUPPORTED",
    "_as_date",
    "_as_datetime",
    "_as_float",
    "_as_int",
    "_as_str",
    "_dt_iso",
    "_dt_isoformat",
    "_get",
    "_init_tz_cache",
    "_isoformat_datetimes",
    "_result",
    "_yahoo_symbol",
]
