"""Public market-data reads: source-health matrix, adapter matrix, and one-shot EOD quotes.

These are the anonymous-allowed public reads. Each is a thin projection over an
already-built registry tool, dispatched through `tools.base.dispatch_tool` (never the tool's own
`run()`) so the sponsor-entitlement gate applies uniformly — a `public` quote is served to
everyone, and were any field `sponsor`, an unentitled caller would receive the masked teaser at
HTTP 200 rather than an error. `optional_user` supplies the (possibly anonymous) entitlement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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

_dispatch = dispatch_validated_tool


router = APIRouter(tags=["market"])


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


@router.get("/health")
async def source_health(
    symbols: str = Query(..., description="Comma-separated symbol ids, e.g. '2330'."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Source-health view for the given symbols (currentness + which upstreams are OK/stale)."""
    per_symbol = [
        await _dispatch(state, "get_source_health", {"id": sid, "market": market.value}, ctx)
        for sid in _split_symbols(symbols)
    ]
    return {"symbols": per_symbol}


@router.get("/sources")
async def source_matrix(
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The adapter/capability matrix — which `(capability, market)` pairs each source declares."""
    return await _dispatch(state, "get_source_matrix", {}, ctx)


@router.get("/quote")
async def quote(
    symbols: str = Query(..., description="Comma-separated symbol ids, e.g. '2330,2317'."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Latest end-of-day price bar per symbol (one-shot; the live-ish feed is a separate SSE)."""
    quotes = [
        await _dispatch(state, "get_price_quote", {"id": sid, "market": market.value}, ctx)
        for sid in _split_symbols(symbols)
    ]
    return {"quotes": quotes}


def _split_symbols(symbols: str) -> list[str]:
    ids = [part.strip() for part in symbols.split(",") if part.strip()]
    if not ids:
        raise ApiError(
            status_code=422,
            code="invalid_request",
            message="At least one symbol id is required.",
            retryable=False,
        )
    return ids


__all__ = ["router"]
