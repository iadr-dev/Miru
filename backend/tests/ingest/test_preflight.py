"""`INGEST_ON_STARTUP` preflight tests."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, datetime
from datetime import date as date_

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.jobs import PRICE_EOD_JOB
from ingest.leader import AlwaysLeader
from ingest.preflight import (
    last_available_trading_day,
    last_completed_us_session,
    run_preflight_if_enabled,
)
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _ctx(tmp_path: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


async def test_disabled_flag_is_a_pure_noop(tmp_path: object) -> None:
    """Production default: `ingest_on_startup=False` -> no fetch, no leader/calendar check,
    not even a sweep."""
    ctx = _ctx(tmp_path)

    runs = await run_preflight_if_enabled(
        PRICE_EOD_JOB, ctx, ingest_on_startup=False, dev_backfill_years=1
    )

    assert runs == ()


def test_last_available_trading_day_before_publish_time_targets_yesterday() -> None:
    calendar = TradingDayCalendar()
    # 2024-01-03 10:00 Taipei local = 2024-01-03 02:00 UTC — before the 17:30 Taipei FinMind
    # publish time, so "today" isn't published yet; target should be the prior trading day
    # (2024-01-02). Passed as real UTC (every real caller's shape) to exercise the function's
    # own UTC->Taipei conversion, not a pre-converted value.
    now = datetime(2024, 1, 3, 2, 0, tzinfo=UTC)

    target = last_available_trading_day(calendar, now=now)

    assert target == date_(2024, 1, 2)


def test_last_available_trading_day_after_publish_time_targets_today() -> None:
    calendar = TradingDayCalendar()
    # 2024-01-03 18:00 Taipei local = 2024-01-03 10:00 UTC — after the 17:30 Taipei publish time.
    now = datetime(2024, 1, 3, 10, 0, tzinfo=UTC)

    target = last_available_trading_day(calendar, now=now)

    assert target == date_(2024, 1, 3)


def test_last_available_trading_day_converts_utc_to_taipei_before_comparing_publish_time() -> None:
    """Regression guard for the real timezone bug: a UTC instant that is BEFORE 17:30 in raw UTC
    clock time but AFTER 17:30 once converted to Taipei (UTC+8) must resolve to "today", not
    "yesterday". Every real caller passes `datetime.now(UTC)` straight through — an unconverted
    comparison here would misread this exact instant as pre-publish and wrongly target yesterday
    for roughly half of every day."""
    calendar = TradingDayCalendar()
    # 2024-01-03 10:00 UTC = 2024-01-03 18:00 Taipei — already past the Taipei 17:30 cutoff, even
    # though 10:00 < 17:30 in raw (unconverted) clock time.
    now = datetime(2024, 1, 3, 10, 0, tzinfo=UTC)

    target = last_available_trading_day(calendar, now=now)

    assert target == date_(2024, 1, 3)


def test_last_available_trading_day_on_weekend_walks_backward_not_forward() -> None:
    """Regression guard: calling this on a Saturday/Sunday before the publish-time cutoff must
    never return a FUTURE date. `target` (yesterday) lands on a Saturday here, which isn't a
    trading day — the old implementation fell through to `next_trading_day` (forward), which
    returned the following Monday: a date that hasn't traded yet. "Most recent AVAILABLE
    trading day" must walk backward to the prior Friday instead."""
    calendar = TradingDayCalendar()
    # 2024-01-07 10:00 Taipei local = 2024-01-07 02:00 UTC; before 17:30 Taipei so
    # target = 2024-01-06 (Saturday) — not a trading day.
    now = datetime(2024, 1, 7, 2, 0, tzinfo=UTC)

    target = last_available_trading_day(calendar, now=now)

    assert target == date_(2024, 1, 5)  # the prior Friday, never a date after `now`
    assert target < now.date()


async def test_enabled_preflight_backfills_bounded_dev_window(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    ctx = _ctx(tmp_path)
    now = datetime(2024, 1, 3, 18, 0, tzinfo=UTC)

    runs = await run_preflight_if_enabled(
        PRICE_EOD_JOB, ctx, ingest_on_startup=True, dev_backfill_years=1, now=now
    )

    assert len(runs) > 0
    assert ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", date_(2024, 1, 3))


def test_last_completed_us_session_excludes_a_session_that_is_still_open() -> None:
    """The real incident: 14:14 UTC on 2026-10-07 is 22:14 Taipei (past the 17:30 Taipei FinMind
    time, so `last_available_trading_day` says "today") but only 10:14 ET — the US 10-07 session
    is open, so the last *completed* session is 10-06."""
    calendar = TradingDayCalendar()
    now = datetime(2026, 10, 7, 14, 14, tzinfo=UTC)

    assert last_available_trading_day(calendar, now=now) == date_(2026, 10, 7)  # the wrong answer
    assert last_completed_us_session(calendar, now=now) == date_(2026, 10, 6)


def test_last_completed_us_session_includes_a_session_once_it_has_settled() -> None:
    calendar = TradingDayCalendar()
    # 21:30 UTC = 17:30 EDT, an hour and a half past the 16:00 close.
    assert last_completed_us_session(
        calendar, now=datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    ) == date_(2026, 10, 7)
    # 20:30 UTC = 16:30 EDT: closed, but not yet settled.
    assert last_completed_us_session(
        calendar, now=datetime(2026, 10, 7, 20, 30, tzinfo=UTC)
    ) == date_(2026, 10, 6)


def test_last_completed_us_session_follows_the_new_york_clock_across_dst() -> None:
    calendar = TradingDayCalendar()
    # Winter (EST, UTC-5): 22:01 UTC Mon = 17:01 EST Mon -> Monday settled.
    assert last_completed_us_session(
        calendar, now=datetime(2024, 1, 8, 22, 1, tzinfo=UTC)
    ) == date_(2024, 1, 8)
    # Summer (EDT, UTC-4): 20:01 UTC = 16:01 EDT, past the close but not settled -> prior day.
    assert last_completed_us_session(
        calendar, now=datetime(2024, 7, 8, 20, 1, tzinfo=UTC)
    ) == date_(2024, 7, 5)


def test_last_completed_us_session_walks_back_over_weekends() -> None:
    calendar = TradingDayCalendar()
    # Saturday morning ET and Sunday evening ET both resolve to Friday.
    assert last_completed_us_session(
        calendar, now=datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    ) == date_(2026, 10, 2)
    assert last_completed_us_session(
        calendar, now=datetime(2026, 10, 4, 23, 0, tzinfo=UTC)
    ) == date_(2026, 10, 2)


def test_last_completed_us_session_treats_a_naive_now_as_new_york_local() -> None:
    calendar = TradingDayCalendar()

    assert last_completed_us_session(calendar, now=datetime(2026, 10, 7, 16, 59)) == date_(
        2026, 10, 6
    )
    assert last_completed_us_session(calendar, now=datetime(2026, 10, 7, 17, 0)) == date_(
        2026, 10, 7
    )


def test_snapshot_day_is_the_taipei_date_on_a_trading_day_and_none_otherwise() -> None:
    """The no-date-parameter feeds are filed under the real Taipei date: not an earlier trading
    day (a Saturday dump replaced Friday's partition), not the UTC date (still yesterday until
    08:00 Taipei), and nothing at all on a weekend or holiday."""
    from calendar.trading_days import TradingDayCalendar  # noqa: PLC0415
    from datetime import UTC, date, datetime  # noqa: PLC0415

    from ingest.preflight import snapshot_day  # noqa: PLC0415
    from schemas.enums import Market  # noqa: PLC0415

    calendar = TradingDayCalendar(market=Market.TW)
    # 02:00 Taipei Tuesday is Monday 18:00 UTC: filed under Tuesday.
    assert snapshot_day(calendar, now=datetime(2026, 10, 13, 18, 0, tzinfo=UTC)) == date(
        2026, 10, 14
    )
    # Saturday 13:00 Taipei: a weekend, no snapshot (it used to land on Friday).
    assert snapshot_day(calendar, now=datetime(2026, 10, 17, 5, 0, tzinfo=UTC)) is None
    # An ordinary Thursday at 13:00 Taipei is filed under that day.
    assert snapshot_day(calendar, now=datetime(2026, 10, 15, 5, 0, tzinfo=UTC)) == date(
        2026, 10, 15
    )
