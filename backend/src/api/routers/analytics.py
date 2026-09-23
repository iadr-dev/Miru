"""Score/leaderboard REST — thin projections of `tools/score_tools.py`'s two agent tools,
dispatched through `tools.base.dispatch_tool` exactly as `themes.py`/`market_rest.py` do, so the
entitlement gate applies uniformly (both tools are `public`, so every caller including anonymous
gets real data).

No user-scoped state here — the composite score and the leaderboard are platform-wide derived
reads, so every route goes through `optional_user`, matching the theme routes' precedent.
"""

from __future__ import annotations

from datetime import date as date_

from fastapi import APIRouter, Depends, Query, status

from accounts.models import User
from analytics.models import ScoreProfile
from analytics.scoring_store import LeaderboardDirection, LeaderboardUniverse
from api.dependencies import AppState, get_state, optional_user, tool_context_for
from api.errors import ApiError
from schemas.enums import Market
from tools.base import ToolContext, dispatch_tool

router = APIRouter(tags=["analytics"])


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


@router.get("/analytics/{symbol}/score")
async def stock_score(
    symbol: str,
    market: Market = Query(Market.TW),
    profile: ScoreProfile = Query(ScoreProfile.SWING),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The stock-detail-page read: the cached composite + band + five-factor breakdown for one
    symbol, or `found=False` if today's batch has not yet computed it."""
    return await _dispatch(
        state,
        "get_stock_score",
        {"id": symbol, "market": market.value, "profile": profile.value},
        ctx,
    )


@router.get("/analytics/{symbol}/summary")
async def stock_summary(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The stock-detail-page AI-summary read: the cached (or freshly generated) headline +
    narrative + SWOT grounded in today's cached composite score. Sponsor-tier — an unentitled
    caller gets `dispatch_tool`'s whole-tool masked-teaser envelope, not real content."""
    return await _dispatch(
        state,
        "get_stock_summary",
        {"id": symbol, "market": market.value},
        ctx,
    )


@router.get("/analytics/rankings")
async def score_rankings(
    market: Market = Query(Market.TW),
    profile: ScoreProfile = Query(ScoreProfile.SWING),
    direction: LeaderboardDirection = Query("bullish"),
    universe: LeaderboardUniverse = Query("stock"),
    limit: int = Query(20, ge=1, le=50),
    as_of: date_ | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The whole-TW-universe leaderboard: top-N strongest (`direction=bullish`) or weakest
    (`direction=bearish`) cached composite scores — a sort-and-slice read of the nightly batch's
    cache, never a live rescan. `universe=stock` (default) or `universe=etf` — a stock and an ETF
    are scored on different factor sets, so they are never ranked in one combined list (see
    `analytics/scoring_store.py::get_leaderboard`'s own docstring)."""
    return await _dispatch(
        state,
        "get_score_leaderboard",
        {
            "market": market.value,
            "profile": profile.value,
            "direction": direction,
            "universe": universe,
            "limit": limit,
            "as_of": as_of.isoformat() if as_of is not None else None,
        },
        ctx,
    )


@router.get("/analytics/{symbol}/trend-score")
async def trend_score(
    symbol: str,
    market: Market = Query(Market.TW),
    days: int = Query(90, ge=1, le=250),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The 趨勢評分 tab read: the six-dimension trend-score composite (five cache-backed, one
    computed live for the requested symbol) plus trailing history for a chart, or `found=False`
    if today's trend-score batch has not yet computed it."""
    return await _dispatch(
        state,
        "get_trend_score",
        {"id": symbol, "market": market.value, "days": days},
        ctx,
    )


__all__ = ["router"]
