"""Whole-market `/market/*` data routes (rankings, flows, heatmaps, announcements)."""

from __future__ import annotations

from datetime import date as date_
from typing import Literal

from fastapi import APIRouter, Depends, Query

from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
)
from api.routers.market_rest._common import context as _context
from api.routers.market_rest._common import market_args as _market_args
from schemas.enums import Market
from tools.base import ToolContext
from tools.international_tools import FinMindInternationalMarket

router = APIRouter()
_dispatch = dispatch_validated_tool


@router.get("/market/index-strip")
async def index_strip(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """TW-tab ticker-strip public items (加權指數/櫃買指數/美元/黃金/原油) — backed by
    `get_market_index_strip`, `DataClass.PUBLIC`."""
    return await _dispatch(state, "get_market_index_strip", {}, ctx)


@router.get("/market/us-index-strip")
async def us_index_strip(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """US-tab ticker-strip public items (S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield/Fed funds
    rate) — backed by `get_us_market_index_strip`, `DataClass.PUBLIC`."""
    return await _dispatch(state, "get_us_market_index_strip", {}, ctx)


@router.get("/market/tick-snapshot")
async def tick_snapshot(
    symbol: str = Query(..., min_length=1, description="4-digit stock id or 3-digit index code."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """One bounded intraday tick-level snapshot for one stock/ETF or index code (Sponsor-tier) —
    backed by `get_tick_snapshot`. The ticker strip uses this for sector-index items (e.g. `036`
    半導體業); an unentitled caller gets the masked-teaser envelope, never an error."""
    return await _dispatch(state, "get_tick_snapshot", {"id": symbol, "market": market.value}, ctx)


@router.get("/market/futures-snapshot")
async def futures_snapshot(
    symbol: str = Query(..., min_length=1, description="Futures contract code, e.g. TXF."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """One bounded intraday futures-contract snapshot (Sponsor-tier) — backed by
    `get_futures_snapshot`. The ticker strip uses this for 台指期 (`id=TXF`)."""
    return await _dispatch(
        state, "get_futures_snapshot", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/market/institutional-ranking")
async def institutional_ranking(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market three-major-institutional-investor buy/sell totals by category."""
    return await _dispatch(
        state,
        "get_institutional_flow_total_market",
        _market_args(market, start_date, end_date),
        ctx,
    )


@router.get("/market/margin-maintenance")
async def margin_maintenance(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market margin-maintenance-ratio (融資維持率) risk gauge (Sponsor-tier)."""
    return await _dispatch(
        state, "get_margin_maintenance", _market_args(market, start_date, end_date), ctx
    )


@router.get("/market/day-trading")
async def day_trading(
    symbol: str = Query(..., min_length=1, description="Stock id — backing tool is per-symbol."),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Day-trading (當沖) eligibility + volume/value for one stock. The named backing tool
    (`get_day_trading`) is per-symbol; no whole-market 當沖排行 tool exists, so this route takes a
    required `symbol` rather than pretending to serve a market-wide ranking it cannot back."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_day_trading", args, ctx)


@router.get("/market/dividend-schedule")
async def dividend_schedule(
    symbol: str = Query(
        ..., min_length=1, description="Stock/ETF id — backing tool is per-symbol."
    ),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Announced dividend/ex-dividend (除權息) schedule for one stock/ETF symbol. The named
    backing tool (`get_dividend_schedule`) is per-symbol; no whole-market dividend-calendar tool
    exists, so this route takes a required `symbol`, mirroring `/market/day-trading`'s and
    `/market/active-etf-report`'s required-`symbol` route shape. A frontend building a
    market-wide-looking calendar view fans this out client-side, once per symbol (e.g. every ETF
    in `get_active_etf_roster`'s roster) — that fan-out is not a whole-market backend capability
    and needs no server-side change here."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_dividend_schedule", args, ctx)


@router.get("/market/etf-money-flow")
async def etf_money_flow(
    start_date: date_ = Query(...),
    end_date: date_ = Query(...),
    market: Market = Query(Market.TW),
    top_n: int = Query(20, ge=1, le=100),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Cross-ETF 資金流向 over a bounded date range (Sponsor-tier)."""
    return await _dispatch(
        state,
        "get_etf_money_flow",
        {
            "market": market.value,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "top_n": top_n,
        },
        ctx,
    )


@router.get("/market/daily-signal")
async def daily_signal(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """今日訊號 — whole-market, single-day active-ETF operations signal aggregated across every
    active ETF (Sponsor-tier). Per-stock 新增/刪除/加碼/減碼 status + 焦點股 highlights."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_daily_signal", args, ctx)


@router.get("/market/industry-flow")
async def industry_flow(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market industry-chain money-flow (產業鏈資金流) aggregate (Sponsor-tier)."""
    return await _dispatch(
        state, "get_industry_money_flow", _market_args(market, start_date, end_date), ctx
    )


@router.get("/market/margin-short-balance")
async def margin_short_balance(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market margin-purchase/short-sale total by category."""
    return await _dispatch(
        state,
        "get_margin_short_total_market",
        _market_args(market, start_date, end_date),
        ctx,
    )


@router.get("/market/advance-decline")
async def advance_decline(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market advance/decline breadth for one trading day (defaults to the latest
    available day) — the backing tool's input is a single `trade_date`, not a start/end range,
    so this route does not reuse `_market_args`."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_advance_decline", args, ctx)


@router.get("/market/order-book-stats")
async def order_book_stats(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-exchange 5-second order-book/trade aggregate (委買委賣統計) — upstream is single-day
    per request, so only `start_date`/`end_date` (via `_market_args`) narrow to one day; a wider
    range is rejected upstream, not silently truncated here."""
    return await _dispatch(
        state, "get_order_book_stats", _market_args(market, start_date, end_date), ctx
    )


@router.get("/market/price-momentum")
async def price_momentum(
    market: Market = Query(Market.TW),
    period: Literal["day", "week", "month"] = Query("week"),
    trade_date: date_ | None = Query(None),
    top_n: int = Query(10, ge=1, le=50),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market top-N gainers/losers by pure close-to-close percentage change over a chosen
    lookback period (day/week/month trading days) — single-factor price momentum, distinct from
    `/analytics/rankings`' multi-factor composite score. Same single-`trade_date` shape as
    `/advance-decline`."""
    args: dict[str, object] = {"market": market.value, "period": period, "top_n": top_n}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_price_momentum", args, ctx)


@router.get("/market/us-rankings")
async def us_rankings(
    kind: Literal["trending", "market_cap", "revenue", "roe", "volume"] = Query("trending"),
    top_n: int = Query(10, ge=1, le=50),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """US-only 排行榜 multi-tab ranking: `trending` (Yahoo's own most-active screen), `market_cap`/
    `revenue`/`roe`/`volume` (a bounded Yahoo screener query sorted by the matching field). No
    `market` param — this is inherently a US-region screener query (`get_us_market_rankings`'s
    own docstring), unlike `/market/price-momentum`'s real `market` axis over our own ingested
    data."""
    return await _dispatch(state, "get_us_market_rankings", {"kind": kind, "top_n": top_n}, ctx)


@router.get("/market/tw-rankings")
async def tw_rankings(
    kind: Literal[
        "dealer_streak",
        "trust_streak",
        "foreign_streak",
        "margin_change",
        "dividend_yield",
        "revenue_growth",
    ] = Query("dealer_streak"),
    top_n: int = Query(10, ge=1, le=50),
    symbol: str | None = Query(
        None, description="When set, return only this symbol's own row (ignores top_n)."
    ),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """TW-only 排行榜 multi-tab ranking (6 of its 7 tabs — 大戶買賣 reuses
    `/market/holder-accumulation` verbatim instead of this route): dealer/trust/foreign
    consecutive-day net-buy/sell streak, day-over-day margin-purchase-balance change, dividend
    yield, monthly-revenue YoY growth. No `market` param — TW-only by construction
    (`get_tw_market_rankings`'s own docstring), mirroring `/market/us-rankings`'s own shape.
    `symbol` switches this into a single-row lookup (e.g. a stock-detail signal badge asking
    "does 2330 have a foreign-buy streak today")."""
    args: dict[str, object] = {"kind": kind, "top_n": top_n}
    if symbol is not None:
        args["symbol"] = symbol
    return await _dispatch(state, "get_tw_market_rankings", args, ctx)


@router.get("/market/holder-accumulation")
async def holder_accumulation(
    market: Market = Query(Market.TW),
    threshold: int = Query(
        400, description="Holding-size threshold in 張: 200/400/800/1000 (validated by the tool)."
    ),
    window: int = Query(2, description="Weekly snapshots back to compare against: 1/2/3/4."),
    as_of: date_ | None = Query(None),
    top_n: int = Query(20, ge=1, le=50),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """大戶加碼股 — whole-market ranking of which stocks grew a chosen large-holder tier
    (>=200/400/800/1,000張) share-of-custody the most over 1-4 weekly 集保戶股權分散表 snapshots
    (Sponsor-tier). Distinct from `/market/big-holder-accumulation`, which wraps the per-symbol
    `get_shareholder_dispersion` tool — this route is the whole-market ranking counterpart,
    backed by `get_holder_accumulation`.

    `threshold`/`window` are plain `int` here (not a route-level `Literal[int, ...]`): FastAPI's
    query-string coercion validates a `Literal` against the RAW string before int-casting it,
    so a real request like `?threshold=400` fails validation despite `400` being a legal
    choice — confirmed live, not a hypothetical. The tool's own `HolderThreshold`/`HolderWindow`
    enums (already-int-typed) still reject an out-of-set value, just after FastAPI's own
    int-coercion has already run once."""
    args: dict[str, object] = {
        "market": market.value,
        "threshold": threshold,
        "window": window,
        "top_n": top_n,
    }
    if as_of is not None:
        args["as_of"] = as_of.isoformat()
    return await _dispatch(state, "get_holder_accumulation", args, ctx)


@router.get("/market/index-contribution")
async def index_contribution(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """APPROXIMATE per-symbol index-contribution estimate for one trading day (market-cap
    weight x price change) — not TWSE's official float-adjusted figure; see the response's own
    `contribution_caveat` (Sponsor-tier). Same single-`trade_date` shape as `/advance-decline`,
    not a start/end range."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_index_contribution", args, ctx)


@router.get("/market/sector-heatmap")
async def sector_heatmap(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    period: Literal[
        "immediate", "week", "two_weeks", "month", "quarter", "half_year", "year"
    ] = Query("immediate"),
    size_by: Literal["turnover", "market_value", "volume"] = Query("turnover"),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market industry/sector heatmap (產業別漲跌熱力圖) for one trading day (defaults to
    the latest available day): each industry's average percentage change over `period`
    (trading-value-weighted when available), constituent stock count, and total turnover/
    market-value/volume for `size_by`'s tile-sizing — the backing tool's date input is a single
    `trade_date`, not a start/end range, same shape as `/advance-decline` and
    `/index-contribution`."""
    args: dict[str, object] = {"market": market.value, "period": period, "size_by": size_by}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_sector_heatmap", args, ctx)


@router.get("/market/stock-heatmap")
async def stock_heatmap(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    period: Literal[
        "immediate", "week", "two_weeks", "month", "quarter", "half_year", "year"
    ] = Query("immediate"),
    size_by: Literal["turnover", "market_value", "volume"] = Query("turnover"),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market flat per-stock heatmap (股票熱力圖) for one trading day (defaults to the
    latest available day): the top-ranked stocks' own percentage change over `period`, close,
    turnover/market-value/volume for `size_by`'s tile-sizing, and industry — the per-ticker
    counterpart to `/market/sector-heatmap`'s per-industry aggregate tiles. Same single-
    `trade_date` shape as `/sector-heatmap`, not a start/end range."""
    args: dict[str, object] = {"market": market.value, "period": period, "size_by": size_by}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_stock_heatmap", args, ctx)


@router.get("/market/sector-constituents")
async def sector_constituents(
    market: Market = Query(Market.TW),
    industry: str | None = Query(
        None, description="Restrict to one get_sector_heatmap industry value."
    ),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Per-stock drill-down (close-to-close % change, close, volume, turnover) for one
    `/market/sector-heatmap` industry tile, or every symbol ranked together if `industry` is
    omitted — same single-`trade_date` shape as `/sector-heatmap`."""
    args: dict[str, object] = {"market": market.value}
    if industry is not None:
        args["industry"] = industry
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_sector_constituents", args, ctx)


@router.get("/market/sector-rotation")
async def sector_rotation(
    market: Market = Query(Market.TW),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Daily industry ranking by net institutional flow over the last 10 trading days ending at
    `end_date` (defaults to the latest available day) — the data behind a sector-rotation
    rank-bump chart, backed by `get_sector_rotation`."""
    args: dict[str, object] = {"market": market.value}
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_sector_rotation", args, ctx)


@router.get("/market/sector-rotation-rrg")
async def sector_rotation_rrg(
    market: Market = Query(Market.TW),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Real weekly Relative Rotation Graph (RS-Ratio x RS-Momentum, JdK construction) for TWSE's
    published industry sub-indices against TAIEX, ending at `end_date` (defaults to the latest
    available day) — backed by `get_sector_rotation_rrg`."""
    args: dict[str, object] = {"market": market.value}
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_sector_rotation_rrg", args, ctx)


@router.get("/market/etf-list")
async def etf_list(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market Taiwan-listed ETF list for one trading day (defaults to the latest
    available day): price/change/volume/1-week-return, AUM, expense-ratio floor, and
    trailing-12-month distribution yield/frequency for every listed ETF — same single-
    `trade_date` shape as `/advance-decline`/`/sector-heatmap`, not a start/end range."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_etf_list", args, ctx)


@router.get("/market/dividend-calendar")
async def dividend_calendar(
    market: Market = Query(Market.TW),
    year: int | None = Query(None),
    month: int | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """股息日曆 — every Taiwan-listed ETF's 除息日/最後購買日/股利發放日 landing within one
    requested calendar month (defaults to the current month). `year`/`month` must both be
    given or both omitted."""
    args: dict[str, object] = {"market": market.value}
    if year is not None:
        args["year"] = year
    if month is not None:
        args["month"] = month
    return await _dispatch(state, "get_dividend_calendar", args, ctx)


@router.get("/market/big-holder-accumulation")
async def big_holder_accumulation(
    symbol: str = Query(..., min_length=1, description="Stock id — backing tool is per-symbol."),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """股權分散 shareholding-level distribution (千張大戶比率) for one stock — a chip-concentration
    signal (Sponsor-tier). The named backing tool (`get_shareholder_dispersion`) is per-symbol;
    mirrors `/market/day-trading`'s required-`symbol` route shape."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_shareholder_dispersion", args, ctx)


@router.get("/market/active-etf-report")
async def active_etf_report(
    symbol: str = Query(
        ..., min_length=1, description="Active ETF id — backing tool is per-symbol."
    ),
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """主動式ETF 操作日報 for one active-management ETF — required `symbol`, plus an optional
    single `trade_date` (the backing tool's own field name; not a start/end range), mirroring
    `/market/day-trading`'s required-`symbol` route shape (Sponsor-tier)."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_active_etf_report", args, ctx)


@router.get("/market/international-index")
async def international_index(
    symbol: str = Query(
        ..., min_length=1, description="The market's own quote symbol, e.g. '^DJI'."
    ),
    market: FinMindInternationalMarket = Query(...),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """FinMind's own roster-info + EOD-price bundle for one US/GB/EU/JP quote symbol — a
    distinct dataset family from the yfinance-primary `price_eod` path for the same markets (see
    `tools/international_tools.py`'s own docstring). `market` reuses
    `FinMindInternationalMarket` (only the four non-TW markets FinMind carries this family for),
    not the full `schemas.enums.Market` set `_market_args` elsewhere in this file uses."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_international_stock", args, ctx)


@router.get("/market/material-announcements")
async def material_announcements(
    symbol: str = Query(..., min_length=1, description="Stock id — backing tool is per-symbol."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Today's MOPS material-announcement (重大訊息) disclosures for one stock. The named
    backing tool (`get_material_announcements`) serves the MOPS adapter's current whole-market
    dump filtered to one company with no date-range param upstream — this route deliberately
    takes no `start_date`/`end_date`, since the tool ignores them (see that tool's own
    docstring)."""
    return await _dispatch(
        state, "get_material_announcements", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/market/material-announcement-feed")
async def material_announcement_feed(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    category: str | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """重大訊息 whole-market feed for one day (defaults to the latest day MOPS ingest has
    captured), optionally filtered to one derived category bucket, with a per-category count
    breakdown — read from the ingest-owned Parquet store, never a live fetch. Distinct from
    `/market/material-announcements` above (still per-symbol, required `symbol`, live-fetched).
    Public — no Sponsor gating."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    if category is not None:
        args["category"] = category
    return await _dispatch(state, "get_material_announcement_feed", args, ctx)


@router.get("/market/insider-transfer-declarations")
async def insider_transfer_declarations(
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """內部人股權轉讓事前申報 (insider advance-transfer-plan declaration) whole-market feed for
    one day (defaults to the latest day MOPS ingest has captured) — `t187ap12_L` only, read from
    the ingest-owned Parquet store, never a live fetch. Public — no Sponsor gating."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_insider_transfer_declarations", args, ctx)


@router.get("/market/disposition-board")
async def disposition_board(
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """處置王-style disposition board: 風險股/處置中/即將出關 buckets, read from the ingest-owned
    `attention_stocks`/`disposition_events` partitions plus already-ingested price/institutional
    -flow joins and a bounded per-symbol margin fan-out. Sponsor-gated (masked teaser for an
    unentitled caller)."""
    return await _dispatch(state, "get_disposition_board", {"market": market.value}, ctx)


__all__ = ["router"]
