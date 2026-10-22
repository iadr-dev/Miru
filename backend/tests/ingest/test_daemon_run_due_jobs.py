"""`IngestDaemon.run_due_jobs` pass-level behaviour: ordering, the mid-pass stop conditions
(lease lost, FinMind refusal), per-cadence trading-day gating and handled-marking."""

from __future__ import annotations

from datetime import date as date_
from datetime import time as time_

from ingest.context import IngestContext
from ingest.daemon import Cadence, IngestDaemon, ScheduledIngestJob
from ingest.journal import JobRun
from ingest.leader import AlwaysLeader
from schemas.enums import Market
from sources.finmind import FinMindQuotaRefusedError
from tests.ingest.test_daemon import _ctx, _SpyJob, _taipei


class _ScriptedLeader:
    """`is_leader()` answers from a script, then keeps repeating the last answer."""

    def __init__(self, *answers: bool) -> None:
        self._answers = list(answers)
        self.calls = 0

    def is_leader(self) -> bool:
        index = min(self.calls, len(self._answers) - 1)
        self.calls += 1
        return self._answers[index]


async def test_jobs_run_in_declared_order_and_the_names_are_returned_in_that_order(
    tmp_path: object,
) -> None:
    first, second = _SpyJob("a"), _SpyJob("b")
    jobs = (
        ScheduledIngestJob("a", time_(21, 30), first),
        ScheduledIngestJob("b", time_(21, 0), second),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 0)) == ["a", "b"]


async def test_a_lease_lost_before_the_first_job_stops_the_pass_and_runs_nothing(
    tmp_path: object,
) -> None:
    spy = _SpyJob("a")
    jobs = (ScheduledIngestJob("a", time_(21, 30), spy),)
    # refresh at the top, the is_leader() gate, then the per-job re-check fails.
    leader = _ScriptedLeader(True, True, False)
    daemon = IngestDaemon(_ctx(tmp_path, leader=leader), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 0)) == []
    assert spy.calls == []


async def test_a_lease_lost_mid_pass_keeps_the_jobs_already_run_and_skips_the_rest(
    tmp_path: object,
) -> None:
    first, second = _SpyJob("a"), _SpyJob("b")
    jobs = (
        ScheduledIngestJob("a", time_(21, 30), first),
        ScheduledIngestJob("b", time_(21, 30), second),
    )
    leader = _ScriptedLeader(True, True, True, False)
    daemon = IngestDaemon(_ctx(tmp_path, leader=leader), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 0)) == ["a"]
    assert first.calls == [date_(2024, 1, 2)]
    assert second.calls == []


async def test_a_finmind_refusal_stops_the_pass_and_the_job_is_not_retried_that_day(
    tmp_path: object,
) -> None:
    async def refused(run_date: date_, ctx: IngestContext) -> JobRun:
        del run_date, ctx
        raise FinMindQuotaRefusedError("402 after reset")

    later = _SpyJob("later")
    jobs = (
        ScheduledIngestJob("refused", time_(21, 30), refused),
        ScheduledIngestJob("later", time_(21, 30), later),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 0)) == []
    assert later.calls == []
    # The refused job was marked handled before it ran; the untouched one is still due.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 5)) == ["later"]


async def test_an_errored_job_is_not_retried_within_the_same_day(tmp_path: object) -> None:
    attempts: list[date_] = []

    async def boom(run_date: date_, ctx: IngestContext) -> JobRun:
        del ctx
        attempts.append(run_date)
        raise RuntimeError("upstream hiccup")

    jobs = (ScheduledIngestJob("a", time_(21, 30), boom),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 0)) == []
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 22, 5)) == []
    assert attempts == [date_(2024, 1, 2)]


async def test_weekly_and_monthly_jobs_are_not_trading_day_gated_but_daily_ones_are(
    tmp_path: object,
) -> None:
    daily, weekly, monthly = _SpyJob("d"), _SpyJob("w"), _SpyJob("m")
    jobs = (
        ScheduledIngestJob("d", time_(6, 0), daily),
        ScheduledIngestJob("w", time_(6, 0), weekly, Cadence.WEEKLY),
        ScheduledIngestJob("m", time_(6, 0), monthly, Cadence.MONTHLY),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    saturday = _taipei(2024, 1, 6, 7, 0)
    assert await daemon.run_due_jobs(now=saturday) == ["w", "m"]
    assert daily.calls == []
    assert weekly.calls == [date_(2024, 1, 6)]
    assert monthly.calls == [date_(2024, 1, 6)]


async def test_a_non_trading_day_skip_is_marked_handled_so_it_is_not_logged_again_that_day(
    tmp_path: object,
) -> None:
    spy = _SpyJob("d")
    jobs = (ScheduledIngestJob("d", time_(6, 0), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 7, 0)) == []
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 9, 0)) == []
    assert spy.calls == []
    # Monday it fires again.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 8, 7, 0)) == ["d"]


async def test_a_us_job_with_no_new_session_does_not_block_the_jobs_after_it(
    tmp_path: object,
) -> None:
    us, tw = _SpyJob("us"), _SpyJob("tw")
    jobs = (
        ScheduledIngestJob("us", time_(6, 0), us, market=Market.US),
        ScheduledIngestJob("tw", time_(6, 0), tw),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    # Monday 06:01 Taipei: the US session that last settled is Friday's.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 8, 6, 1)) == ["us", "tw"]
    assert us.calls == [date_(2024, 1, 5)]
    assert tw.calls == [date_(2024, 1, 8)]
    # Tuesday 06:01 Taipei: Monday's US session settled (Mon 17:01 EST) - a new session.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 9, 6, 1)) == ["us", "tw"]
    assert us.calls == [date_(2024, 1, 5), date_(2024, 1, 8)]
    # Same Tuesday session again on a later pass: handled today -> nothing more.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 9, 6, 30)) == []


async def test_a_us_job_whose_session_was_already_pulled_is_skipped(
    tmp_path: object,
) -> None:
    us, tw = _SpyJob("us"), _SpyJob("tw")
    jobs = (
        ScheduledIngestJob("us", time_(6, 0), us, market=Market.US),
        ScheduledIngestJob("tw", time_(6, 30), tw),
    )
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 6, 6, 10)) == ["us"]  # Sat: Friday's
    assert us.calls == [date_(2024, 1, 5)]
    # Sunday: still Friday's session - the US job is claimed but has nothing new to pull.
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 7, 6, 10)) == []
    assert us.calls == [date_(2024, 1, 5)]
