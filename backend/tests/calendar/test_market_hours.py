"""`MarketHours` — the intraday-session overlay used by the live-quote poller's market-open gate."""

from __future__ import annotations

from calendar.market_hours import MarketHours
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")


def _taipei(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TAIPEI)


def test_open_during_tw_session_on_a_trading_day() -> None:
    hours = MarketHours()
    # 2024-01-02 is a Tuesday (a trading day under the plain weekday fallback).
    assert hours.is_open(_taipei(2024, 1, 2, 10, 30)) is True


def test_closed_before_open_and_after_close() -> None:
    hours = MarketHours()
    assert hours.is_open(_taipei(2024, 1, 2, 8, 59)) is False
    assert hours.is_open(_taipei(2024, 1, 2, 13, 31)) is False


def test_closed_on_a_weekend_even_within_session_hours() -> None:
    hours = MarketHours()
    # 2024-01-06 is a Saturday.
    assert hours.is_open(_taipei(2024, 1, 6, 10, 30)) is False


def test_accepts_utc_and_converts_to_local() -> None:
    hours = MarketHours()
    # 02:00 UTC on a Tuesday == 10:00 Asia/Taipei — inside the session.
    assert hours.is_open(datetime(2024, 1, 2, 2, 0, tzinfo=UTC)) is True
    # 06:00 UTC == 14:00 Asia/Taipei — after close.
    assert hours.is_open(datetime(2024, 1, 2, 6, 0, tzinfo=UTC)) is False
