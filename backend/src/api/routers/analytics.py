"""Score/leaderboard REST — thin projections of `tools/score_tools.py`'s two agent tools,
dispatched through `tools.base.dispatch_tool` exactly as `themes.py`/`market_rest.py` do, so the
entitlement gate applies uniformly (both tools are `public`, so every caller including anonymous
gets real data).

No user-scoped state here — the composite score and the leaderboard are platform-wide derived
reads, so every route goes through `optional_user`, matching the theme routes' precedent.
"""

from __future__ import annotations

from datetime import date as date_
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from analytics.models import ScoreProfile
from analytics.scoring_store import LeaderboardDirection, LeaderboardUniverse
from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_session,
    get_state,
    optional_user,
    require_user,
    tool_context_for,
)
from api.errors import ApiError
from api.rate_guard import guard_reads
from memory.screener_screens import (
    ScreenerScreen,
    ScreenerScreenNameConflictError,
    ScreenerScreenNotFoundError,
    ScreenerScreenStore,
)
from schemas.enums import Market
from schemas.screener import ScreenerOperator
from tools.base import ToolContext

_dispatch = dispatch_validated_tool


router = APIRouter(tags=["analytics"], dependencies=[Depends(guard_reads)])


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


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


@router.get("/analytics/screener/factors")
async def screener_factors(
    market: Literal["TW", "US", "ETF"] = Query(...),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The per-market screener factor/signal catalog the condition builder discovers from
    (README.md §2.1) — `get_factor_catalog`."""
    return await _dispatch(state, "get_factor_catalog", {"market": market}, ctx)


@router.get("/analytics/screener/presets")
async def screener_presets(
    market: Literal["TW", "US", "ETF"] = Query(...),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Named starting-point condition sets for the `PresetLibrary` (README.md §3) —
    `get_screener_presets`."""
    return await _dispatch(state, "get_screener_presets", {"market": market}, ctx)


class ScreenerRunConditionBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: str = Field(min_length=1)
    op: ScreenerOperator
    value: float | str | None = None
    value2: float | str | None = None


class ScreenerRunGateOverridesBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_price: float | None = None
    min_avg_volume: float | None = None
    min_market_cap: float | None = None
    exclude_disposition: bool | None = None
    exclude_attention: bool | None = None
    exclude_full_cash_delivery: bool | None = None
    honeymoon_exclude_days: int | None = None
    exclude_leveraged_inverse: bool | None = None
    min_aum: float | None = None


class ScreenerRunRequest(BaseModel):
    """The `POST /analytics/screener/run` body — a JSON object rather than query params, since
    `conditions[]`/`gate_overrides` are nested structures that don't flatten trivially into a
    GET query string (same reasoning `market_rest.py`'s `_split_symbols` docstring gives for
    other nested-shape routes, but here the nesting is deep enough that even a flattened
    repeated-key encoding would be awkward, so this route is a plain POST instead)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["TW", "US", "ETF"]
    conditions: tuple[ScreenerRunConditionBody, ...] = Field(default_factory=tuple)
    match: Literal["AND", "OR"] = "AND"
    gate_overrides: ScreenerRunGateOverridesBody | None = None
    count_only: bool = False
    size: int = Field(default=50, ge=1, le=250)


@router.post("/analytics/screener/run")
async def screener_run(
    body: ScreenerRunRequest,
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Run the gates->setup screener funnel (README.md §1/§5) — `run_screener`. A descriptive
    filter result ('符合條件的清單，非推薦名單'), never a ranked/recommended list; pair with
    `GET /analytics/rankings` for the separate rank stage over this call's survivors."""
    return await _dispatch(state, "run_screener", body.model_dump(mode="json"), ctx)


def _screen_store(session: AsyncSession = Depends(get_session)) -> ScreenerScreenStore:
    return ScreenerScreenStore(session)


def _screen_not_found(screen_id: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="screener_screen_not_found",
        message=f"Screener screen {screen_id} not found.",
        retryable=False,
    )


def _screen_name_conflict(name: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="screener_screen_name_conflict",
        message=f"A saved screen named '{name}' already exists for this market.",
        retryable=False,
    )


def _screen_view(screen: ScreenerScreen) -> dict[str, object]:
    return screen.model_dump(mode="json")


@router.get("/analytics/screener/screens")
async def list_screener_screens(
    market: Literal["TW", "US", "ETF"] = Query(...),
    user: User = Depends(require_user),
    store: ScreenerScreenStore = Depends(_screen_store),
) -> dict[str, object]:
    """`SavedScreens`'s list read (README.md §6) — every screen the caller has saved for this
    market, most-recently-updated first."""
    screens = await store.list_screens(user_id=user.id, market=market)
    return {"screens": [_screen_view(screen) for screen in screens]}


class SaveScreenerScreenRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    market: Literal["TW", "US", "ETF"]
    conditions: tuple[ScreenerRunConditionBody, ...] = Field(default_factory=tuple)
    match: Literal["AND", "OR"] = "AND"
    gate_overrides: ScreenerRunGateOverridesBody | None = None


@router.post("/analytics/screener/screens", status_code=status.HTTP_201_CREATED)
async def save_screener_screen(
    body: SaveScreenerScreenRequest,
    user: User = Depends(require_user),
    store: ScreenerScreenStore = Depends(_screen_store),
) -> dict[str, object]:
    """Save the builder's current state as a new named screen (README.md §6). 409s on a name
    already used for this market — the client offers "另存新篩選" rather than clobbering."""
    try:
        screen = await store.save_screen(
            user_id=user.id,
            name=body.name,
            market=body.market,
            conditions=tuple(c.model_dump(mode="json") for c in body.conditions),
            match=body.match,
            gate_overrides=(
                body.gate_overrides.model_dump(mode="json")
                if body.gate_overrides is not None
                else None
            ),
        )
    except ScreenerScreenNameConflictError as exc:
        raise _screen_name_conflict(body.name) from exc
    return _screen_view(screen)


class UpdateScreenerScreenRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    conditions: tuple[ScreenerRunConditionBody, ...] | None = None
    match: Literal["AND", "OR"] | None = None
    gate_overrides: ScreenerRunGateOverridesBody | None = None


@router.patch("/analytics/screener/screens/{screen_id}")
async def update_screener_screen(
    screen_id: str,
    body: UpdateScreenerScreenRequest,
    user: User = Depends(require_user),
    store: ScreenerScreenStore = Depends(_screen_store),
) -> dict[str, object]:
    """Rename and/or replace a saved screen's conditions in place — the "dirty -> update" path
    (README.md §6), distinct from creating a new screen via `POST .../screens`."""
    try:
        screen = await store.update_screen(
            user_id=user.id,
            screen_id=screen_id,
            name=body.name,
            conditions=(
                tuple(c.model_dump(mode="json") for c in body.conditions)
                if body.conditions is not None
                else None
            ),
            match=body.match,
            gate_overrides=(
                body.gate_overrides.model_dump(mode="json")
                if body.gate_overrides is not None
                else None
            ),
        )
    except ScreenerScreenNotFoundError as exc:
        raise _screen_not_found(screen_id) from exc
    return _screen_view(screen)


@router.delete("/analytics/screener/screens/{screen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_screener_screen(
    screen_id: str,
    user: User = Depends(require_user),
    store: ScreenerScreenStore = Depends(_screen_store),
) -> None:
    """Idempotent delete — deleting a screen that isn't there (already gone, or never the
    caller's) is a no-op, never a 404, matching `DELETE /watchlists/{id}`'s own convention."""
    await store.delete_screen(user_id=user.id, screen_id=screen_id)


__all__ = ["router"]
