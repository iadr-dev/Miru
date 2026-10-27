"""Which date partition a period-keyed filing is stored under.

Monthly revenue is filed (FinMind's `date`) under the 1st of the month after the revenue month, a
financial statement under the end of its quarter. Those nominal dates are calendar dates, not
trading days, and the ingest calendar gate skips a non-trading day, so a 1st that falls on a
weekend or a holiday (2026-02-01, 2026-03-01, ...) never ran and its period was never ingested.

The rule: **a period is stored under the first trading day on or after its nominal date.** When
the nominal date is a trading day (two months in three) nothing changes: same partition as
before. Otherwise the partition moves to the next trading day, while the ROWS keep FinMind's own
fields (`trade_date` / `period_end` stay the nominal date) and the sources are still asked for the
nominal date. Every reader walks trading-day partitions (or lists the stored partition dates), so
one trading-day key is found by all of them unchanged.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from datetime import timedelta
from enum import StrEnum
from typing import Final


class PeriodAnchor(StrEnum):
    """The nominal date a dataset's period is filed under."""

    MONTH_START = "month_start"  # the 1st of the month after the revenue month
    QUARTER_END = "quarter_end"  # 03-31, 06-30, 09-30, 12-31


_QUARTER_ENDS: Final = ((3, 31), (6, 30), (9, 30), (12, 31))
# The next trading day is searched at most this far from a nominal date (`next_trading_day` bounds
# its own walk at 14 days); a partition date further than that from a quarter end is not one.
_MAX_LAG_DAYS: Final = 14


def _quarter_ends_before(run_date: date_) -> list[date_]:
    """The quarter ends on or before `run_date` within the lag window, newest first."""
    found = [
        date_(year, month, day)
        for year in (run_date.year - 1, run_date.year)
        for month, day in _QUARTER_ENDS
    ]
    return sorted(
        (d for d in found if d <= run_date and run_date - d <= timedelta(days=_MAX_LAG_DAYS)),
        reverse=True,
    )


def _nominal_candidates(anchor: PeriodAnchor, run_date: date_) -> list[date_]:
    if anchor is PeriodAnchor.MONTH_START:
        return [run_date.replace(day=1)]
    return _quarter_ends_before(run_date)


def partition_date_for(nominal: date_, calendar: TradingDayCalendar) -> date_:
    """The partition a period filed under `nominal` is stored under: `nominal` itself on a
    trading day, else the next trading day."""
    return calendar.next_trading_day(nominal, inclusive=True)


def filing_date_for(
    anchor: PeriodAnchor, run_date: date_, calendar: TradingDayCalendar
) -> date_ | None:
    """The nominal filing date whose period is stored under partition `run_date`, or `None` when
    `run_date` is no period's partition (it holds no rows by definition: every other trading day).
    The inverse of `partition_date_for`."""
    for nominal in _nominal_candidates(anchor, run_date):
        if partition_date_for(nominal, calendar) == run_date:
            return nominal
    return None


__all__ = ["PeriodAnchor", "filing_date_for", "partition_date_for"]
