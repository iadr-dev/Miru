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
from typing import Literal

from fastapi import APIRouter, Depends, Query, status

from accounts.models import User
from api.dependencies import AppState, get_state, optional_user, tool_context_for
from api.errors import ApiError
from schemas.enums import Market
from tools.base import ToolContext, dispatch_tool
from tools.international_tools import FinMindInternationalMarket

router = APIRouter(tags=["market-data"])


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


async def _dispatch(
    state: AppState, tool_name: str, args: dict[str, object], ctx: ToolContext
) -> dict[str, object]:
    tool = state.tool_registry.get_tool(tool_name)
    if tool is None:  # pragma: no cover — registry always has these; guards a wiring regression
        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="tool_unavailable",
            message=f"Tool {tool_name} is not registered.",
            retryable=False,
        )
    validated = tool.input_model.model_validate(args)
    result = await dispatch_tool(tool, validated, ctx)
    return result.model_dump(mode="json")


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
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Whole-market industry/sector heatmap (產業別漲跌熱力圖) for one trading day (defaults to
    the latest available day): each industry's average close-to-close percentage change
    (trading-value-weighted when available) and constituent stock count — the backing tool's
    input is a single `trade_date`, not a start/end range, same shape as `/advance-decline` and
    `/index-contribution`."""
    args: dict[str, object] = {"market": market.value}
    if trade_date is not None:
        args["trade_date"] = trade_date.isoformat()
    return await _dispatch(state, "get_sector_heatmap", args, ctx)


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


@router.get("/market/symbol-info")
async def symbol_info(
    symbols: list[str] = Query(
        ..., min_length=1, max_length=20, description="Up to 20 symbol ids, e.g. '2330'."
    ),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Batched symbol->name (+ industry/security-type) lookup, e.g. to enrich a bare-symbol
    ranking (`get_score_leaderboard`) with a display name. A symbol absent from the returned
    `rows` is not in the current whole-market roster — never a fabricated name in its place."""
    args: dict[str, object] = {"symbols": symbols, "market": market.value}
    return await _dispatch(state, "get_symbol_info", args, ctx)


@router.get("/market/price-history")
async def price_history(
    symbols: list[str] = Query(
        ..., min_length=1, max_length=20, description="Up to 20 symbol ids, e.g. '2330'."
    ),
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
        state, "get_price_history", {"symbols": symbols, "market": market.value, "days": days}, ctx
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


__all__ = ["router"]
