"""Thin REST over the already-built market / derivatives / macro agent tools.

Each route is a direct projection of one (or a small bundle of) registry tool(s), dispatched
through `tools.base.dispatch_tool` exactly as `market.py`'s `/quote` does — never a tool's own
`run()` — so the sponsor-entitlement gate applies uniformly: a Sponsor-classed tool returns the
masked-teaser shape at HTTP 200 for an unentitled caller, real data for a Premium/Pro one.
`optional_user` supplies the (possibly anonymous) entitlement.

One route the consolidated interface reference lists here is deliberately absent: its named
backing tool (`get_market_context`) was never built — no adapter/capability backs it — so wiring
a route that dispatched a nonexistent tool would 500 by construction. It is omitted rather than
faked. `get_advance_decline`, `get_index_contribution`, and `get_sector_heatmap` (產業別漲跌熱力圖)
— previously in the same absent list (`get_sector_indices` was the placeholder name for the
heatmap concept before its two real ingredients — `STOCK_INFO_JOB`'s industry roster and
`PRICE_EOD_JOB`'s closes — were identified) — are now real, derived-store-backed tools
(`tools/market_tools.py`) and have routes below (`/market/advance-decline`,
`/market/index-contribution`, `/market/sector-heatmap`). `get_taiex_vix`
(`/derivatives/taiex-vix`) and `get_international_stock` (`/market/international-index`) are
likewise real, already-built tools that simply lacked routes before this pass — never listed in
this module's own "never built"
absent-list above (that claim would have been stale/wrong). `run_tag_screener`/`get_tag_catalog`
(`/market/tag-screener`, `/market/tag-catalog`) are likewise real, already-built tools that simply
lacked routes before this pass.
"""

from __future__ import annotations

from datetime import date as date_
from typing import Literal, cast

from fastapi import APIRouter, Depends, Query, status

from accounts.models import User
from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
    optional_user,
    tool_context_for,
)
from api.errors import ApiError
from schemas.enums import Market
from tools.base import ToolContext
from tools.international_tools import FinMindInternationalMarket

router = APIRouter(tags=["market-data"])


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


# `GetDividendCalendarInput`'s `year`/`month`-together validator is the first cross-field
# constraint in this router that can actually trip `dispatch_validated_tool`'s 422 path — a bad
# tool arg (as opposed to a bad query PARAM, which FastAPI's own `RequestValidationError` handler
# already renders as 422) must not fall through to the catch-all 500 handler.
_dispatch = dispatch_validated_tool


@router.get("/market/index-strip")
async def index_strip(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """TW-tab ticker-strip public items (加權指數/櫃買指數/美元/黃金/原油) — backed by
    `get_market_index_strip`, `DataClass.PUBLIC`."""
    return await _dispatch(state, "get_market_index_strip", {}, ctx)


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


# ---- market-level reads --------------------------------------------------------------------


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


@router.get("/market/tag-catalog")
async def tag_catalog(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The discoverable tap-tile screener tag list, grouped by category with bilingual labels —
    `get_tag_catalog` takes no input, so this route dispatches with an empty args dict."""
    return await _dispatch(state, "get_tag_catalog", {}, ctx)


@router.get("/market/tag-screener")
async def tag_screener(
    tags: list[str] = Query(
        default=[],
        description="Tag keys to require (see /market/tag-catalog), e.g. 外資連3買.",
    ),
    negate_tags: list[str] = Query(
        default=[],
        description="Tag keys to require the ABSENCE of — combined with `tags` under `match`.",
    ),
    match: Literal["AND", "OR"] = Query("AND"),
    market: Market = Query(Market.TW),
    as_of: date_ | None = Query(None),
    size: int = Query(50, ge=1, le=250),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Tap-tile tag screener over the nightly-computed flag partition. `run_tag_screener`'s own
    input nests a `tags: tuple[TagCondition]` (each `{tag, negate}`) that doesn't flatten
    trivially into GET query params, so this route instead accepts two flat repeated-key lists —
    plain tag keys in `tags` and their negated counterparts in `negate_tags` — and builds the
    `TagCondition` tuple server-side; this keeps the route a plain GET (matching every other route
    in this file) with no nested-object query-string encoding for the caller to get right."""
    conditions: list[dict[str, object]] = [{"tag": tag, "negate": False} for tag in tags]
    conditions.extend({"tag": tag, "negate": True} for tag in negate_tags)
    if not conditions:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_request",
            message="At least one of `tags` or `negate_tags` is required.",
            retryable=False,
        )
    args: dict[str, object] = {
        "market": market.value,
        "tags": conditions,
        "match": match,
        "size": size,
    }
    if as_of is not None:
        args["as_of"] = as_of.isoformat()
    return await _dispatch(state, "run_tag_screener", args, ctx)


def _split_symbols(symbols: str) -> list[str]:
    """Splits the frontend's single comma-joined `symbols` value (`lib/api/market.ts`'s
    `symbolsSearchParams` sends `symbols=2330,2454`, never repeated `symbols=` params) into a
    list — `list[str] = Query(...)` only populates from repeated query keys, so declaring these
    routes that way silently accepted a single-symbol request and matched nothing for every
    multi-symbol batch call (every comma-joined value became one unmatched literal string).
    Empty segments (a trailing comma, `""`) are dropped rather than passed through as a bogus
    symbol id."""
    return [s for s in symbols.split(",") if s]


@router.get("/market/symbol-info")
async def symbol_info(
    symbols: str = Query(..., description="Up to 20 comma-separated symbol ids, e.g. '2330,2454'."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Batched symbol->name (+ industry/security-type) lookup, e.g. to enrich a bare-symbol
    ranking (`get_score_leaderboard`) with a display name. A symbol absent from the returned
    `rows` is not in the current whole-market roster — never a fabricated name in its place."""
    args: dict[str, object] = {"symbols": _split_symbols(symbols), "market": market.value}
    return await _dispatch(state, "get_symbol_info", args, ctx)


@router.get("/market/price-history")
async def price_history(
    symbols: str = Query(..., description="Up to 20 comma-separated symbol ids, e.g. '2330,2454'."),
    market: Market = Query(Market.TW),
    days: int = Query(20, ge=2, le=60, description="Trailing trading-day count, 2-60."),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """A bounded trailing-day OHLCV window for up to 20 symbols — the data behind a sparkline
    or a client-computed %-change, for any bare-symbol list (leaderboard, watchlist, ETF
    roster). `get_price_quote`/`/quote` stay latest-bar-only; this is the separate bounded-
    history capability those callers need instead."""
    return await _dispatch(
        state,
        "get_price_history",
        {"symbols": _split_symbols(symbols), "market": market.value, "days": days},
        ctx,
    )


# ---- derivatives ---------------------------------------------------------------------------


@router.get("/derivatives/taiex-vix")
async def taiex_vix(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """臺指選擇權波動率指數 (TAIEX option VIX) reading series — whole-market, Sponsor-tier.
    Backed by `get_taiex_vix` (`MarketOnlyInput`, no per-contract `id`), a separate concern
    from `/derivatives/{contract}`'s per-contract bundle below. Declared BEFORE
    `/derivatives/{contract}` — FastAPI matches path routes in registration order, and a
    dynamic `{contract}` segment would otherwise swallow this literal `taiex-vix` path."""
    return await _dispatch(state, "get_taiex_vix", _market_args(market, start_date, end_date), ctx)


@router.get("/derivatives/{contract}")
async def derivatives(
    contract: str,
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """期/選 daily OI/price, put/call positioning, and institutional/large-trader positioning
    for one contract code — descriptive positioning context, not a directional call. Bundles the
    three per-contract derivatives tools; the whole-market TAIEX VIX read is a separate concern
    (its backing tool is market-level, not per-contract)."""
    args: dict[str, object] = {"id": contract, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return {
        "futures_options_daily": await _dispatch(state, "get_futures_options_daily", args, ctx),
        "put_call_ratio": await _dispatch(state, "get_put_call_ratio", args, ctx),
        "institutional": await _dispatch(state, "get_derivatives_institutional", args, ctx),
    }


# ---- macro ---------------------------------------------------------------------------------


@router.get("/macro")
async def macro(
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """匯率/利率/黃金/原油/美債殖利率/Fear-Greed macro-context report (Sponsor-tier)."""
    args: dict[str, object] = {}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return await _dispatch(state, "get_macro_indicators", args, ctx)


@router.get("/macro/business-indicator")
async def business_indicator(
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Monthly 景氣對策信號 business-climate indicator (Sponsor-tier)."""
    return await _dispatch(
        state, "get_business_indicator", _market_args(market, start_date, end_date), ctx
    )


def _market_args(
    market: Market, start_date: date_ | None, end_date: date_ | None
) -> dict[str, object]:
    args: dict[str, object] = {"market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return args


def _symbol_date_args(
    symbol: str, market: Market, start_date: date_ | None, end_date: date_ | None
) -> dict[str, object]:
    """Same shape `/market/day-trading`'s own inline body builds — the `DateRangeInput`-backed
    per-symbol tools (institutional flow, margin/short, securities lending, dividend result,
    month revenue, financial statement, valuation ratios, chip flows, disposition securities,
    calendar events, FinMind stock news) all take this exact `{id, market, [start_date],
    [end_date]}` shape, so this one helper serves the stock-detail-page route batch below rather
    than duplicating the same four lines per route."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if start_date is not None:
        args["start_date"] = start_date.isoformat()
    if end_date is not None:
        args["end_date"] = end_date.isoformat()
    return args


@router.get("/market/news-feed")
async def news_feed(
    query: str = Query("台股"),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """即時新聞 — near-real-time news aggregated from financial RSS + optional Finnhub, backed by
    `get_news_feed`, `DataClass.PUBLIC`. `query` only scopes the Finnhub call; RSS feeds are
    always fetched whole (not query-filterable upstream)."""
    return await _dispatch(state, "get_news_feed", {"query": query}, ctx)


@router.get("/market/ptt-board")
async def ptt_board(
    board: str = Query("Stock"),
    lookback_days: int = Query(1, ge=1, le=14),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """PTT — one board's index-listing (title/author/push-count/post-tag/posted_date), backed by
    `get_ptt_board`, `DataClass.PUBLIC`. `board` must be one of `tools.ptt_tools.PTT_BOARDS`
    ('Stock' 股票版, 'Tech_Job' 科技業板, 'Warrant' 權證版 — usable in analysis; 'Beauty' 表特版,
    'Gossiping' 八卦版 — offtopic, never a market claim). `lookback_days` (default 1, today's
    index page only) walks the board's own 'older posts' nav link to cover more days — there is
    no per-page date filter upstream, so a wider window costs more sequential page fetches."""
    return await _dispatch(
        state, "get_ptt_board", {"board": board, "lookback_days": lookback_days}, ctx
    )


# ---- stock-detail-page reads (per-symbol) --------------------------------------------------


@router.get("/stock/company-profile")
async def company_profile(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """AssetHeader + 基本資料's 個股資訊卡/財務快照 — the composite profile card, backed by
    `get_company_profile`, `DataClass.PUBLIC`."""
    return await _dispatch(
        state, "get_company_profile", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/stock/insider-shareholding")
async def insider_shareholding(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """基本資料's 內部人/董監持股·質押比 — MOPS 董監事持股餘額, backed by
    `get_insider_shareholding`, `DataClass.PUBLIC`."""
    return await _dispatch(
        state, "get_insider_shareholding", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/stock/insider-transfer-declarations")
async def stock_insider_transfer_declarations(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    trade_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """基本資料's 董監事持股 card, 持股轉讓 sub-tab — 內部人股權轉讓事前申報, filtered to one
    symbol server-side (the underlying `get_insider_transfer_declarations` tool is a
    whole-market-per-day feed — this route is the same feed, just narrowed by `symbol` before
    returning, no new tool needed). `DataClass.PUBLIC`."""
    result = await _dispatch(
        state,
        "get_insider_transfer_declarations",
        {"market": market.value, **({"trade_date": trade_date} if trade_date else {})},
        ctx,
    )
    rows = result.get("rows")
    if not isinstance(rows, list):
        return result
    typed_rows = cast("list[dict[str, object]]", rows)
    filtered_rows: list[dict[str, object]] = [
        row for row in typed_rows if row.get("symbol") == symbol
    ]
    return {**result, "rows": filtered_rows}


@router.get("/stock/esg-score")
async def esg_score(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """基本資料's ESG風險卡 — backed by `get_esg_score`, `DataClass.PUBLIC`. Coverage-gated
    (`esg=None` when yfinance tracks no ESG data) — the frontend hides this card entirely on
    `None`, never an empty scaffold."""
    return await _dispatch(state, "get_esg_score", {"id": symbol, "market": market.value}, ctx)


@router.get("/stock/disposition-securities")
async def disposition_securities(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """基本資料's 處置/注意 banner — 處置有價證券 announcement rows, backed by
    `get_disposition_securities`, `DataClass.SPONSOR`. No forecast field on this tool — the
    banner shows only the current/historical flag, never a "最快N日進處置" projection."""
    return await _dispatch(
        state,
        "get_disposition_securities",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/dividend-result")
async def dividend_result(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """財務分析's 股利政策 (settled-result half) — 除權除息結果, backed by `get_dividend_result`,
    `DataClass.PUBLIC`. Distinct from `/market/dividend-schedule` (announced-policy timeline)."""
    return await _dispatch(
        state, "get_dividend_result", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/dividend-policy-summary")
async def dividend_policy_summary(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """財務分析's 股利政策 card — the one-call composite report (announced events, per-year
    cash+stock totals, 現金殖利率/配息率/填息成功率 stats, per-event fill results), backed by
    `get_dividend_policy_summary`, `DataClass.PUBLIC`. `start_date` bounds how far back the
    per-year chart/table go (the frontend's 計算區間 selector), not a fixed server default."""
    return await _dispatch(
        state,
        "get_dividend_policy_summary",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/month-revenue")
async def month_revenue(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """財務分析's 月營收趨勢 — 月營收 filing + pre-computed MoM/YoY, backed by `get_month_revenue`,
    `DataClass.PUBLIC`."""
    return await _dispatch(
        state, "get_month_revenue", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/financial-statement")
async def financial_statement(
    symbol: str = Query(..., min_length=1),
    statement_type: Literal["income_statement", "balance_sheet", "cash_flow"] = Query(
        "income_statement"
    ),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """財務分析's 財務結構/獲利趨勢 sub-tabs (損益表/資產負債表/現金流) — one statement type per
    call, backed by `get_financial_statement`, `DataClass.PUBLIC`."""
    args = _symbol_date_args(symbol, market, start_date, end_date)
    args["statement_type"] = statement_type
    return await _dispatch(state, "get_financial_statement", args, ctx)


@router.get("/stock/institutional-flow")
async def institutional_flow(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 法人 sub-tab — per-symbol 三大法人買賣 + pre-computed rolling net, backed by
    `get_institutional_flow`, `DataClass.PUBLIC`. Distinct from
    `/market/institutional-ranking`'s whole-market `get_institutional_flow_total_market`."""
    return await _dispatch(
        state,
        "get_institutional_flow",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/margin-short")
async def margin_short(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 融資券 sub-tab — per-symbol 融資融劵 balance, backed by `get_margin_short`,
    `DataClass.PUBLIC`. Distinct from `/market/margin-short-balance`'s whole-market
    `get_margin_short_total_market`."""
    return await _dispatch(
        state, "get_margin_short", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/securities-lending")
async def securities_lending(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 借券 sub-tab — 借券成交 rows, backed by `get_securities_lending`,
    `DataClass.PUBLIC`."""
    return await _dispatch(
        state,
        "get_securities_lending",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/chip-flows")
async def chip_flows(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 分點 sub-tab — 分點/broker-level trading rows, backed by `get_chip_flows`,
    `DataClass.SPONSOR` → masked-teaser envelope for an unentitled caller."""
    return await _dispatch(
        state, "get_chip_flows", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/government-bank-flows")
async def government_bank_flows(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 摘要 sub-tab's 4th 法人籤碼 category — 八大行庫 (eight government-bank)
    buy/sell rows, backed by `get_government_bank_flows`, `DataClass.SPONSOR` → masked-teaser
    envelope for an unentitled caller (same gating `chip_flows`'s own 分點 route uses above)."""
    return await _dispatch(
        state,
        "get_government_bank_flows",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/valuation-ratios")
async def valuation_ratios(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """研究圖表's 估值河流 — daily PER/PBR/殖利率 + pre-computed band, backed by
    `get_valuation_ratios`, `DataClass.PUBLIC`."""
    return await _dispatch(
        state, "get_valuation_ratios", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/analyst-consensus")
async def analyst_consensus(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """研究圖表's 分析師深度 — target-price/rating consensus, backed by `get_analyst_consensus`,
    `DataClass.PUBLIC`. Real for TW too; `consensus=None` when yfinance tracks no coverage."""
    return await _dispatch(
        state, "get_analyst_consensus", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/stock/calendar-events")
async def stock_calendar_events(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """研究圖表's 個股事件calendar — feed-sourced 除權息/CB events, backed by
    `get_calendar_events`, `DataClass.SPONSOR` → masked-teaser envelope for an unentitled
    caller. Distinct from `/analytics/...`-namespaced routes; this one is symbol-scoped."""
    return await _dispatch(
        state, "get_calendar_events", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/finmind-news")
async def finmind_stock_news(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """相關新聞's 財經新聞 sub-tab — FinMind's own per-symbol news dataset (single trading day
    per request upstream), backed by `get_finmind_stock_news`, `DataClass.PUBLIC`. Distinct from
    `/market/news-feed`'s whole-market RSS/Finnhub aggregator (即時新聞)."""
    return await _dispatch(
        state,
        "get_finmind_stock_news",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/foreign-shareholding")
async def stock_foreign_shareholding(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 三大法人 column, 持股 sub-tab — daily 外資持股比 (foreign-ownership ratio/
    limit), backed by `get_foreign_shareholding`, `DataClass.PUBLIC`. `/stock/*` alias for the
    same tool `/market/day-trading`'s sibling routes already expose under a `/market/*` prefix
    despite being per-symbol — kept consistent with every other stock-detail-page route here."""
    return await _dispatch(
        state,
        "get_foreign_shareholding",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/day-trading")
async def stock_day_trading(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """籌碼分析's 融資券 column, 當沖 sub-tab — day-trading eligibility + volume/value, backed
    by `get_day_trading`, `DataClass.PUBLIC`. Same tool as `/market/day-trading`; this `/stock/*`
    alias keeps this page's route naming consistent (that route's own docstring already notes
    it's per-symbol despite the `/market/` prefix)."""
    return await _dispatch(
        state,
        "get_day_trading",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/technical-indicators")
async def technical_indicators(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """技術分析's K線 chart data source — MA/Bollinger/KD/MACD series, 支撐/壓力 levels, and
    candlestick-pattern hits over a date range, backed by `get_technical_indicators`,
    `DataClass.PUBLIC`."""
    return await _dispatch(
        state,
        "get_technical_indicators",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/shareholder-dispersion")
async def shareholder_dispersion(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """技術分析/籌碼分析's 散戶 副圖 overlay — 股東人數/千張大戶比率 concentration series, backed
    by `get_shareholder_dispersion`, `DataClass.SPONSOR` → masked-teaser envelope for an
    unentitled caller (same gating `chip_flows`'s own 分點 route uses)."""
    return await _dispatch(
        state,
        "get_shareholder_dispersion",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/etfs-holding-stock")
async def etfs_holding_stock(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    as_of: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """追蹤 tab's ETF持有 card — every active-management ETF holding one
    stock, with 持有張數排行/權重排行/建清倉 sub-views, backed by `get_etfs_holding_stock`,
    `DataClass.SPONSOR` → masked-teaser envelope for an unentitled caller."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if as_of is not None:
        args["as_of"] = as_of.isoformat()
    return await _dispatch(state, "get_etfs_holding_stock", args, ctx)


@router.get("/stock/live-quote")
async def live_quote(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """即時 tab's 五檔 ladder — one bounded TWSE MIS best-5 bid/ask + last-trade snapshot,
    backed by `get_live_quote_snapshot`, `DataClass.PUBLIC` (never Sponsor-gated; TWSE MIS is
    the one free, keyless intraday source). The client re-polls this route on an interval —
    the tool itself makes exactly one upstream call per invocation, never a loop."""
    return await _dispatch(
        state, "get_live_quote_snapshot", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/stock/price-limit")
async def price_limit(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """看盤 tab's 詳細 stats row — daily 參考/漲停/跌停 price, backed by `get_price_limit`,
    `DataClass.PUBLIC`."""
    return await _dispatch(
        state, "get_price_limit", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get("/stock/intraday-kbar")
async def intraday_kbar(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """即時 tab's 分K chart — minute-bar series for one trading day, backed by
    `get_intraday_kbar`, `DataClass.SPONSOR` → masked-teaser envelope for an unentitled
    caller. Upstream publishes ~15:50 the same day, not a live feed during market hours."""
    return await _dispatch(
        state,
        "get_intraday_kbar",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/price-tick")
async def price_tick(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """即時 tab's 逐筆 list — tick-by-tick trade history for one trading day, backed by
    `get_price_tick`, `DataClass.SPONSOR` → masked-teaser envelope for an unentitled caller.
    Upstream publishes ~15:30 the same day (an end-of-day replay of the day's ticks), never a
    live-ticking stream — the frontend must not present this as real-time."""
    return await _dispatch(
        state,
        "get_price_tick",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get("/stock/ptt-search")
async def ptt_stock_search(
    symbol: str = Query(..., min_length=1),
    company_name: str = Query(..., min_length=1),
    lookback_days: int = Query(7, ge=1, le=14),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """相關新聞's PTT sub-tab — best-effort per-symbol search of Stock/Tech_Job/Warrant board
    posts (matched against post BODY text, not just the title), backed by
    `get_ptt_stock_search`, `DataClass.PUBLIC`. `company_name` is caller-supplied (e.g. from
    `get_symbol_info`) rather than resolved server-side here, since the caller already has it
    from rendering the page's own header."""
    return await _dispatch(
        state,
        "get_ptt_stock_search",
        {"symbol": symbol, "company_name": company_name, "lookback_days": lookback_days},
        ctx,
    )


__all__ = ["router"]
