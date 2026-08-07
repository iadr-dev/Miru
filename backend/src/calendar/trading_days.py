"""The trading-day calendar primitive + NCDR typhoon-closure overlay.

Every job is expected to be gated on the trading-day list (`TaiwanStockTradingDate`) plus the
NCDR typhoon-closure overlay, so a job never runs (or skips) based on a naive weekday check
alone.

**Known gap (documented, not silently ignored):** `sources/ncdr_closure.py` (the
`NCDR_DGPA_CLOSURE_CAP` adapter) was not built in Phase 2, and there is no `Capability` enum
value for trading-day/closure data yet (`schemas/enums.py`'s `Capability` list has no
`trading_calendar`/`closure` entry — only `calendar_events`, which is for *forward* events,
not the historical/weekday trading-day list). Rather than block this phase on those additions,
`TradingDayCalendar` is built so it **degrades gracefully**: with no known-trading-dates list
and no closure overlay supplied, it falls back to a plain Mon-Fri business-day calendar and
treats "no closure evidence available" as "assume normal trading calendar" — never a hard
failure, a graceful-degradation principle applied here even before the NCDR adapter exists.

When `sources/ncdr_closure.py` lands (with its own capability + router config), the intended
wiring is: fetch its `SourceResult` via the router, pass the resulting closure dates into
`TradingDayCalendar(closure_dates=...)` — no change to this primitive's public shape.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta

_SATURDAY = 5  # date.weekday(): Mon=0 ... Sun=6
_SUNDAY = 6


@dataclass(frozen=True)
class TradingDayCalendar:
    """A market's trading-day calendar: weekday business days, minus any known holidays,
    minus any NCDR-style closure-overlay dates (typhoon/work-suspension days).

    - `known_trading_dates`: an explicit allow-list (e.g. from a future FinMind
      `TaiwanStockTradingDate` ingest cache). When `None` (today's default — ingest doesn't
      populate this yet), falls back to pure Mon-Fri weekday math.
    - `closure_dates`: the NCDR typhoon-closure overlay. Empty by default — "no closure
      adapter yet" is represented as an empty set, i.e. *no* known closures, not a failure.
    """

    known_trading_dates: frozenset[date_] | None = None
    closure_dates: frozenset[date_] = field(default_factory=frozenset[date_])

    def is_trading_day(self, day: date_) -> bool:
        """True if `day` is a trading day under this calendar.

        Degrades gracefully: absent an explicit `known_trading_dates` allow-list, this is
        exactly "is it a weekday and not an overlaid closure day" — never raises, never
        requires network access.
        """
        if day in self.closure_dates:
            return False
        if self.known_trading_dates is not None:
            return day in self.known_trading_dates
        return day.weekday() not in (_SATURDAY, _SUNDAY)

    def next_trading_day(self, day: date_, *, inclusive: bool = False) -> date_:
        """The next trading day on/after `day` (or strictly after, if `inclusive=False`)."""
        candidate = day if inclusive else day + timedelta(days=1)
        # Bounded loop: a market is never closed for a full calendar week under any real
        # overlay, but bound defensively rather than risk an infinite loop on bad input.
        for _ in range(14):
            if self.is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(f"no trading day found within 14 days of {day.isoformat()}")

    def business_days_between(self, start: date_, end: date_) -> tuple[date_, ...]:
        """All trading days in `[start, end]` inclusive — bounded, never a full-market scan
        (this is a date-range primitive, not a per-symbol data fetch)."""
        if end < start:
            raise ValueError("end must not precede start")
        days: list[date_] = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return tuple(days)

    def shift_business_days(self, day: date_, offset: int) -> date_:
        """Move `day` by `offset` trading days (negative = backward). Used by rule-derived
        markers such as 停止過戶起始日 = AGM - 60 days / 融券強制回補最後日 = 停止過戶開始日 - 6
        營業日."""
        step = 1 if offset >= 0 else -1
        remaining = abs(offset)
        current = day
        while remaining > 0:
            current += timedelta(days=step)
            if self.is_trading_day(current):
                remaining -= 1
        return current


def calendar_from_known_dates(
    trading_dates: Iterable[date_],
    *,
    closure_dates: Iterable[date_] = (),
) -> TradingDayCalendar:
    """Build a calendar from an explicit trading-date list (e.g. a future ingest cache) plus
    an optional NCDR closure overlay."""
    return TradingDayCalendar(
        known_trading_dates=frozenset(trading_dates),
        closure_dates=frozenset(closure_dates),
    )


__all__ = ["TradingDayCalendar", "calendar_from_known_dates"]
