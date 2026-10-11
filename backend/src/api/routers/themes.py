"""Theme/tier lookup REST — thin projections of the theme-lookup agent tools, dispatched through
`tools.base.dispatch_tool` exactly as the other market routes are, so the entitlement gate applies
uniformly (theme data is `public`, so every caller including anonymous gets real data).

This is platform reference data — classification and relationships, not user-scoped state — so
every route reads through `optional_user` (an anonymous caller is fine), mirroring how the pets
catalog is public while a user's own pets require sign-in. There is no user-scoped theme data.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from accounts.models import User
from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
    optional_user,
    tool_context_for,
)
from api.rate_guard import guard_reads
from schemas.enums import Market
from tools.base import ToolContext

_dispatch = dispatch_validated_tool


router = APIRouter(tags=["themes"], dependencies=[Depends(guard_reads)])


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


@router.get("/themes")
async def list_themes(
    kind: Literal["industry", "concept"] | None = Query(None),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The discovery-level theme catalog (screener README.md §6 主題 Themes sub-tab) — every
    active theme with its member count. `get_themes` defaults to `status="active"`."""
    args: dict[str, object] = {}
    if kind is not None:
        args["kind"] = kind
    return await _dispatch(state, "get_themes", args, ctx)


@router.get("/themes/{key}")
async def theme_detail(
    key: str,
    limit: int = Query(200, ge=1, le=1000),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """One theme's identity + its member symbols, each with sub-industry/confidence/source."""
    return await _dispatch(state, "get_theme_members", {"theme_key": key, "limit": limit}, ctx)


@router.get("/themes/{key}/tiers")
async def theme_tiers(
    key: str,
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """One theme's sub-industries grouped into upstream/midstream/downstream, each assignment
    marked curated (editorial) or heuristic (automatically classified)."""
    return await _dispatch(state, "get_theme_tiers", {"theme_key": key}, ctx)


@router.get("/symbols/{symbol}/themes")
async def symbol_themes(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The reverse lookup: every theme one symbol belongs to."""
    return await _dispatch(state, "get_symbol_themes", {"id": symbol, "market": market.value}, ctx)


@router.get("/themes/{key}/tier-heatmap")
async def theme_tier_heatmap(
    key: str,
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """個股 > 產業分析's 產業鏈 tier-tile view — a theme's upstream/midstream/downstream
    sub-industry tiles, each colored by that sub-industry's own members' real average price
    change over the latest trading day."""
    return await _dispatch(state, "get_theme_tier_heatmap", {"theme_key": key}, ctx)


@router.get("/symbols/{symbol}/supply-chain")
async def symbol_supply_chain(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """個股 > 產業分析's 供應鏈關係圖 card — every supply/customer/competitor edge involving one
    symbol, from either side."""
    return await _dispatch(
        state, "get_supply_chain_edges", {"id": symbol, "market": market.value}, ctx
    )


__all__ = ["router"]
