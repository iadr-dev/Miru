"""Equity/ETF/fund screener query -> matching symbols — the screener carve-out.

A screener request is query-shaped, not `(capability, symbol)`-shaped — there is no symbol to
route through `dataaccess/router.py`'s `fetch(cap, sym, **params)` seam, and forcing one
(e.g. a dummy/sentinel `Symbol`) would misrepresent what the router's per-adapter-fallback
logic means: there is exactly one adapter that can run a Yahoo screener query, so there is
nothing to "fall back" between, and the `(market, capability) -> [source_id,...]` priority
config has no meaningful `market` axis for a query that can itself span many countries via
its own `region`/`exchange` conditions. This mirrors the two existing non-per-symbol
carve-outs in this codebase (`FinMindAdapter.fetch_whole_market`, `batch_download`): a plain
module-level function, not part of `SourceAdapter`, called directly by whatever future tool
needs it (the ingest layer for those two; a screener tool for this one) — never reachable
through the router.

Coverage scope: only the equity/ETF/fund kinds are wired. Yahoo's screener endpoint returns
whatever symbols match a query regardless of country (its own `region`/`exchange` fields are
just more condition fields, not a query-level market switch), so this is honestly a *global*
query surface, not scoped to any one of this codebase's `Market` values — a caller narrows to
one country the same way any other Yahoo screener condition narrows anything, by adding a
`region`/`exchange` equality condition. In practice Yahoo's own screener coverage and this
codebase's stated "yfinance primary for US" policy make US the market this is actually
validated against (see the live network test); GB/JP/EU-only queries are unverified by this
pass and not claimed as validated here.

Note: this module is named `screener.py` inside the `sources.yfinance` package, which
collides in *name* (not import path) with `schemas/screener.py` — every reference to the
schema types below uses an explicit `from schemas.screener import ...`, never a bare
`import screener`, to avoid any ambiguous-import situation.
"""

from __future__ import annotations

from typing import Any, cast

import anyio.to_thread
import structlog

from schemas.screener import (
    ScreenerAssetKind,
    ScreenerCondition,
    ScreenerHit,
    ScreenerOperator,
    ScreenerQuery,
    ScreenerResult,
)

from ._shared import _as_float, _as_int, _as_str, _get, _init_tz_cache

logger = structlog.get_logger(__name__)

# This vocabulary is identical across `EquityQuery`/`ETFQuery`/`FundQuery` (all subclass the same
# `QueryBase`), so one mapping table serves all three asset kinds. Gaps, documented rather than
# silently dropped: this codebase's own operator set has no equivalent of yfinance's `AND`/`OR`
# composition operators — those aren't per-condition operators here, they're `ScreenerQuery.match`
# instead, so every leaf condition below maps 1:1 with no residual gap in either direction.
_SCREENER_OP_MAP: dict[ScreenerOperator, str] = {
    ScreenerOperator.GT: "gt",
    ScreenerOperator.GTE: "gte",
    ScreenerOperator.LT: "lt",
    ScreenerOperator.LTE: "lte",
    ScreenerOperator.EQ: "eq",
    ScreenerOperator.BETWEEN: "btwn",
    ScreenerOperator.IN: "is-in",
}


def _condition_operand(condition: ScreenerCondition) -> list[Any]:
    if condition.op is ScreenerOperator.BETWEEN:
        return [condition.field, condition.value, condition.value2]
    if condition.op is ScreenerOperator.IN:
        return [condition.field, *(condition.values or ())]
    return [condition.field, condition.value]


def _build_query(asset_kind: ScreenerAssetKind, q: ScreenerQuery) -> Any:
    """Build a `yfinance` `EquityQuery`/`ETFQuery`/`FundQuery` tree from our own condition
    vocabulary. Raises `ValueError` on an unknown field/value for that asset kind — the query
    SDK itself validates against its documented field set; this function never pre-guesses or
    silently drops a condition it can't map."""
    import yfinance as yf  # noqa: PLC0415

    query_cls: type[Any] = {
        ScreenerAssetKind.EQUITY: yf.EquityQuery,
        ScreenerAssetKind.ETF: yf.ETFQuery,
        ScreenerAssetKind.FUND: yf.FundQuery,
    }[asset_kind]

    leaves = [query_cls(_SCREENER_OP_MAP[c.op], _condition_operand(c)) for c in q.conditions]
    if len(leaves) == 1:
        return leaves[0]
    return query_cls(q.match.lower(), leaves)


def _run_screener_blocking(asset_kind: ScreenerAssetKind, q: ScreenerQuery) -> dict[str, Any]:
    import yfinance as yf  # noqa: PLC0415

    _init_tz_cache()
    built = _build_query(asset_kind, q)
    screen_kwargs: dict[str, Any] = {"size": q.size, "sortAsc": q.sort_ascending}
    if q.sort_field is not None:
        screen_kwargs["sortField"] = q.sort_field
    response = cast("dict[str, Any]", yf.screen(built, **screen_kwargs))
    return response


# Fields promoted to `ScreenerHit`'s own named attributes; everything else Yahoo returns for a
# quote row is passed through in `matched_fields` as-is (never invented, never dropped).
_HIT_NAME_FIELDS = ("longName", "shortName")


def _quote_to_hit(quote: dict[str, Any], rank: int) -> ScreenerHit:
    name = None
    for key in _HIT_NAME_FIELDS:
        value = _as_str(_get(quote, key))
        if value:
            name = value
            break
    matched_fields: dict[str, float | str | bool | None] = {}
    for key, value in quote.items():
        if isinstance(value, bool | str) or value is None:
            matched_fields[key] = value
        else:
            matched_fields[key] = _as_float(value)
    return ScreenerHit(
        symbol=str(_get(quote, "symbol", "")),
        name=name,
        exchange=_as_str(_get(quote, "fullExchangeName")) or _as_str(_get(quote, "exchange")),
        region=_as_str(_get(quote, "market")),
        matched_fields=matched_fields,
        rank=rank,
        score=None,
    )


async def run_screener(query: ScreenerQuery) -> ScreenerResult:
    """Run an equity/ETF/fund screener query against Yahoo's screener endpoint and return
    normalized `ScreenerHit` rows.

    Not part of `SourceAdapter`/`fetch()` — see the module docstring above for why. Blocking
    `yf.screen()` runs isolated in a threadpool, same as every other call in this package. An
    empty match or a rejected query (bad field/value for this asset kind) degrades to a real
    empty `ScreenerResult` with the actual reason — never fabricated matches, and never a
    raised exception out of this function.
    """
    try:
        raw = await anyio.to_thread.run_sync(_run_screener_blocking, query.asset_kind, query)
    except Exception as exc:  # never raise out of a screener call — degrade honestly
        logger.warning(
            "yfinance_screener_failed", asset_kind=query.asset_kind.value, error=str(exc)
        )
        return ScreenerResult(
            asset_kind=query.asset_kind, total_matches=0, hits=(), detail=str(exc)
        )
    quotes = cast("list[dict[str, Any]]", raw.get("quotes") or [])
    total = _as_int(raw.get("total")) or len(quotes)
    if not quotes:
        return ScreenerResult(
            asset_kind=query.asset_kind,
            total_matches=0,
            hits=(),
            detail="no symbols matched this screener query",
        )
    hits = tuple(_quote_to_hit(q, rank) for rank, q in enumerate(quotes, start=1))
    return ScreenerResult(asset_kind=query.asset_kind, total_matches=total, hits=hits, detail="ok")


__all__ = [
    "_build_query",
    "_condition_operand",
    "_quote_to_hit",
    "_run_screener_blocking",
    "run_screener",
]
