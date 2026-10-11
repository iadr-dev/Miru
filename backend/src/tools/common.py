"""Shared per-tool input primitives.

Every capability-scoped tool in this registry takes an explicit `(symbol, market)` — never a
market-wide shape (the full-market-scan guard) — so that one invariant lives
in a single input base class rather than being re-derived per tool module.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import Market
from schemas.market import Symbol

# The widest range any real caller asks for is the dividend history's "all" (30 years). Anything
# past this is a typo or an attempt to make the upstream fetch decades of rows (and mint a new
# cache key per distinct range), so it is rejected at validation time rather than forwarded.
MAX_DATE_RANGE_DAYS = 31 * 366


def check_date_range(start: date_ | None, end: date_ | None) -> None:
    """Reject an inverted or absurdly wide `[start, end]`; either end may be open (`None`)."""
    if start is not None and end is not None:
        if start > end:
            msg = f"start_date {start.isoformat()} is after end_date {end.isoformat()}"
            raise ValueError(msg)
        if (end - start).days > MAX_DATE_RANGE_DAYS:
            msg = f"date range is wider than {MAX_DATE_RANGE_DAYS} days"
            raise ValueError(msg)


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

    @model_validator(mode="after")
    def _valid_range(self) -> DateRangeInput:
        check_date_range(self.start_date, self.end_date)
        return self

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

    @model_validator(mode="after")
    def _valid_range(self) -> MarketOnlyInput:
        check_date_range(self.start_date, self.end_date)
        return self

    def to_symbol(self) -> Symbol:
        return Symbol(id="", market=self.market)

    def date_params(self) -> dict[str, object]:
        params: dict[str, object] = {}
        if self.start_date is not None:
            params["start_date"] = self.start_date.isoformat()
        if self.end_date is not None:
            params["end_date"] = self.end_date.isoformat()
        return params


def candidate_trading_days(start: date_, end: date_, *, limit: int) -> tuple[date_, ...]:
    """Trading days in `[start, end]`, most-recent-first, capped at `limit` — shared by every
    tool whose upstream report is single-day-per-call (`chip_broker`/分點,
    `government_bank_buysell`/八大行庫) and therefore fans out to one `router.fetch()` per
    trading day rather than a single ranged call. A plain `TradingDayCalendar()` (weekday math,
    no holiday allow-list) is deliberately used rather than a real ingested calendar (needs a
    `ParquetStore`, a layer this small bounded probe doesn't warrant pulling in): a real TW
    holiday in the window just comes back as an empty per-day fetch, which downstream health/row
    handling already treats as a normal "no rows this day" case, not an error."""
    calendar = TradingDayCalendar()
    all_days = calendar.business_days_between(start, end)
    return tuple(reversed(all_days[-limit:]))


__all__ = [
    "MAX_DATE_RANGE_DAYS",
    "DateRangeInput",
    "MarketOnlyInput",
    "SymbolInput",
    "candidate_trading_days",
    "check_date_range",
]
