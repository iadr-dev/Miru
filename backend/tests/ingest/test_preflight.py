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
from ingest.preflight import last_available_trading_day, run_preflight_if_enabled
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
