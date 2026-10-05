from __future__ import annotations

from calendar.holidays import is_market_holiday, is_market_open_day
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from scheduler.models import ScheduleTrigger, TriggerType
from scheduler.triggers import next_run_after
from schemas.enums import Market

_TAIPEI = ZoneInfo("Asia/Taipei")


def _pre_open(market: Market) -> ScheduleTrigger:
    return ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="pre_open", market=market)


def test_tw_national_day_is_a_closure_but_teachers_day_is_not() -> None:
    assert is_market_holiday(date(2026, 10, 9), Market.TW)  # 國慶日補假
    assert not is_market_open_day(date(2026, 10, 9), Market.TW)
    assert is_market_open_day(date(2026, 9, 28), Market.TW)  # 孔子誕辰 — exchange open


def test_us_thanksgiving_is_a_closure() -> None:
    assert is_market_holiday(date(2026, 11, 26), Market.US)
    assert is_market_open_day(date(2026, 11, 27), Market.US)


def test_market_event_skips_a_holiday_friday_and_the_weekend() -> None:
    # Thu 2026-10-08 after the pre-open time -> Fri 10/9 (holiday), Sat, Sun are skipped.
    after = datetime(2026, 10, 8, 9, 0, tzinfo=_TAIPEI).astimezone(UTC)
    nxt = next_run_after(_pre_open(Market.TW), after).astimezone(_TAIPEI)
    assert nxt.date() == date(2026, 10, 12)
    assert (nxt.hour, nxt.minute) == (8, 30)


def test_us_market_event_skips_thanksgiving() -> None:
    ny = ZoneInfo("America/New_York")
    after = datetime(2026, 11, 25, 9, 0, tzinfo=ny).astimezone(UTC)
    nxt = next_run_after(_pre_open(Market.US), after).astimezone(ny)
    assert nxt.date() == date(2026, 11, 27)
