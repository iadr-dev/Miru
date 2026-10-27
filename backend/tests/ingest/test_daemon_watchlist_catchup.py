"""The daemon and the 08:00 `strategy_watchlist` window: a daemon that comes up after 08:00 on a
trading day builds the day's list once, through the same job function the schedule uses."""

from __future__ import annotations

from datetime import date as date_
from datetime import time as time_

import structlog.testing

from analytics.strategy_watch.batch import PRICE_EOD_DATASET, STRATEGY_WATCHLIST_DATASET
from ingest.context import IngestContext
from ingest.daemon import WATCHLIST_JOB, IngestDaemon, ScheduledIngestJob
from ingest.journal import JobRun, RunStatus
from ingest.leader import AlwaysLeader
from ingest.watchlist_catchup import CATCHUP_DETAIL
from tests.ingest.test_daemon import _ctx, _taipei

TUESDAY = date_(2024, 1, 2)


class _WatchlistJob:
    """Stands in for `job_strategy_watchlist`: records calls, writes the partition on SUCCESS."""

    def __init__(self, status: RunStatus = RunStatus.SUCCESS) -> None:
        self.status = status
        self.calls: list[date_] = []

    async def __call__(self, run_date: date_, ctx: IngestContext) -> JobRun:
        self.calls.append(run_date)
        if self.status is RunStatus.SUCCESS:
            ctx.store.write_partition(
                STRATEGY_WATCHLIST_DATASET, ctx.market.value, run_date, [{"symbol": "2330"}]
            )
        return JobRun(dataset=WATCHLIST_JOB, run_date=run_date, rows_written=1, status=self.status)


class _Follower:
    def is_leader(self) -> bool:
        return False


class _Boom(_WatchlistJob):
    async def __call__(self, run_date: date_, ctx: IngestContext) -> JobRun:
        self.calls.append(run_date)
        raise RuntimeError("boom")


def _daemon(
    tmp_path: object, job: _WatchlistJob, *, leader: object | None = None
) -> tuple[IngestDaemon, IngestContext]:
    ctx = _ctx(tmp_path, leader=leader or AlwaysLeader())
    daemon = IngestDaemon(ctx, jobs=(ScheduledIngestJob(WATCHLIST_JOB, time_(8, 0), job),))
    return daemon, ctx


async def test_a_restart_inside_the_session_builds_the_missing_list_once(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, ctx = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 10, 30))

    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 31)) == []
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 11, 45)) == []
    assert job.calls == [TUESDAY]
    assert ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, "TW", TUESDAY)


async def test_an_existing_list_is_never_rebuilt(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, ctx = _daemon(tmp_path, job)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", TUESDAY, [{"symbol": "1101"}])
    daemon.seed_startup(now=_taipei(2024, 1, 2, 10, 30))

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 31))
    assert job.calls == []


async def test_nothing_is_built_after_the_session_close(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 13, 40))

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 13, 41))
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 19, 0))
    assert job.calls == []


async def test_the_session_close_minute_itself_still_builds(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 13, 0))

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 13, 30))
    assert job.calls == [TUESDAY]


async def test_nothing_is_built_on_a_non_trading_day(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 6, 10, 30))  # Saturday

    await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 10, 31))
    assert job.calls == []


async def test_a_follower_never_builds(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, _ = _daemon(tmp_path, job, leader=_Follower())
    daemon.seed_startup(now=_taipei(2024, 1, 2, 10, 30))

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 31))
    assert job.calls == []


async def test_a_stale_skip_is_retried_hourly_not_every_pass(tmp_path: object) -> None:
    job = _WatchlistJob(RunStatus.SKIPPED)
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 9, 0))

    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 1))
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 31))
    assert job.calls == [TUESDAY]

    job.status = RunStatus.SUCCESS
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 2))
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 3))
    assert job.calls == [TUESDAY, TUESDAY]


async def test_a_skip_is_retried_early_once_newer_prices_land(tmp_path: object) -> None:
    job = _WatchlistJob(RunStatus.SKIPPED)
    daemon, ctx = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 9, 0))
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 1))
    assert job.calls == [TUESDAY]

    job.status = RunStatus.SUCCESS
    ctx.store.write_partition(PRICE_EOD_DATASET, "TW", date_(2024, 1, 1), [{"symbol": "2330"}])
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 2))
    assert job.calls == [TUESDAY, TUESDAY]


async def test_a_failing_job_never_breaks_the_pass_and_waits_an_hour(tmp_path: object) -> None:
    job = _Boom()
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 9, 0))

    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 1)) == []
    await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 9, 20))
    assert job.calls == [TUESDAY]


async def test_the_scheduled_run_that_published_nothing_is_not_repeated_in_its_own_pass(
    tmp_path: object,
) -> None:
    job = _WatchlistJob(RunStatus.SKIPPED)
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 7, 0))

    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 8, 1)) == [WATCHLIST_JOB]
    assert job.calls == [TUESDAY]


async def test_the_catch_up_run_is_journaled_as_a_startup_catch_up(tmp_path: object) -> None:
    job = _WatchlistJob()
    daemon, _ = _daemon(tmp_path, job)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 10, 30))
    with structlog.testing.capture_logs() as logs:
        await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 31))
    journaled = [
        e for e in logs if e["event"] == "ingest_run_journaled" and e["dataset"] == WATCHLIST_JOB
    ]
    assert [e["detail"] for e in journaled] == [f"{CATCHUP_DETAIL}: "]
