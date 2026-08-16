"""Ingest daemon scheduling tests: wall-clock due-detection, once-per-day, leader gate,
non-trading-day skip, startup seeding — plus a real end-to-end pass that drives an actual
`job_price_eod` through to a landed partition via the recorded fixture (no live network)."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from zoneinfo import ZoneInfo

import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS, IngestDaemon, ScheduledIngestJob
from ingest.jobs import (
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    ALL_WHOLE_MARKET_JOBS,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    DIVIDENDS_JOB,
    FINANCIALS_JOB,
    INSTITUTIONAL_FLOW_JOB,
    MARKET_VALUE_JOB,
    PRICE_EOD_JOB,
    job_active_etf_holding_changes,
    job_active_etf_holdings,
    job_balance_sheet,
    job_cash_flow,
    job_dividends,
    job_financials,
    job_institutional_flow,
    job_market_value,
    job_price_eod,
)
from ingest.journal import JobRun, RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory

_TAIPEI = ZoneInfo("Asia/Taipei")


def _settings() -> Settings:
    return Settings(finmind_api_key="test-token")


def _ctx(tmp_path: object, *, leader: object) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=_settings()),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _taipei(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TAIPEI)


class _SpyJob:
    """A realistically-shaped stub job function: records each call and returns a SUCCESS run,
    matching the real `IngestJobFn` async signature."""

    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        self.calls: list[date_] = []

    async def __call__(self, run_date: date_, ctx: IngestContext) -> JobRun:
        del ctx
        self.calls.append(run_date)
        return JobRun(
            dataset=self.dataset, run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )


async def test_job_fires_once_after_its_time_and_not_again_same_day(tmp_path: object) -> None:
    spy = _SpyJob("price_eod")
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    tuesday = date_(2024, 1, 2)

    # Before the fire time: nothing due.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 17, 0)) == []
    assert spy.calls == []

    # After the fire time: fires exactly once.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 17, 45)) == ["price_eod"]
    assert spy.calls == [tuesday]

    # A later pass the same day does not re-fire.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 21, 0)) == []
    assert spy.calls == [tuesday]


async def test_fires_again_the_next_trading_day(tmp_path: object) -> None:
    spy = _SpyJob("price_eod")
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 18, 0))
    await daemon.run_due_jobs(now=_taipei(2024, 1, 3, 18, 0))

    assert spy.calls == [date_(2024, 1, 2), date_(2024, 1, 3)]


async def test_non_leader_pass_is_silent_noop(tmp_path: object) -> None:
    spy = _SpyJob("price_eod")
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=NeverLeader()), jobs=jobs)

    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 18, 0)) == []
    assert spy.calls == []


async def test_non_trading_day_is_skipped_not_run(tmp_path: object) -> None:
    spy = _SpyJob("price_eod")
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)

    # 2024-01-06 is a Saturday — past the fire time but not a trading day: no run, no re-attempt.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 18, 0)) == []
    assert spy.calls == []
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 22, 0)) == []
    assert spy.calls == []


async def test_seed_startup_suppresses_already_elapsed_window(tmp_path: object) -> None:
    spy = _SpyJob("price_eod")
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)

    # Boot at 20:00 — the 17:40 window already passed today; seeding suppresses a replay.
    daemon.seed_startup(now=_taipei(2024, 1, 2, 20, 0))
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 20, 1)) == []
    assert spy.calls == []


async def test_one_job_error_does_not_block_others_in_pass(tmp_path: object) -> None:
    async def _boom(run_date: date_, ctx: IngestContext) -> JobRun:
        del run_date, ctx
        raise RuntimeError("upstream hiccup")

    ok = _SpyJob("dividends")
    jobs = (
        ScheduledIngestJob("financials", time_(21, 30), _boom),
        ScheduledIngestJob("dividends", time_(21, 30), ok),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)

    ran = await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 21, 35))

    assert ran == ["dividends"]
    assert ok.calls == [date_(2024, 1, 2)]


async def test_pass_drives_real_job_price_eod_end_to_end(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure-mode guard: prove the daemon actually invokes a REAL job function through to a
    landed partition — not just that its internal schedule bookkeeping looks right. Uses the same
    recorded fixture + monkeypatched client the job's own unit tests use; no live network."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(body))
    ctx = _ctx(tmp_path, leader=AlwaysLeader())
    jobs = (ScheduledIngestJob("price_eod", time_(17, 40), job_price_eod),)
    daemon = IngestDaemon(ctx, jobs=jobs)
    trading_day = date_(2024, 1, 2)

    ran = await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 17, 45))

    assert ran == ["price_eod"]
    assert ctx.store.has_partition(PRICE_EOD_JOB.dataset, "TW", trading_day)


def test_every_whole_market_job_is_scheduled() -> None:
    """Regression guard for the gap this test was added to close: `job_balance_sheet`,
    `job_cash_flow`, and `job_market_value` were fully implemented and exported (reachable from
    the backfill CLI's `ALL_WHOLE_MARKET_JOBS`) but never added to `SCHEDULED_JOBS`, so the daily
    cron silently never refreshed those three datasets — only a manual backfill run ever
    populated them. `ALL_WHOLE_MARKET_JOBS` is the canonical list `jobs.py`'s own comment says a
    completeness-asserting test should walk; this is that test."""
    scheduled_run_fns = {job.run for job in SCHEDULED_JOBS}
    # `WholeMarketJobSpec` has no back-reference to its wrapping `job_*` function, so the mapping
    # is spelled out explicitly here rather than derived — deliberately verbose so a newly added
    # spec without a matching entry fails loudly instead of being silently skipped by a clever
    # but wrong inference.
    spec_to_job_fn = {
        PRICE_EOD_JOB: job_price_eod,
        INSTITUTIONAL_FLOW_JOB: job_institutional_flow,
        FINANCIALS_JOB: job_financials,
        DIVIDENDS_JOB: job_dividends,
        BALANCE_SHEET_JOB: job_balance_sheet,
        CASH_FLOW_JOB: job_cash_flow,
        MARKET_VALUE_JOB: job_market_value,
        ACTIVE_ETF_HOLDING_JOB: job_active_etf_holdings,
        ACTIVE_ETF_HOLDING_CHANGE_JOB: job_active_etf_holding_changes,
    }
    missing = [
        spec.dataset
        for spec in ALL_WHOLE_MARKET_JOBS
        if spec_to_job_fn[spec] not in scheduled_run_fns
    ]
    assert not missing, (
        f"WholeMarketJobSpec dataset(s) not wired into SCHEDULED_JOBS — the daily cron will "
        f"never refresh them, only a manual backfill will: {missing}"
    )


def test_us_jobs_are_scheduled() -> None:
    """Regression guard mirroring `test_every_whole_market_job_is_scheduled`: `job_us_price_eod`,
    `job_us_fundamentals`, and `job_us_symbol_universe` were fully implemented in `ingest/us_jobs.py`
    / `ingest/us_universe.py` but never added to `SCHEDULED_JOBS`, so the daily cron never refreshed
    US market data at all — only a manual backfill ever populated it."""
    from ingest.us_jobs import job_us_fundamentals, job_us_price_eod
    from ingest.us_universe import job_us_symbol_universe

    scheduled_run_fns = {job.run for job in SCHEDULED_JOBS}
    missing = [
        fn.__name__
        for fn in (job_us_price_eod, job_us_fundamentals, job_us_symbol_universe)
        if fn not in scheduled_run_fns
    ]
    assert not missing, (
        f"US job(s) not wired into SCHEDULED_JOBS — the daily cron will never refresh them: "
        f"{missing}"
    )
