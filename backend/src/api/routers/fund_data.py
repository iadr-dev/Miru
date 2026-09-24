"""Fund/analyst-data REST — thin projections of the previously tool-only `analyst_tools.py` and
`etf_overlap_tools.py` agent tools, dispatched through `tools.base.dispatch_tool` exactly as
`themes.py`/`market_rest.py` do, so the entitlement gate applies uniformly (every tool wrapped
here is `public`, so every caller including anonymous gets real data).

This is a new router rather than an extension of `market_rest.py`: that router's own docstring
scopes it to market/derivatives/macro reads, and everything here is fund/holder/options-chain
shaped (`analyst_tools.py`'s non-TW-primary capability set plus the ETF-overlap pair) — a
distinct enough concern to warrant its own file, matching how `themes.py` and `analytics.py`
(the `score_tools.py` projection) each got their own router rather than being folded into
`market_rest.py`.

List-typed query params (`get_etf_overlap`'s 3-10 symbols, `get_fund_comparison`'s 2-10
`(id, market)` pairs) follow this codebase's one existing multi-symbol REST convention —
`market.py`'s `/quote?symbols=2330,2317` comma-separated string, parsed with the same
`_split_symbols` shape — rather than a repeated `?symbols=a&symbols=b` query param, for which
this codebase has no other precedent. `get_fund_comparison`'s comparison list needs a market per
symbol (unlike the single-market overlap-checker), so each entry is `id:market`
(e.g. `0050:TW,SPY:US`) rather than a bare id.

Both list-bounded tools' own Pydantic input models enforce the real min/max symbol counts
(`GetEtfOverlapInput`/`GetFundComparisonInput` in `etf_overlap_tools.py`) — `model_validate`
raising `pydantic.ValidationError` for an out-of-bounds count is caught here and turned into the
standard 422 envelope, the same shape `RequestValidationError` renders for a bad query param, so
an out-of-bounds symbol count never 500s.
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

router = APIRouter(tags=["fund-data"])

# The numeric literal, matching `api/errors.py`'s own convention — the `status.HTTP_422_*`
# constant's name churns across Starlette versions (entity → content) and emits a deprecation
# warning; the code is stable. Still needed in this file for the list-bounded-symbol-count 422s
# below, even though `_dispatch` itself now delegates to the shared `dispatch_validated_tool`.
_HTTP_422_UNPROCESSABLE = 422

_MIN_OVERLAP_SYMBOLS = 3
_MAX_OVERLAP_SYMBOLS = 10
_MIN_COMPARISON_SYMBOLS = 2
_MAX_COMPARISON_SYMBOLS = 10


def _context(
    user: User | None = Depends(optional_user),
    state: AppState = Depends(get_state),
) -> ToolContext:
    return tool_context_for(user, state)


# `GetEtfOverlapInput`/`GetFundComparisonInput`'s own min/max-symbol-count validators are what
# actually trips `dispatch_validated_tool`'s 422 path here (see this module's own docstring).
_dispatch = dispatch_validated_tool


# ---- per-symbol analyst-data reads (non-TW) ------------------------------------------------


@router.get("/funds/{symbol}/options-chain")
async def options_chain(
    symbol: str,
    market: Market = Query(Market.US),
    expiry: str | None = Query(
        None, description="Option expiry date (ISO). Omitted means the nearest expiry."
    ),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Option chain (calls + puts, one expiry) for one non-TW symbol."""
    args: dict[str, object] = {"id": symbol, "market": market.value}
    if expiry is not None:
        args["expiry"] = expiry
    return await _dispatch(state, "get_options_chain", args, ctx)


@router.get("/funds/{symbol}/institutional-holders")
async def institutional_holders(
    symbol: str,
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Institutional + mutual-fund holder rows for one non-TW symbol."""
    return await _dispatch(
        state, "get_institutional_holders", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/funds/{symbol}/insider-activity")
async def insider_activity(
    symbol: str,
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Insider (Form-4-derived) transaction rows for one non-TW symbol."""
    return await _dispatch(
        state, "get_insider_activity", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/funds/{symbol}/holdings")
async def fund_holdings(
    symbol: str,
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Top-holdings + sector-weighting rows for one non-TW ETF/mutual fund."""
    return await _dispatch(state, "get_fund_holdings", {"id": symbol, "market": market.value}, ctx)


@router.get("/funds/{symbol}/fund-info")
async def etf_fund_info(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Fund-level reference info for one TW listed ETF (issuer, tracked index, units outstanding,
    listed/inception dates, manager person, custodian bank) — not per-constituent holdings or
    NAV/IOPV. Backed by `get_etf_fund_info` over TWSE's fund-basic-data disclosure."""
    return await _dispatch(state, "get_etf_fund_info", {"id": symbol, "market": market.value}, ctx)


@router.get("/funds/{symbol}/expense-ratio")
async def etf_expense_ratio(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Internal-fee (內扣費用) disclosure for one TW fund/ETF: management fee, custody fee,
    custodian bank, and an approximate TER floor (management + custody). Backed by
    `get_etf_expense_ratio` over the SITCA monthly fee report — see that tool's caveat on why the
    TER is a floor, not the audited total-expense-ratio."""
    return await _dispatch(
        state, "get_etf_expense_ratio", {"id": symbol, "market": market.value}, ctx
    )


@router.get("/funds/{symbol}/theme-tags")
async def etf_theme_tags(
    symbol: str,
    market: Market = Query(Market.TW),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """TWSE's own 主題/因子 (theme/factor) editorial tags for one TW listed ETF (e.g. 高股息/
    科技主題型/公司治理及ESG) — zero, one, or several tags. Backed by `get_etf_theme_tags` over
    the ETF e添富 microsite's own per-symbol page."""
    return await _dispatch(state, "get_etf_theme_tags", {"id": symbol, "market": market.value}, ctx)


@router.get("/funds/{symbol}/premium-discount")
async def etf_premium_discount(
    symbol: str,
    market: Market = Query(Market.TW),
    days: int = Query(60, ge=2, le=180),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """折溢價 (premium/discount to NAV) history + latest AUM for one TW ETF, over a bounded
    trailing trading-day window. Backed by `get_etf_premium_discount` over the already-ingested
    `etf_nav`/`etf_aum` datasets — never a live per-request NAV fetch."""
    return await _dispatch(
        state,
        "get_etf_premium_discount",
        {"id": symbol, "market": market.value, "days": days},
        ctx,
    )


# ---- multi-symbol overlap / comparison -----------------------------------------------------


@router.get("/funds/overlap")
async def etf_overlap(
    symbols: str = Query(
        ...,
        description=(
            "Comma-separated ETF/fund symbols, 3-10, same non-TW market, e.g. 'SPY,VOO,IVV'."
        ),
    ),
    market: Market = Query(Market.US),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Constituent-overlap comparison across 3+ non-TW ETFs/funds — the explicit fix for
    'existing tools only compare 2 funds at a time'. Never available for TW ETFs (see
    `sources/etf_pcf.py`'s documented per-constituent-holdings gap)."""
    ids = _split_symbols(symbols, min_count=_MIN_OVERLAP_SYMBOLS, max_count=_MAX_OVERLAP_SYMBOLS)
    return await _dispatch(state, "get_etf_overlap", {"symbols": ids, "market": market.value}, ctx)


@router.get("/funds/comparison")
async def fund_comparison(
    symbols: str = Query(
        ...,
        description=(
            "Comma-separated 'id:market' pairs, 2-10, any market (incl. TW), e.g. '0050:TW,SPY:US'."
        ),
    ),
    lookback_days: int = Query(
        365, ge=1, le=3650, description="Total-return lookback window in calendar days."
    ),
    state: AppState = Depends(get_state),
    ctx: ToolContext = Depends(_context),
) -> dict[str, object]:
    """Total-return-with-dividends-reinvested comparison across 2+ symbols in any market
    (including TW). Never computes overlap%/NAV/premium-discount for TW symbols — a documented,
    honest adapter gap relayed in each TW row's `unavailable_note`."""
    pairs = _split_symbol_market_pairs(
        symbols, min_count=_MIN_COMPARISON_SYMBOLS, max_count=_MAX_COMPARISON_SYMBOLS
    )
    return await _dispatch(
        state,
        "get_fund_comparison",
        {"symbols": pairs, "lookback_days": lookback_days},
        ctx,
    )


def _split_symbols(raw: str, *, min_count: int, max_count: int) -> list[str]:
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not (min_count <= len(ids) <= max_count):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_request",
            message=f"Expected between {min_count} and {max_count} symbols, got {len(ids)}.",
            retryable=False,
        )
    return ids


def _split_symbol_market_pairs(
    raw: str, *, min_count: int, max_count: int
) -> list[dict[str, object]]:
    tokens = [part.strip() for part in raw.split(",") if part.strip()]
    if not (min_count <= len(tokens) <= max_count):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_request",
            message=f"Expected between {min_count} and {max_count} symbols, got {len(tokens)}.",
            retryable=False,
        )
    pairs: list[dict[str, object]] = []
    for token in tokens:
        if ":" not in token:
            raise ApiError(
                status_code=_HTTP_422_UNPROCESSABLE,
                code="invalid_request",
                message=f"Expected 'id:market' pairs, got {token!r}.",
                retryable=False,
            )
        symbol_id, market_id = token.split(":", 1)
        try:
            market = Market(market_id.strip().upper())
        except ValueError as exc:
            raise ApiError(
                status_code=_HTTP_422_UNPROCESSABLE,
                code="invalid_request",
                message=f"Unknown market {market_id!r} in {token!r}.",
                retryable=False,
            ) from exc
        pairs.append({"id": symbol_id.strip(), "market": market.value})
    return pairs


__all__ = ["router"]
