"""Thin REST over the already-built market / derivatives / macro agent tools.

Each route is a direct projection of one (or a small bundle of) registry tool(s), dispatched
through `tools.base.dispatch_tool` exactly as `market.py`'s `/quote` does — never a tool's own
`run()` — so the sponsor-entitlement gate applies uniformly: a Sponsor-classed tool returns the
masked-teaser shape at HTTP 200 for an unentitled caller, real data for a Premium/Pro one.
`optional_user` supplies the (possibly anonymous) entitlement.

Four routes the consolidated interface reference lists here are deliberately absent: their named
backing tools (`get_market_context`, `get_advance_decline`, `get_index_contribution`,
`get_sector_indices`) were never built — no adapter/capability backs them — so wiring a route
that dispatched a nonexistent tool would 500 by construction. They are omitted rather than faked.
"""

from __future__ import annotations

from datetime import date as date_

from fastapi import APIRouter, Depends, Query, status

from accounts.models import User
from api.dependencies import AppState, get_state, optional_user, tool_context_for
from api.errors import ApiError
from schemas.enums import Market
from tools.base import ToolContext, dispatch_tool

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


# ---- derivatives ---------------------------------------------------------------------------


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
