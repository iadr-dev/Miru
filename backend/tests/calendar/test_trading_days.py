"""Trading-day calendar primitive + graceful degradation when NCDR closure data is
unavailable."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from datetime import date, timedelta


def test_weekday_fallback_when_no_known_dates_or_closures() -> None:
    """No `known_trading_dates`, no `closure_dates` — degrades to plain Mon-Fri math, never
    raises (the "no closure adapter yet" gap)."""
    calendar = TradingDayCalendar()

    monday = date(2024, 1, 1)  # a Monday
    saturday = date(2024, 1, 6)

    assert calendar.is_trading_day(monday)
    assert not calendar.is_trading_day(saturday)


def test_closure_overlay_excludes_a_weekday() -> None:
    typhoon_day = date(2024, 7, 25)  # a Thursday
    calendar = TradingDayCalendar(closure_dates=frozenset({typhoon_day}))

    assert not calendar.is_trading_day(typhoon_day)
    assert calendar.is_trading_day(typhoon_day - timedelta(days=1))


def test_next_trading_day_skips_weekend() -> None:
    calendar = TradingDayCalendar()
    friday = date(2024, 1, 5)

    assert calendar.next_trading_day(friday) == date(2024, 1, 8)  # skips Sat/Sun to Monday


def test_business_days_between_is_bounded_and_inclusive() -> None:
    calendar = TradingDayCalendar()
    days = calendar.business_days_between(date(2024, 1, 1), date(2024, 1, 7))

    assert days == (
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    )


def test_shift_business_days_backward_skips_weekend() -> None:
    calendar = TradingDayCalendar()
    # From Monday 2024-01-08, 6 business days back should land on 2023-12-29 (Fri) - 5 more.
    result = calendar.shift_business_days(date(2024, 1, 8), -6)

    assert result.weekday() < 5  # a business day
    assert result < date(2024, 1, 8)


def test_known_trading_dates_allow_list_overrides_weekday_math() -> None:
    """When an explicit allow-list is supplied (future ingest cache), it is authoritative —
    even a weekday not in the list is not a trading day."""
    known = {date(2024, 1, 2), date(2024, 1, 3)}
    calendar = calendar_from_known_dates(known)

    assert calendar.is_trading_day(date(2024, 1, 2))
    assert not calendar.is_trading_day(date(2024, 1, 4))  # a weekday, but absent from the list
