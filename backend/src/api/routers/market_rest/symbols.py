"""Symbol-level `/market/*` routes: tag screener, symbol info, price history, quotes."""

from __future__ import annotations

from datetime import date as date_
from typing import Literal

from fastapi import APIRouter, Depends, Query, status

from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_state,
)
from api.errors import ApiError
from api.routers.market_rest._common import context as _context
from api.tool_responses import tool_responses
from schemas.enums import Market
from tools.base import ToolContext

_SYMBOL_IDS_DESCRIPTION = "Up to 20 comma-separated symbol ids, e.g. '2330,2454'."

router = APIRouter()
_dispatch = dispatch_validated_tool


@router.get("/market/tag-catalog", response_model=None, responses=tool_responses("get_tag_catalog"))
async def tag_catalog(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The discoverable tap-tile screener tag list, grouped by category with bilingual labels —
    `get_tag_catalog` takes no input, so this route dispatches with an empty args dict."""
    return await _dispatch(state, "get_tag_catalog", {}, ctx)


@router.get(
    "/market/tag-screener", response_model=None, responses=tool_responses("run_tag_screener")
)
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
    symbols: str | None = Query(
        None,
        description="Up to 20 comma-separated symbol ids — narrows the match to just these "
        "symbols, e.g. a stock-detail badge asking 'does 2330 carry 爆量 today'.",
    ),
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
    if symbols is not None:
        args["symbols"] = _split_symbols(symbols)
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


@router.get("/market/symbol-info", response_model=None, responses=tool_responses("get_symbol_info"))
async def symbol_info(
    symbols: str = Query(..., description=_SYMBOL_IDS_DESCRIPTION),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Batched symbol->name (+ industry/security-type) lookup, e.g. to enrich a bare-symbol
    ranking (`get_score_leaderboard`) with a display name. A symbol absent from the returned
    `rows` is not in the current whole-market roster — never a fabricated name in its place."""
    args: dict[str, object] = {"symbols": _split_symbols(symbols), "market": market.value}
    return await _dispatch(state, "get_symbol_info", args, ctx)


@router.get(
    "/market/price-history", response_model=None, responses=tool_responses("get_price_history")
)
async def price_history(
    symbols: str = Query(..., description=_SYMBOL_IDS_DESCRIPTION),
    market: Market = Query(Market.TW),
    days: int = Query(20, ge=2, le=260, description="Trailing trading-day count, 2-260."),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """A bounded trailing-day OHLCV window (up to 260 trading days) for up to 20 symbols — the
    data behind a sparkline, a compare chart or a client-computed %-change, for any bare-symbol
    list (leaderboard, watchlist, ETF roster). `get_price_quote`/`/quote` stay latest-bar-only;
    this is the separate bounded-history capability those callers need instead."""
    return await _dispatch(
        state,
        "get_price_history",
        {"symbols": _split_symbols(symbols), "market": market.value, "days": days},
        ctx,
    )


@router.get("/market/quotes", response_model=None, responses=tool_responses("get_symbol_quotes"))
async def symbol_quotes(
    symbols: str = Query(..., description=_SYMBOL_IDS_DESCRIPTION),
    market: Market = Query(Market.TW),
    days: int = Query(20, ge=2, le=60, description="Sparkline window in trading days, 2-60."),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The list-row view for up to 20 symbols: name, latest close, DAILY change/change %,
    volume and a sparkline, in one call. Lists use this instead of combining `symbol-info` +
    `price-history` client-side, so every surface shows the same daily change."""
    return await _dispatch(
        state,
        "get_symbol_quotes",
        {"symbols": _split_symbols(symbols), "market": market.value, "days": days},
        ctx,
    )


__all__ = ["router"]
