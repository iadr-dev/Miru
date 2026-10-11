"""Derivatives, macro, calendar and news/PTT routes."""

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

router = APIRouter()
_dispatch = dispatch_validated_tool


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


@router.get("/market/macro-calendar")
async def macro_calendar(
    impact: Literal["high", "medium", "low"] | None = Query(None),
    days_ahead: int = Query(14, ge=1, le=90),
    from_date: date_ | None = Query(None),
    to_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """US/global macro-release events (GDP/CPI/PCE/PMI/NFP/FOMC) with consensus/prior/actual,
    backed by `get_macro_calendar` (financecalendar.com, `DataClass.PUBLIC`) — a distinct
    third-party feed from `get_calendar_events_feed`'s existing yfinance economic calendar, see
    that tool's own docstring. `from_date`/`to_date` (given together) override `days_ahead` and
    can address a genuinely past range, for a caller paging back to a prior month/week."""
    args: dict[str, object] = {"days_ahead": days_ahead}
    if impact is not None:
        args["impact"] = impact
    if from_date is not None:
        args["from_date"] = from_date.isoformat()
    if to_date is not None:
        args["to_date"] = to_date.isoformat()
    return await _dispatch(state, "get_macro_calendar", args, ctx)


@router.get("/market/news-feed")
async def news_feed(
    query: str = Query("台股"),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """即時新聞 — near-real-time news aggregated from financial RSS + optional Finnhub, backed by
    `get_news_feed`, `DataClass.PUBLIC`. `market=TW` (default): `query` scopes the Finnhub
    company-news call; RSS feeds are always fetched whole (not query-filterable upstream). Any
    other market: RSS is skipped (TW-only outlets) and Finnhub's general-market-news category is
    used instead of a `query`-scoped company-news call — see `get_news_feed`'s own docstring."""
    return await _dispatch(state, "get_news_feed", {"query": query, "market": market.value}, ctx)


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


__all__ = ["router"]
