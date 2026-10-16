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

from calendar.holidays import is_market_holiday
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date as date_
from datetime import timedelta

from schemas.enums import Market

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
    # The market this calendar serves. When set, a day OUTSIDE the known span is also checked
    # against the scheduled-holiday table (`calendar/holidays.py`), so a weekday exchange closure
    # after the last stored partition (the day after a holiday has not landed yet) is not taken for
    # a trading day. `None` keeps the plain weekday fallback.
    market: Market | None = None

    def is_trading_day(self, day: date_) -> bool:
        """True if `day` is a trading day under this calendar.

        Degrades gracefully: absent an explicit `known_trading_dates` allow-list, this is
        exactly "is it a weekday and not an overlaid closure day" — never raises, never
        requires network access. Once an allow-list IS supplied, it is authoritative for every
        date, including one outside the list's own min/max span — a caller asking about a date
        the list has no opinion on gets `False`, not a weekday guess (the list is presumed
        complete for whatever range the caller cares about). The internal walking methods below
        (`next_trading_day`/`business_days_between`/`shift_business_days`) use a separate,
        non-public extrapolating variant instead of this method directly — see
        `is_trading_day_extrapolating`'s own docstring for why.
        """
        if day in self.closure_dates:
            return False
        if self.known_trading_dates is not None:
            return day in self.known_trading_dates
        return day.weekday() not in (_SATURDAY, _SUNDAY)

    def known_trading_date_bounds(self) -> tuple[date_, date_] | None:
        """`(earliest, latest)` of `known_trading_dates`, or `None` if this calendar has no
        allow-list (pure weekday math, unbounded)."""
        if not self.known_trading_dates:
            return None
        return min(self.known_trading_dates), max(self.known_trading_dates)

    def is_trading_day_extrapolating(self, day: date_) -> bool:
        """`is_trading_day`, but a day outside the allow-list's own known span falls back to
        weekday math instead of being treated as authoritatively "not a trading day".

        Used by the day-by-day walking methods below (`next_trading_day`/
        `business_days_between`/`shift_business_days`) AND by every "should today's job run"
        gate (`ingest/jobs.py::_run_if_trading_day`, `ingest/us_jobs.py::job_us_price_eod`,
        `analytics/scoring_batch/`/`analytics/screener/batch.py`'s own gates, the daemon's
        daily-cadence dispatch check, `calendar/market_hours.py`) — **never** by the plain
        `is_trading_day` (which stays strictly allow-list-authoritative for historical
        gap-detection; see its own docstring).

        The calendar's `known_trading_dates` is built once per process from whatever partitions
        already exist on disk (`ingest/daemon.py::_build_calendar`) — so "today" is *always*
        beyond the known max until today's own job has actually written its partition. A gate
        that called the strict `is_trading_day` here would see `today not in known_trading_dates`
        forever and skip its own run every single day, permanently landing one real trading day
        behind with no way to catch up (confirmed empirically: `us_price_eod`/`TaiwanStockPrice`
        both stalled one day behind their real upstream on every process, every day). Falling
        back to weekday math for a day beyond the known range is the same graceful-degradation
        default `is_trading_day` itself already uses when it has no allow-list at all (see the
        class docstring) — a genuine holiday not yet in the known set is occasionally
        mis-classified as a trading day this way, but the resulting fetch just returns an empty
        `SourceResult`, which downstream partition/row-count logic already handles as a normal
        "no rows published today" case, not an error.

        Also prevents a walk that extrapolates past the list's known range -- e.g.
        `shift_business_days` asked to go back further than a partially-ingested store's known
        trading days -- from finding `is_trading_day` returning `False` for every remaining
        candidate forever, since none of them are literally in the frozenset; the walk would
        never terminate and eventually raise `OverflowError` on `date.min`. Confirmed
        empirically: a 3-year dev-backfill window reaching back past ~9 months of actually-
        ingested history."""
        if day in self.closure_dates:
            return False
        bounds = self.known_trading_date_bounds()
        if bounds is not None:
            low, high = bounds
            if low <= day <= high:
                return day in self.known_trading_dates  # type: ignore[operator]
        if day.weekday() in (_SATURDAY, _SUNDAY):
            return False
        return self.market is None or not is_market_holiday(day, self.market)

    def next_trading_day(self, day: date_, *, inclusive: bool = False) -> date_:
        """The next trading day on/after `day` (or strictly after, if `inclusive=False`)."""
        candidate = day if inclusive else day + timedelta(days=1)
        # Bounded loop: a market is never closed for a full calendar week under any real
        # overlay, but bound defensively rather than risk an infinite loop on bad input.
        for _ in range(14):
            if self.is_trading_day_extrapolating(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(f"no trading day found within 14 days of {day.isoformat()}")

    def previous_trading_day(self, day: date_, *, inclusive: bool = False) -> date_:
        """The most recent trading day on/before `day` (or strictly before, if
        `inclusive=False`) — the backward counterpart to `next_trading_day`, for "most recent
        available data" callers (e.g. `ingest/preflight.py::last_available_trading_day`) that
        must never walk into the future when `day` itself isn't a trading day."""
        candidate = day if inclusive else day - timedelta(days=1)
        for _ in range(14):
            if self.is_trading_day_extrapolating(candidate):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError(f"no trading day found within 14 days of {day.isoformat()}")

    def business_days_between(self, start: date_, end: date_) -> tuple[date_, ...]:
        """All trading days in `[start, end]` inclusive — bounded, never a full-market scan
        (this is a date-range primitive, not a per-symbol data fetch)."""
        if end < start:
            raise ValueError("end must not precede start")
        days: list[date_] = []
        current = start
        while current <= end:
            if self.is_trading_day_extrapolating(current):
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
            if self.is_trading_day_extrapolating(current):
                remaining -= 1
        return current


def calendar_from_known_dates(
    trading_dates: Iterable[date_],
    *,
    closure_dates: Iterable[date_] = (),
    market: Market | None = None,
) -> TradingDayCalendar:
    """Build a calendar from an explicit trading-date list (e.g. a future ingest cache) plus
    an optional NCDR closure overlay."""
    return TradingDayCalendar(
        known_trading_dates=frozenset(trading_dates),
        closure_dates=frozenset(closure_dates),
        market=market,
    )


__all__ = ["TradingDayCalendar", "calendar_from_known_dates"]
