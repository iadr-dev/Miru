"""Per-stock `/stock/*` detail routes."""

from __future__ import annotations

from datetime import date as date_
from typing import Literal, cast

from fastapi import APIRouter, Depends, Query

from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
)
from api.routers.market_rest._common import context as _context
from api.tool_responses import tool_responses
from schemas.enums import Market
from tools.base import ToolContext

router = APIRouter()
_dispatch = dispatch_validated_tool


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


@router.get(
    "/stock/company-profile", response_model=None, responses=tool_responses("get_company_profile")
)
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


@router.get(
    "/stock/insider-shareholding",
    response_model=None,
    responses=tool_responses("get_insider_shareholding"),
)
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


@router.get(
    "/stock/insider-transfer-declarations",
    response_model=None,
    responses=tool_responses("get_insider_transfer_declarations"),
)
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


@router.get("/stock/esg-score", response_model=None, responses=tool_responses("get_esg_score"))
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


@router.get(
    "/stock/disposition-securities",
    response_model=None,
    responses=tool_responses("get_disposition_securities"),
)
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


@router.get(
    "/stock/dividend-result", response_model=None, responses=tool_responses("get_dividend_result")
)
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


@router.get(
    "/stock/dividend-policy-summary",
    response_model=None,
    responses=tool_responses("get_dividend_policy_summary"),
)
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


@router.get(
    "/stock/month-revenue", response_model=None, responses=tool_responses("get_month_revenue")
)
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


@router.get(
    "/stock/financial-statement",
    response_model=None,
    responses=tool_responses("get_financial_statement"),
)
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


@router.get(
    "/stock/institutional-flow",
    response_model=None,
    responses=tool_responses("get_institutional_flow"),
)
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


@router.get(
    "/stock/margin-short", response_model=None, responses=tool_responses("get_margin_short")
)
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


@router.get(
    "/stock/securities-lending",
    response_model=None,
    responses=tool_responses("get_securities_lending"),
)
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


@router.get("/stock/chip-flows", response_model=None, responses=tool_responses("get_chip_flows"))
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


@router.get(
    "/stock/government-bank-flows",
    response_model=None,
    responses=tool_responses("get_government_bank_flows"),
)
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


@router.get(
    "/stock/valuation-ratios", response_model=None, responses=tool_responses("get_valuation_ratios")
)
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


@router.get(
    "/stock/analyst-consensus",
    response_model=None,
    responses=tool_responses("get_analyst_consensus"),
)
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


@router.get("/stock/sec-filings", response_model=None, responses=tool_responses("get_sec_filings"))
async def sec_filings(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The 新聞公告 tab's SEC filings list — most-recent 10-K/10-Q/8-K/etc. from EDGAR, backed by
    `get_sec_filings`, `DataClass.PUBLIC`, US-only (no equivalent feed for any other market)."""
    return await _dispatch(state, "get_sec_filings", {"id": symbol, "market": market.value}, ctx)


@router.get(
    "/stock/live-price-quote", response_model=None, responses=tool_responses("get_live_price_quote")
)
async def live_price_quote(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """`AssetHeader`'s one-shot price read for US symbols, backed by `get_live_price_quote`
    (yfinance WebSocket tick, falling back to a `.info` snapshot when no tick has streamed in
    yet — e.g. market closed). `DataClass.PUBLIC`. Distinct from `GET /quote/stream`'s SSE feed:
    that feed's poller only fetches while the market session is open (bounded observation), so a
    page load outside market hours needs this one-shot read instead to show a last-known price."""
    return await _dispatch(
        state, "get_live_price_quote", {"id": symbol, "market": market.value}, ctx
    )


@router.get(
    "/stock/calendar-events-feed",
    response_model=None,
    responses=tool_responses("get_calendar_events_feed"),
)
async def calendar_events_feed(
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The US 總覽 tab's upcoming-events calendar — earnings/IPO/economic calendars bundled from
    `yfinance.Calendars`, backed by `get_calendar_events_feed`, `DataClass.PUBLIC`. Market-wide
    (no `symbol` param — `MarketScopedInput` has none either), non-TW only."""
    return await _dispatch(state, "get_calendar_events_feed", {"market": market.value}, ctx)


@router.get("/stock/peers", response_model=None, responses=tool_responses("get_stock_peers"))
async def stock_peers(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The 產業同業 tab's sector/industry peer list — resolves the symbol's own classification then
    fetches its peers, backed by `get_stock_peers`, `DataClass.PUBLIC`."""
    return await _dispatch(state, "get_stock_peers", {"id": symbol, "market": market.value}, ctx)


@router.get(
    "/stock/calendar-events", response_model=None, responses=tool_responses("get_calendar_events")
)
async def stock_calendar_events(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """研究圖表's 個股事件calendar — feed-sourced 除權息/CB events, backed by
    `get_calendar_events`, `DataClass.PUBLIC` (the Sponsor CB events are added for Premium/Pro
    only). Distinct from `/analytics/...`-namespaced routes; this one is symbol-scoped."""
    return await _dispatch(
        state, "get_calendar_events", _symbol_date_args(symbol, market, start_date, end_date), ctx
    )


@router.get(
    "/stock/finmind-news", response_model=None, responses=tool_responses("get_finmind_stock_news")
)
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


@router.get(
    "/stock/foreign-shareholding",
    response_model=None,
    responses=tool_responses("get_foreign_shareholding"),
)
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


@router.get("/stock/day-trading", response_model=None, responses=tool_responses("get_day_trading"))
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


@router.get(
    "/stock/technical-indicators",
    response_model=None,
    responses=tool_responses("get_technical_indicators"),
)
async def technical_indicators(
    symbol: str = Query(..., min_length=1),
    market: Market = Query(Market.TW),
    start_date: date_ | None = Query(None),
    end_date: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """技術分析's K線 chart data source — MA/Bollinger/KD/MACD series, 支撐/壓力 levels,
    candlestick-pattern hits, and multi-bar chart patterns over a date range, backed by
    `get_technical_indicators`, `DataClass.PUBLIC`."""
    return await _dispatch(
        state,
        "get_technical_indicators",
        _symbol_date_args(symbol, market, start_date, end_date),
        ctx,
    )


@router.get(
    "/stock/shareholder-dispersion",
    response_model=None,
    responses=tool_responses("get_shareholder_dispersion"),
)
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


@router.get(
    "/stock/etfs-holding-stock",
    response_model=None,
    responses=tool_responses("get_etfs_holding_stock"),
)
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


@router.get(
    "/stock/live-quote", response_model=None, responses=tool_responses("get_live_quote_snapshot")
)
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


@router.get("/stock/price-limit", response_model=None, responses=tool_responses("get_price_limit"))
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


@router.get(
    "/stock/intraday-kbar", response_model=None, responses=tool_responses("get_intraday_kbar")
)
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


@router.get("/stock/price-tick", response_model=None, responses=tool_responses("get_price_tick"))
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


@router.get(
    "/stock/ptt-search", response_model=None, responses=tool_responses("get_ptt_stock_search")
)
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
