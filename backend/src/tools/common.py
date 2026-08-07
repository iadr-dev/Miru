"""Shared per-tool input primitives.

Every capability-scoped tool in this registry takes an explicit `(symbol, market)` — never a
market-wide shape (the full-market-scan guard) — so that one invariant lives
in a single input base class rather than being re-derived per tool module.
"""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market
from schemas.market import Symbol


class SymbolInput(BaseModel):
    """The one-symbol input shape every capability-scoped tool subclasses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: Market

    def to_symbol(self) -> Symbol:
        return Symbol(id=self.id, market=self.market)

    def date_params(self) -> dict[str, object]:
        """No date range on the bare `SymbolInput` shape — overridden by `DateRangeInput`.
        Defined here (returning empty) so every capability-scoped input shares one
        `to_symbol()`+`date_params()` interface regardless of which of these three input
        classes a given tool declares (see `tools/_rows.py`'s generic row-list tool)."""
        return {}


class DateRangeInput(SymbolInput):
    """`SymbolInput` plus an optional, still per-symbol, bounded date range."""

    start_date: date_ | None = None
    end_date: date_ | None = None

    def date_params(self) -> dict[str, object]:
        """Render the optional range as `DataAccessRouter.fetch(**params)` kwargs."""
        params: dict[str, object] = {}
        if self.start_date is not None:
            params["start_date"] = self.start_date.isoformat()
        if self.end_date is not None:
            params["end_date"] = self.end_date.isoformat()
        return params


class MarketOnlyInput(BaseModel):
    """The input shape for a **market-level** dataset — a whole-exchange aggregate or
    reference roster that carries no per-symbol scope at all (e.g. a whole-market margin-
    short total, a trading-calendar reference table). Still bounded to exactly one market,
    never an "all markets" shape, matching the full-market-scan guard's spirit even though
    there is no `id` field to require.

    Every dataset this input backs has a row-mapper that ignores whatever `Symbol` it is
    handed (documented per-dataset in `sources/finmind/*.py`) — the placeholder id
    `to_symbol()` constructs is never read as real data, only used to satisfy
    `DataAccessRouter.fetch`'s signature.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    start_date: date_ | None = None
    end_date: date_ | None = None

    def to_symbol(self) -> Symbol:
        return Symbol(id="", market=self.market)

    def date_params(self) -> dict[str, object]:
        params: dict[str, object] = {}
        if self.start_date is not None:
            params["start_date"] = self.start_date.isoformat()
        if self.end_date is not None:
            params["end_date"] = self.end_date.isoformat()
        return params


__all__ = ["DateRangeInput", "MarketOnlyInput", "SymbolInput"]
