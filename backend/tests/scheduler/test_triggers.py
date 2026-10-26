"""`next_run_after` — the trigger resolver that gives every job a concrete fire time."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from scheduler.models import ScheduleTrigger, TriggerType
from scheduler.triggers import UnresolvableTriggerError, next_run_after
from schemas.enums import Market

TPE = ZoneInfo("Asia/Taipei")


def _event(spec: str, market: Market = Market.TW) -> ScheduleTrigger:
    return ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec=spec, market=market)


def _cron(spec: str) -> ScheduleTrigger:
    return ScheduleTrigger(type=TriggerType.CRON, spec=spec, market=Market.TW)


def _local(result: datetime) -> datetime:
    return result.astimezone(TPE)


def test_after_close_same_day_when_before_1430() -> None:
    # Wed 2026-09-30 10:00 Taipei -> same day 14:30 Taipei.
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    result = _local(next_run_after(_event("after_close"), after))
    assert (result.date(), result.hour, result.minute) == (date(2026, 9, 30), 14, 30)


def test_after_close_rolls_to_next_trading_day_when_past() -> None:
    after = datetime(2026, 9, 30, 15, 0, tzinfo=TPE)  # Wednesday, past 14:30
    result = _local(next_run_after(_event("after_close"), after))
    assert result.date() == date(2026, 10, 1)  # Thursday


def test_market_event_skips_weekend() -> None:
    after = datetime(2026, 10, 2, 15, 0, tzinfo=TPE)  # Friday, past 14:30
    result = _local(next_run_after(_event("after_close"), after))
    assert result.date() == date(2026, 10, 5)  # Monday
    assert result.weekday() == 0


def test_market_event_skips_holidays_via_injected_calendar() -> None:
    after = datetime(2026, 9, 30, 15, 0, tzinfo=TPE)
    closed = {date(2026, 10, 1), date(2026, 10, 2)}
    result = _local(
        next_run_after(
            _event("after_close"),
            after,
            is_trading_day=lambda d, _m: d not in closed and d.weekday() < 5,
        )
    )
    assert result.date() == date(2026, 10, 5)


def test_strictly_after_never_returns_now() -> None:
    at_fire_time = datetime(2026, 9, 30, 14, 30, tzinfo=TPE)
    result = _local(next_run_after(_event("after_close"), at_fire_time))
    assert result.date() == date(2026, 10, 1)


def test_us_market_uses_new_york_time() -> None:
    after = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    result = next_run_after(_event("after_close", Market.US), after)
    ny = result.astimezone(ZoneInfo("America/New_York"))
    assert (ny.hour, ny.minute) == (16, 30)


def test_cron_weekday_list_and_time() -> None:
    # 09:15 on Mon and Fri; after Mon 09:15 the next is Fri.
    after = datetime(2026, 9, 28, 9, 15, tzinfo=TPE)  # Monday 09:15
    result = _local(next_run_after(_cron("15 9 * * 1,5"), after))
    assert (result.date(), result.hour, result.minute) == (date(2026, 10, 2), 9, 15)


def test_cron_range_and_sunday_as_7() -> None:
    after = datetime(2026, 10, 2, 12, 0, tzinfo=TPE)  # Friday
    assert _local(next_run_after(_cron("0 8 * * 7"), after)).weekday() == 6  # Sunday
    weekdays = _local(next_run_after(_cron("0 8 * * 1-5"), after))
    assert weekdays.weekday() == 0  # next Monday


def test_cron_multiple_hours_pick_earliest_future() -> None:
    after = datetime(2026, 9, 30, 9, 0, tzinfo=TPE)
    result = _local(next_run_after(_cron("0 8,12,18 * * *"), after))
    assert (result.hour, result.minute) == (12, 0)


def test_cron_day_of_month_or_weekday_semantics() -> None:
    # Standard cron: with both dom and dow restricted, EITHER may match.
    after = datetime(2026, 9, 30, 12, 0, tzinfo=TPE)
    result = _local(next_run_after(_cron("0 9 1 * 5"), after))
    assert result.date() == date(2026, 10, 1)  # the 1st (also a Thursday) beats Friday the 2nd


def test_result_is_utc_and_timezone_required() -> None:
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    assert next_run_after(_event("pre_open"), after).utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    event = _event("pre_open")
    naive = datetime(2026, 9, 30, 10, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        next_run_after(event, naive)


@pytest.mark.parametrize("spec", ["0 8 * *", "61 8 * * *", "0 25 * * *", "0 8 * * 9", "a b c d e"])
def test_malformed_cron_is_unresolvable(spec: str) -> None:
    trigger = _cron(spec)
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError):
        next_run_after(trigger, after)


def test_impossible_date_is_unresolvable() -> None:
    trigger = _cron("0 8 30 2 *")
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError):
        next_run_after(trigger, after)


def test_unknown_market_event_is_unresolvable() -> None:
    trigger = _event("midnight")
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError):
        next_run_after(trigger, after)


def test_no_trading_day_within_horizon_is_unresolvable() -> None:
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError, match="no trading day found"):
        next_run_after(_event("pre_open"), after, is_trading_day=lambda _d, _m: False)


def test_trading_check_receives_local_day_and_market_in_order() -> None:
    seen: list[tuple[date, Market]] = []

    def check(day: date, market: Market) -> bool:
        seen.append((day, market))
        return len(seen) >= 3

    after = datetime(2026, 9, 30, 15, 0, tzinfo=TPE)
    result = next_run_after(_event("after_close"), after, is_trading_day=check)
    assert seen == [
        (date(2026, 9, 30), Market.TW),
        (date(2026, 10, 1), Market.TW),
        (date(2026, 10, 2), Market.TW),
    ]
    assert _local(result) == datetime(2026, 10, 2, 14, 30, tzinfo=TPE)


def test_unknown_market_event_message_and_cause() -> None:
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError, match="unknown market event 'midnight'") as info:
        next_run_after(_event("midnight"), after)
    assert isinstance(info.value.__cause__, ValueError)


def test_cron_never_fires_message() -> None:
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)
    with pytest.raises(UnresolvableTriggerError, match="never fires within a year"):
        next_run_after(_cron("0 8 30 2 *"), after)


def test_cron_ignores_trading_day_check() -> None:
    after = datetime(2026, 9, 30, 10, 0, tzinfo=TPE)  # Wednesday
    result = next_run_after(_cron("0 8 * * 6"), after, is_trading_day=lambda _d, _m: False)
    assert _local(result) == datetime(2026, 10, 3, 8, 0, tzinfo=TPE)  # Saturday


def test_cron_candidate_must_be_strictly_after() -> None:
    after = datetime(2026, 9, 30, 8, 0, tzinfo=TPE)
    result = next_run_after(_cron("0 8 * * *"), after)
    assert _local(result) == datetime(2026, 10, 1, 8, 0, tzinfo=TPE)
