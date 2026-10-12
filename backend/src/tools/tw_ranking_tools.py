"""`get_tw_market_rankings` — the TW-market 排行榜 card's backend: 6 tabs, none backed by a live
screener (TW has no Yahoo-style screener endpoint — `market_ranking_tools.py`'s own module
docstring: "there is no TW/GB/JP/EU equivalent screener path in this codebase today"). Every kind
here is OUR OWN aggregation over already-ingested whole-market Parquet partitions, following
`GetPriceMomentumTool`'s own read pattern (`tools/market/`): read the store directly via
`ParquetStore.read_partition`/`read_range`, never a per-symbol live fan-out and never a live
whole-market FinMind fetch at request time (`fetch_whole_market` is `ingest/`-only — the
governance "no full-market scan" rule).

The 7th tab (大戶買賣) is NOT part of this module — it reuses `get_holder_accumulation`
(`tools/market/holder_accumulation.py::GetHolderAccumulationTool`) verbatim, wired separately at
the frontend/route
layer, since that tool already has the exact Sponsor-gated shape this tab needs.

Streak tabs (dealer/trust/foreign) read `INSTITUTIONAL_FLOW_DATASET`
(`analytics/screener/batch.py`'s own constant — the same dataset that batch job's `_chip_flags`
reads, but this tool computes a signed streak LENGTH via `chip_streaks.streak_length`, not a
fixed-`min_days` boolean tag) over a `_STREAK_LOOKBACK_TRADING_DAYS`-day window, so a real streak
longer than that batch job's own `_CHIP_LOOKBACK_DAYS=12` window isn't silently truncated."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import date as date_
from enum import StrEnum
from typing import cast

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field

from analytics.screener import chip_streaks
from analytics.screener.batch import INSTITUTIONAL_FLOW_DATASET
from config.settings import get_settings
from ingest.jobs import MARGIN_SHORT_JOB, MONTH_REVENUE_JOB, STOCK_INFO_JOB, VALUATION_RATIOS_JOB
from ingest.store import ParquetStore
from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args

_MAX_TOP_N = 50
_DEFAULT_TOP_N = 10

# Wide enough that a genuinely long real streak is provable within the window — well past the
# screener-tag batch job's own `_CHIP_LOOKBACK_DAYS=12` (that window is sized for a fixed 3-day
# streak check plus a 5-day cumulative check, not for measuring an arbitrarily long streak).
_STREAK_LOOKBACK_TRADING_DAYS = 20

# Upper bound on serving a cached ranking even if the store fingerprint didn't change (an
# idempotent same-date re-write doesn't bump a directory mtime).
_RANKING_CACHE_TTL_S = 600.0

_DEALER_INVESTOR = "Dealer_self"
_INVESTMENT_TRUST_INVESTOR = "Investment_Trust"
_FOREIGN_INVESTOR_INVESTOR = "Foreign_Investor"


class TwRankingKind(StrEnum):
    """One tab of the TW-market 排行榜 card (excluding 大戶買賣, wired separately — see module
    docstring)."""

    DEALER_STREAK = "dealer_streak"  # 主力連買賣
    TRUST_STREAK = "trust_streak"  # 投信連買賣
    FOREIGN_STREAK = "foreign_streak"  # 外資連買賣
    MARGIN_CHANGE = "margin_change"  # 融資增減
    DIVIDEND_YIELD = "dividend_yield"  # 股息殖利率
    REVENUE_GROWTH = "revenue_growth"  # 月營收成長


_STREAK_INVESTOR_BY_KIND: dict[TwRankingKind, str] = {
    TwRankingKind.DEALER_STREAK: _DEALER_INVESTOR,
    TwRankingKind.TRUST_STREAK: _INVESTMENT_TRUST_INVESTOR,
    TwRankingKind.FOREIGN_STREAK: _FOREIGN_INVESTOR_INVESTOR,
}


class GetTwMarketRankingsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TwRankingKind
    top_n: int = Field(default=_DEFAULT_TOP_N, ge=1, le=_MAX_TOP_N)
    # When set, the caller wants one symbol's own row (e.g. a stock-detail signal badge asking
    # "does 2330 have a foreign-buy streak today"), not a top-N ranking — `top_n` is ignored in
    # this mode since there's nothing to truncate.
    symbol: str | None = None


class TwRankingRow(BaseModel):
    """One ranked row — the `SymbolQuoteRows`-compatible base (symbol/name/price/change/
    change_pct/volume, left `None` here since this tool doesn't itself fetch a live quote; the
    frontend enriches via `useSymbolQuotes` the same way `UsMarketRankingRows` does) plus one
    shared `extra_value`/`extra_label` pair carrying this kind's own distinct metric — a signed
    streak-day count (`dealer_streak`/`trust_streak`/`foreign_streak`), a signed margin-balance
    change (`margin_change`), or a plain percentage (`dividend_yield`/`revenue_growth`). One
    shared numeric field for all 6 kinds keeps the frontend's row-rendering path uniform."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    extra_value: float
    extra_label: str


class GetTwMarketRankingsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TwRankingKind
    trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    rows: tuple[TwRankingRow, ...] = Field(default_factory=tuple)


def _unavailable_reason(dataset_label: str) -> str:
    return (
        f"No ingested {dataset_label} partition found for TW yet — the whole-market ingest job "
        "may not have run for this dataset yet."
    )


def _latest_dataset_date(store: ParquetStore, dataset: str) -> date_ | None:
    known_dates = store.list_partition_dates(dataset, "TW")
    return max(known_dates) if known_dates else None


def _names_by_symbol(store: ParquetStore) -> dict[str, str]:
    roster_date = _latest_dataset_date(store, STOCK_INFO_JOB.dataset)
    if roster_date is None:
        return {}
    raw = store.read_partition(STOCK_INFO_JOB.dataset, "TW", roster_date)
    return {
        str(row["symbol"]): str(row["name"]) for row in raw if row.get("symbol") and row.get("name")
    }


def _streak_rows(
    store: ParquetStore,
    kind: TwRankingKind,
    names_by_symbol: dict[str, str],
    top_n: int,
    target_symbol: str | None = None,
) -> GetTwMarketRankingsOutput:
    known_dates = store.list_partition_dates(INSTITUTIONAL_FLOW_DATASET, "TW")
    if not known_dates:
        return GetTwMarketRankingsOutput(
            kind=kind, data_available=False, unavailable_reason=_unavailable_reason("investor flow")
        )
    latest_date = max(known_dates)
    # The trailing `_STREAK_LOOKBACK_TRADING_DAYS` actually-ingested dates, not a calendar-day
    # window — a holiday/weekend gap in the raw date range would otherwise under-fill the window.
    window_dates = tuple(sorted(known_dates)[-_STREAK_LOOKBACK_TRADING_DAYS:])
    # One batched, column-projected DuckDB read + a vectorised per-day net, instead of a
    # connection-per-date `read_partition` loop boxing ~20 days of whole-market rows (all three
    # investor types) into dicts: the cold path dropped from ~13s to a second or two.
    frame = store.read_range_frame(
        INSTITUTIONAL_FLOW_DATASET,
        "TW",
        window_dates,
        columns=("symbol", "investor", "trade_date", "buy", "sell"),
    )
    investor = _STREAK_INVESTOR_BY_KIND[kind]
    # Positional tuples, not attribute access: pandas has no stub-backed row types here (same
    # idiom as `analytics/scoring_batch/::_flow_rows_by_symbol`).
    flow_tuples = cast(
        "Iterable[tuple[object, object, object, object, object]]",
        frame.itertuples(index=False, name=None),
    )
    per_symbol_days: dict[str, dict[str, float]] = defaultdict(dict)
    for symbol, row_investor, trade_date, buy, sell in flow_tuples:
        if not isinstance(symbol, str) or row_investor != investor or trade_date is None:
            continue
        day_key = str(trade_date)
        net = float(
            (buy if isinstance(buy, int | float) else 0)
            - (sell if isinstance(sell, int | float) else 0)
        )
        per_symbol_days[symbol][day_key] = per_symbol_days[symbol].get(day_key, 0.0) + net
    per_symbol: dict[str, list[float]] = {
        symbol: [net for _, net in sorted(per_day.items())]
        for symbol, per_day in per_symbol_days.items()
    }

    ranked: list[TwRankingRow] = []
    for symbol, series in per_symbol.items():
        length = chip_streaks.streak_length(series)
        if length == 0:
            continue
        ranked.append(
            TwRankingRow(
                symbol=symbol,
                name=names_by_symbol.get(symbol),
                extra_value=float(length),
                extra_label=f"連{abs(length)}天{'買超' if length > 0 else '賣超'}",
            )
        )
    if target_symbol is not None:
        return GetTwMarketRankingsOutput(
            kind=kind,
            trade_date=latest_date,
            data_available=True,
            rows=tuple(r for r in ranked if r.symbol == target_symbol),
        )
    ranked.sort(key=lambda r: abs(r.extra_value), reverse=True)
    return GetTwMarketRankingsOutput(
        kind=kind, trade_date=latest_date, data_available=True, rows=tuple(ranked[:top_n])
    )


def _margin_change_rows(
    store: ParquetStore,
    names_by_symbol: dict[str, str],
    top_n: int,
    target_symbol: str | None = None,
) -> GetTwMarketRankingsOutput:
    latest_date = _latest_dataset_date(store, MARGIN_SHORT_JOB.dataset)
    if latest_date is None:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.MARGIN_CHANGE,
            data_available=False,
            unavailable_reason=_unavailable_reason("margin short"),
        )
    raw_rows = store.read_partition(MARGIN_SHORT_JOB.dataset, "TW", latest_date)
    ranked: list[TwRankingRow] = []
    for row in raw_rows:
        symbol = row.get("symbol")
        today = row.get("margin_purchase_today_balance")
        yesterday = row.get("margin_purchase_yesterday_balance")
        if (
            not isinstance(symbol, str)
            or not isinstance(today, int | float)
            or not isinstance(yesterday, int | float)
        ):
            continue
        change = float(today) - float(yesterday)
        ranked.append(
            TwRankingRow(
                symbol=symbol,
                name=names_by_symbol.get(symbol),
                extra_value=change,
                extra_label=f"{'+' if change >= 0 else ''}{change:,.0f}",
            )
        )
    if target_symbol is not None:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.MARGIN_CHANGE,
            trade_date=latest_date,
            data_available=True,
            rows=tuple(r for r in ranked if r.symbol == target_symbol),
        )
    ranked.sort(key=lambda r: r.extra_value, reverse=True)
    return GetTwMarketRankingsOutput(
        kind=TwRankingKind.MARGIN_CHANGE,
        trade_date=latest_date,
        data_available=True,
        rows=tuple(ranked[:top_n]),
    )


def _dividend_yield_rows(
    store: ParquetStore,
    names_by_symbol: dict[str, str],
    top_n: int,
    target_symbol: str | None = None,
) -> GetTwMarketRankingsOutput:
    latest_date = _latest_dataset_date(store, VALUATION_RATIOS_JOB.dataset)
    if latest_date is None:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.DIVIDEND_YIELD,
            data_available=False,
            unavailable_reason=_unavailable_reason("valuation ratios"),
        )
    raw_rows = store.read_partition(VALUATION_RATIOS_JOB.dataset, "TW", latest_date)
    ranked: list[TwRankingRow] = []
    for row in raw_rows:
        symbol = row.get("symbol")
        yield_pct = row.get("dividend_yield")
        if not isinstance(symbol, str) or not isinstance(yield_pct, int | float):
            continue
        ranked.append(
            TwRankingRow(
                symbol=symbol,
                name=names_by_symbol.get(symbol),
                extra_value=float(yield_pct),
                extra_label=f"{float(yield_pct):.2f}%",
            )
        )
    if target_symbol is not None:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.DIVIDEND_YIELD,
            trade_date=latest_date,
            data_available=True,
            rows=tuple(r for r in ranked if r.symbol == target_symbol),
        )
    ranked.sort(key=lambda r: r.extra_value, reverse=True)
    return GetTwMarketRankingsOutput(
        kind=TwRankingKind.DIVIDEND_YIELD,
        trade_date=latest_date,
        data_available=True,
        rows=tuple(ranked[:top_n]),
    )


def _revenue_growth_rows(
    store: ParquetStore,
    names_by_symbol: dict[str, str],
    top_n: int,
    target_symbol: str | None = None,
) -> GetTwMarketRankingsOutput:
    known_dates = store.list_partition_dates(MONTH_REVENUE_JOB.dataset, "TW")
    if not known_dates:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.REVENUE_GROWTH,
            data_available=False,
            unavailable_reason=_unavailable_reason("month revenue"),
        )
    latest_date = max(known_dates)
    latest_rows = store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", latest_date)
    latest_by_symbol: dict[str, dict[str, object]] = {
        str(row["symbol"]): row for row in latest_rows if row.get("symbol")
    }
    if not latest_by_symbol:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.REVENUE_GROWTH,
            trade_date=latest_date,
            data_available=False,
            unavailable_reason=_unavailable_reason("month revenue"),
        )
    # Real YoY match: same calendar month, filing year - 1 — not an approximation, and matched
    # per-symbol (each symbol's OWN latest revenue_month/revenue_year), since filings land on
    # irregular per-company dates so not every symbol's latest filing is for the same month. A
    # given symbol's year-ago filing may also live under a different partition date than
    # `latest_date`, so every known date is scanned rather than assuming a fixed offset.
    target_by_symbol: dict[str, tuple[int, int]] = {}
    for symbol, row in latest_by_symbol.items():
        month = row.get("revenue_month")
        year = row.get("revenue_year")
        if isinstance(month, int) and isinstance(year, int):
            target_by_symbol[symbol] = (month, year - 1)

    year_ago_by_symbol: dict[str, dict[str, object]] = {}
    for scan_date in known_dates:
        for row in store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", scan_date):
            row_symbol = row.get("symbol")
            if (
                not isinstance(row_symbol, str)
                or row_symbol in year_ago_by_symbol
                or row_symbol not in target_by_symbol
            ):
                continue
            target_month, target_year = target_by_symbol[row_symbol]
            if row.get("revenue_month") == target_month and row.get("revenue_year") == target_year:
                year_ago_by_symbol[row_symbol] = row

    ranked: list[TwRankingRow] = []
    for symbol, latest_row in latest_by_symbol.items():
        year_ago_row = year_ago_by_symbol.get(symbol)
        latest_revenue = latest_row.get("revenue")
        year_ago_revenue = year_ago_row.get("revenue") if year_ago_row else None
        if (
            not isinstance(latest_revenue, int | float)
            or not isinstance(year_ago_revenue, int | float)
            or year_ago_revenue == 0
        ):
            continue
        yoy_pct = (float(latest_revenue) - float(year_ago_revenue)) / float(year_ago_revenue) * 100
        ranked.append(
            TwRankingRow(
                symbol=symbol,
                name=names_by_symbol.get(symbol),
                extra_value=yoy_pct,
                extra_label=f"{yoy_pct:+.1f}%",
            )
        )
    if target_symbol is not None:
        return GetTwMarketRankingsOutput(
            kind=TwRankingKind.REVENUE_GROWTH,
            trade_date=latest_date,
            data_available=True,
            rows=tuple(r for r in ranked if r.symbol == target_symbol),
        )
    ranked.sort(key=lambda r: r.extra_value, reverse=True)
    return GetTwMarketRankingsOutput(
        kind=TwRankingKind.REVENUE_GROWTH,
        trade_date=latest_date,
        data_available=True,
        rows=tuple(ranked[:top_n]),
    )


class GetTwMarketRankingsTool(BaseTool):
    """`get_tw_market_rankings` — see module docstring."""

    name = "get_tw_market_rankings"
    description = (
        "Get a top-N TW-market ranking for one of: dealer/trust/foreign consecutive-day "
        "net-buy/sell streak, day-over-day margin-purchase-balance change, dividend yield, or "
        "monthly-revenue YoY growth. Reads already-ingested whole-market partitions, never a "
        "live per-symbol fetch."
    )
    input_model: type[BaseModel] = GetTwMarketRankingsInput
    output_model: type[BaseModel] = GetTwMarketRankingsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "tw_market_ranking"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store
        # kind -> (computed-at monotonic seconds, store freshness token, FULL ranking). The
        # ranking is identical for every caller of a kind — `top_n` and `symbol` only slice it —
        # so a stock page asking "does 2330 have a foreign streak" must not recompute (and
        # re-read 20 days of whole-market flow) every time.
        self._full_rankings: dict[
            TwRankingKind, tuple[float, object, GetTwMarketRankingsOutput]
        ] = {}

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetTwMarketRankingsInput, args)
        # Synchronous DuckDB + pure-Python aggregation: off the event loop so it can't freeze every
        # other request (and the SSE fan-out) while it runs.
        return await anyio.to_thread.run_sync(self._rank, typed)

    def _rank(self, typed: GetTwMarketRankingsInput) -> GetTwMarketRankingsOutput:
        full = self._full_ranking(typed.kind)
        if typed.symbol is not None:
            return full.model_copy(
                update={"rows": tuple(r for r in full.rows if r.symbol == typed.symbol)}
            )
        return full.model_copy(update={"rows": full.rows[: typed.top_n]})

    def _full_ranking(self, kind: TwRankingKind) -> GetTwMarketRankingsOutput:
        token = self._store.freshness_token("TW")
        cached = self._full_rankings.get(kind)
        now = time.monotonic()
        if cached is not None and cached[1] == token and now - cached[0] < _RANKING_CACHE_TTL_S:
            return cached[2]
        names_by_symbol = _names_by_symbol(self._store)
        everything = 1_000_000  # the slice to `top_n` happens after caching, in `_rank`
        if kind in _STREAK_INVESTOR_BY_KIND:
            full = _streak_rows(self._store, kind, names_by_symbol, everything)
        elif kind is TwRankingKind.MARGIN_CHANGE:
            full = _margin_change_rows(self._store, names_by_symbol, everything)
        elif kind is TwRankingKind.DIVIDEND_YIELD:
            full = _dividend_yield_rows(self._store, names_by_symbol, everything)
        else:
            full = _revenue_growth_rows(self._store, names_by_symbol, everything)
        self._full_rankings[kind] = (now, token, full)
        return full


def build_registration_table() -> list[tuple[Tool, bool]]:
    store = ParquetStore(root=get_settings().parquet_root)
    return [(GetTwMarketRankingsTool(store), False)]


__all__ = [
    "GetTwMarketRankingsInput",
    "GetTwMarketRankingsOutput",
    "GetTwMarketRankingsTool",
    "TwRankingKind",
    "TwRankingRow",
    "build_registration_table",
]
