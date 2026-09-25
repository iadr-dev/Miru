"""Context & market-data reads: canonical/conversation packages, symbol type-ahead, and the two
SSE feeds (live-ish quote, cache-invalidation).

All reads dispatch through `tools.base.dispatch_tool` (never a tool's `run()`) so the sponsor
gate applies uniformly. `optional_user` supplies the (possibly anonymous) entitlement — public
reads tolerate an anonymous caller.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from accounts.models import User
from api.dependencies import AppState, get_state, optional_user, tool_context_for
from api.errors import ApiError
from api.quote_stream import entitlement_transform, quote_tool_name_for
from api.sse import format_sse_event
from schemas.enums import Market, Tier
from tools.base import ToolContext, dispatch_tool

router = APIRouter(tags=["context"])

_CANONICAL = "canonical"
_CONVERSATION = "conversation"
_QUOTE_STREAM_FEATURE = "live_quote_stream"
# Non-TW markets yfinance's free-text symbol search actually serves (the backing tool refuses TW).
_SEARCH_SERVED_MARKETS = frozenset({Market.US, Market.GB, Market.JP, Market.EU})


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


@router.get("/context/{package}")
async def context_package(
    package: str,
    symbol: str = Query(..., min_length=1, description="Symbol id the package is built for."),
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """The canonical (`canonical`) or AI-safe conversation (`conversation`) package for one
    symbol. Both backing tools are per-symbol, so `symbol` is required — there is no whole-market
    package build (that would violate the full-market-scan guard)."""
    if package == _CANONICAL:
        tool_name = "get_canonical_context"
    elif package == _CONVERSATION:
        tool_name = "build_conversation_package"
    else:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="unknown_package",
            message=f"Unknown context package {package!r} (expected canonical|conversation).",
            retryable=False,
        )
    return await _dispatch(state, tool_name, {"id": symbol, "market": market.value}, ctx)


@router.get("/search")
async def symbol_search(
    q: str = Query(..., min_length=1, description="Free-text company name or ticker."),
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Symbol type-ahead over the free-text search capability.

    NOTE on coverage: the only built symbol-search tool (`get_symbol_search`, yfinance) serves
    non-TW markets (US/GB/JP/EU) — there is no ingested TaiwanStockInfo/ETF symbol-universe
    search service backing a TW/ETF type-ahead today, so `market=TW` returns a clear
    `not_supported` rather than a naive whole-roster scan masquerading as a ranked type-ahead.
    """
    if market not in _SEARCH_SERVED_MARKETS:
        raise ApiError(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code="not_supported",
            message=(
                "Symbol type-ahead is only backed for US/GB/JP/EU today; a TW/ETF "
                "symbol-universe search service is not yet built."
            ),
            retryable=False,
        )
    return await _dispatch(state, "get_symbol_search", {"query": q, "market": market.value}, ctx)


@router.get("/quote/stream")
async def quote_stream(
    symbols: str = Query(..., description="Comma-separated symbol ids."),
    market: Market = Query(Market.TW),
    cadence: Literal["normal", "saver"] = Query(
        "normal",
        description="Poll-cadence hint: `normal` (~5s) or `saver` (coarser, for metered/"
        "mobile connections). The poller serves a symbol at the fastest cadence any subscriber "
        "requested, so a `normal` subscriber is never starved by a co-subscribed `saver`.",
    ),
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> StreamingResponse:
    """Live-ish quote feed (SSE) via the shared demand-driven poller.

    One poller per worker fetches each demanded symbol once per tick — through `dispatch_tool`
    over `get_price_quote`, exactly the one-shot `/quote` path — and fans the snapshot out to
    every subscriber, so N clients watching one symbol cost one upstream fetch, not N. Subscribing
    increments a Redis demand refcount; disconnecting decrements it, and at zero the symbol leaves
    the demand set so the poller stops touching it (bounded observation, never an unattended
    scan). Each `quote` frame carries `as_of` (the snapshot's own source timestamp) and
    `market_open`; off-hours the poller idles.

    Entitlement is unchanged from the one-shot read: the shared fetch pulls real data, and each
    connection re-applies `get_price_quote`'s own masking to its frames (via the same
    `MaskedTeaser` shape `dispatch_tool` uses), so an unentitled caller sees the masked teaser per
    tick, never a hard error. Over-cap symbols (per-connection or global) are dropped from this
    subscription rather than crashing the connection.
    """
    ids = [part.strip() for part in symbols.split(",") if part.strip()]
    if not ids:
        raise ApiError(
            status_code=422,
            code="invalid_request",
            message="At least one symbol id is required.",
            retryable=False,
        )
    tier = ctx.tier if user is not None else Tier.FREE
    transform = entitlement_transform(
        state.tool_registry, tier, tool_name=quote_tool_name_for(market)
    )
    stream = state.quote_feed.stream(ids, market=market, cadence=cadence, transform=transform)
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/updates/stream")
async def updates_stream(
    state: AppState = Depends(get_state),
    _user: User | None = Depends(optional_user),
) -> StreamingResponse:
    """Cache-invalidation feed (SSE): `data.updated` frames when a derived-compute batch lands.

    The ingest leader publishes a lightweight `{capability, market, as_of, scope}` signal the
    moment a whole-market partition lands; this route subscribes to that signal's transport
    (in-process for the single-process backend, Redis pub/sub cross-worker in the hosted topology)
    and relays each frame so the frontend invalidates exactly the affected keys instead of waiting
    out a fixed time boundary.
    """

    async def _updates() -> AsyncIterator[str]:
        subscription = state.data_update_broker.reader_factory.updates()
        with contextlib.suppress(BaseException):
            async for event in subscription:
                yield format_sse_event(event)

    return StreamingResponse(_updates(), media_type="text/event-stream")


__all__ = ["router"]
