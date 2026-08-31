"""Classify a raw TW symbol code as a real, scoreable equity/ETF vs. a warrant/derivative —
shared by every reader of the whole-market `TaiwanStockPrice` partition that needs "how many
real stocks", not "how many rows".

Confirmed live against a single day's `TaiwanStockPrice` partition (originally measured in
`analytics/scoring_batch.py`, before this module existed): plain equities are 4-digit numeric
codes (~2.3K/day); ETFs are `00`-prefixed 5-6-digit codes (~350/day, e.g. `0050`, `006201`,
`00631L`); warrants are 6-digit numeric codes NOT starting with `00` (~17.1K/day — the dominant
share of the raw partition) plus a residual few hundred 5-char preferred-share/other exotic codes
not starting with `00`. A plain length cutoff alone is not enough: some real preferred-share
codes ARE 5 characters (e.g. `2881B`), same length as the 5-char ETF codes, but a warrant is
never `00`-prefixed — hence the two-part rule below.

A tool/batch that reads `TaiwanStockPrice` (or any dataset keyed by the same symbol universe)
without this filter silently mixes ~17K warrant rows into what should be a ~2.65K-symbol
equity/ETF market — this was confirmed to be the root cause of `get_advance_decline`'s breadth
counts not matching TWSE's real market (thousands of extra warrant rows inflating every bucket).
"""

from __future__ import annotations

from enum import StrEnum
from typing import cast

import pandas as pd

_MAX_PLAIN_EQUITY_LENGTH = 4
_MAX_ETF_LENGTH = 6
_ETF_PREFIX = "00"

# TWSE's own documented ETF suffix convention (confirmed against the exchange's real-time MIS
# ETF disclosure page, `sources/twse_etf_nav.py`): a leveraged ETF's code ends in `L` (e.g.
# `00631L` 元大台灣50正2, a 2x-leveraged product), an inverse ETF's code ends in `R` (e.g.
# `00632R` 元大台灣50反1). This is a real, load-bearing exchange convention, not a heuristic —
# every leveraged/inverse ETF listed on TWSE follows it.
_LEVERAGED_SUFFIX = "L"
_INVERSE_SUFFIX = "R"


class SymbolType(StrEnum):
    """Which scoring branch a symbol belongs to — the discriminator `scoring_batch.py` uses to
    pick `StockScoreBreakdown` vs `EtfScoreBreakdown` (see `analytics/models.py`). Plain equities
    and ETFs need entirely different factors (an ETF has no ROE/EV-EBITDA of its own; a leveraged/
    inverse ETF's daily-rebalancing decay makes the stock-style trend-template actively
    misleading, not just unavailable — see the design discussion this module implements)."""

    STOCK = "stock"
    ETF = "etf"
    ETF_LEVERAGED = "etf_leveraged"
    ETF_INVERSE = "etf_inverse"


def is_scoreable_symbol(symbol: str) -> bool:
    """Scalar predicate for a plain `dict`/loop-shaped reader (e.g. `tools/market_tools.py`'s
    `_closes_by_symbol`) — same rule as `scoreable_symbol_mask`, just per-value instead of
    vectorized."""
    is_plain_equity = len(symbol) <= _MAX_PLAIN_EQUITY_LENGTH
    is_etf = len(symbol) <= _MAX_ETF_LENGTH and symbol.startswith(_ETF_PREFIX)
    return is_plain_equity or is_etf


def classify_symbol_type(symbol: str) -> SymbolType:
    """Which scoring branch `symbol` belongs to, from the code shape alone. Does NOT distinguish
    active vs. passive ETFs — that needs ingested holdings data (`ActiveEtfHoldingRow`
    membership for the day), not the code shape, so `scoring_batch.py` layers that distinction
    on top of this function's `ETF` result itself, rather than this module reaching into the
    store. Callers must call `is_scoreable_symbol` first — this function assumes the symbol is
    already known to be a real equity/ETF, not a warrant."""
    if symbol.startswith(_ETF_PREFIX) and len(symbol) <= _MAX_ETF_LENGTH:
        if symbol.endswith(_LEVERAGED_SUFFIX):
            return SymbolType.ETF_LEVERAGED
        if symbol.endswith(_INVERSE_SUFFIX):
            return SymbolType.ETF_INVERSE
        return SymbolType.ETF
    return SymbolType.STOCK


def scoreable_symbol_mask(symbols: pd.Series) -> pd.Series:
    """Vectorized counterpart of `is_scoreable_symbol`, for a columnar `DataFrame`-shaped reader
    (e.g. `analytics/scoring_batch.py`'s whole-market price window) — a mask over the whole
    frame, not a per-row Python check, so the excluded ~90% of rows never reach per-symbol
    processing at all."""
    lengths = cast("pd.Series", symbols.str.len())
    is_plain_equity = cast("pd.Series", lengths <= _MAX_PLAIN_EQUITY_LENGTH)
    starts_with_etf_prefix = cast("pd.Series", symbols.str.startswith(_ETF_PREFIX))
    is_etf = cast("pd.Series", (lengths <= _MAX_ETF_LENGTH) & starts_with_etf_prefix)
    return cast("pd.Series", is_plain_equity | is_etf)


__all__ = ["SymbolType", "classify_symbol_type", "is_scoreable_symbol", "scoreable_symbol_mask"]
