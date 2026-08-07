"""Screener query + result contracts.

A screener request is shaped around a *query* (a set of field conditions over a whole
universe), not a `(capability, symbol)` pair — there is no symbol to scope a fetch to, so these
types deliberately do not extend the per-capability normalized-row pattern in
`schemas/capabilities.py`. They are the typed input/output contract for
`sources/yfinance.run_screener` (see that function's docstring for why it lives outside
`SourceAdapter.fetch()`).

`ScreenerOperator` is this codebase's own operator vocabulary — the same
`{gt, gte, lt, lte, eq, between, in}` set the condition-builder tooling documents — kept
independent of any one upstream SDK's operator spelling. Each screener backend translates this
vocabulary to its own at the call site (see `sources/yfinance._EQUITY_OP_MAP`), so a future
second screener backend (if one is ever added) reuses these same query/result types.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import Market


class ScreenerAssetKind(StrEnum):
    """Which screener universe a query targets. Each kind has a disjoint valid-field set
    upstream (an ETF's expense-ratio field means nothing to an equity query) — never mixed."""

    EQUITY = "equity"
    ETF = "etf"
    FUND = "fund"


class ScreenerOperator(StrEnum):
    """`gt/gte/lt/lte/eq` are single-value comparisons; `between` takes `value`+`value2`
    (inclusive range); `in` takes `values` (membership test against an enum-like field, e.g.
    sector/region)."""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    BETWEEN = "between"
    IN = "in"


class ScreenerCondition(BaseModel):
    """One condition row. `field` is a raw upstream screener field name (e.g.
    `intradaymarketcap`, `sector`) — this layer does not remap field names to our own factor
    vocabulary; that mapping is the future `get_factor_catalog` tool's job, not this adapter's."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    op: ScreenerOperator
    value: float | str | None = None
    value2: float | str | None = None  # required, and only meaningful, for `BETWEEN`
    values: tuple[str, ...] | None = None  # required, and only meaningful, for `IN`

    @model_validator(mode="after")
    def _check_operand_shape(self) -> ScreenerCondition:
        if self.op is ScreenerOperator.BETWEEN and (self.value is None or self.value2 is None):
            raise ValueError("BETWEEN requires both value and value2")
        if self.op is ScreenerOperator.IN and not self.values:
            raise ValueError("IN requires a non-empty values tuple")
        if self.op not in (ScreenerOperator.BETWEEN, ScreenerOperator.IN) and self.value is None:
            raise ValueError(f"{self.op.value} requires value")
        return self


class ScreenerQuery(BaseModel):
    """A custom screener query — the caller-facing input to `sources.yfinance.run_screener`.
    Named/predefined Yahoo screens (e.g. `"day_gainers"`) are a distinct string-keyed API this
    type does not model — see that function's docstring for the finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_kind: ScreenerAssetKind
    conditions: tuple[ScreenerCondition, ...] = Field(min_length=1)
    match: Literal["AND", "OR"] = "AND"
    size: int = Field(default=25, ge=1, le=250)  # Yahoo hard-caps a custom-query screen at 250
    sort_field: str | None = None
    sort_ascending: bool = False


class ScreenerHit(BaseModel):
    """One matched row. `matched_fields` carries the raw Yahoo quote fields returned for this
    hit, coerced to plain JSON-safe scalars — this is a thin passthrough of whatever Yahoo's
    screener response included for that symbol, not reshaped into our own factor names (that
    remapping belongs to the tool/analytics layer that consumes this, not this adapter)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    exchange: str | None = None
    region: str | None = None  # yfinance's own region code, e.g. "us" — not our `Market` enum;
    # a single query can legitimately span many countries (screener conditions decide scope)
    matched_fields: dict[str, float | str | bool | None]
    rank: int | None = None  # 1-based position in the returned (sorted) page, not a percentile
    score: float | None = None  # unset: Yahoo's screener endpoint does not return a match score


class ScreenerResult(BaseModel):
    """The normalized screener response. `detail` is never empty — an empty/failed query
    still carries a real, honest reason (never fabricated matches)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_kind: ScreenerAssetKind
    total_matches: int = Field(ge=0)
    hits: tuple[ScreenerHit, ...]
    detail: str = Field(min_length=1)


# -- tag screener -----------------------------------------------------------------------------
#
# A second, sibling contract living in this same module: the tap-tile TW tag screener. Unlike the
# numeric `ScreenerQuery` above (a field+operator+value query against Yahoo's screener, non-TW),
# a tag query selects named end-of-day boolean conditions (e.g. "外資連3買", "5日均線回升"),
# combines them AND/OR, and returns matching symbols. Every tag resolves at end of day — there is
# no intraday tag — and matches are read back from a nightly pre-computed flag dataset, not
# evaluated live per query. The two contracts share the `match` semantics and the never-empty
# `detail` convention but nothing else, so they are modelled independently rather than force-fitted
# onto one base.


class TagCondition(BaseModel):
    """One requested tag, optionally negated. `tag` is a key from the screener tag catalog
    (`analytics/screener/catalog.py`); an unknown key resolves to zero matches, never an error —
    the read seam reports which requested tags were unrecognized in its `detail`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: str = Field(min_length=1)
    negate: bool = False


class TagScreenerQuery(BaseModel):
    """A tap-tile tag screener query. `as_of` selects which end-of-day flag partition to read;
    `None` means the latest available one. `match` combines the requested tags: `AND` returns
    symbols satisfying every requested condition, `OR` symbols satisfying any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    tags: tuple[TagCondition, ...] = Field(min_length=1)
    match: Literal["AND", "OR"] = "AND"
    as_of: date | None = None
    size: int = Field(default=50, ge=1, le=250)


class TagScreenerHit(BaseModel):
    """One matched symbol plus the requested tags it actually satisfied — the "show the work"
    transparency rule: an `AND` hit lists every requested tag (all satisfied), an `OR` hit lists
    the subset it matched. A negated condition that held is reported here in its `not <tag>` form,
    so the row is self-explaining without re-deriving the query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    matched_tags: tuple[str, ...] = Field(min_length=1)


class TagScreenerResult(BaseModel):
    """The normalized tag-screener response. `detail` is never empty — an empty result still
    carries a real, honest reason (no ingested flag partition yet, no symbol matched, an unknown
    tag key), never fabricated matches. `total_matches` is the full match count before the `size`
    page cap; `hits` is the capped page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    as_of: date | None = None
    total_matches: int = Field(ge=0)
    hits: tuple[TagScreenerHit, ...]
    detail: str = Field(min_length=1)


__all__ = [
    "ScreenerAssetKind",
    "ScreenerCondition",
    "ScreenerHit",
    "ScreenerOperator",
    "ScreenerQuery",
    "ScreenerResult",
    "TagCondition",
    "TagScreenerHit",
    "TagScreenerQuery",
    "TagScreenerResult",
]
