"""The daemon and the 08:00 `strategy_watchlist` window: what a restart after 08:00 does."""

from __future__ import annotations

from datetime import time as time_

from ingest.daemon import IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader
from tests.ingest.test_daemon import _ctx, _SpyJob, _taipei


async def test_a_restart_after_the_morning_window_never_builds_that_days_watchlist(
    tmp_path: object,
) -> None:
    """Characterization: `seed_startup` marks the elapsed 08:00 window handled, so a daemon that
    comes up at 10:30 leaves the day without a watchlist (the gap the catch-up closes)."""
    spy = _SpyJob("strategy_watchlist")
    jobs = (ScheduledIngestJob("strategy_watchlist", time_(8, 0), spy),)
    daemon = IngestDaemon(_ctx(tmp_path, leader=AlwaysLeader()), jobs=jobs)
    daemon.seed_startup(now=_taipei(2024, 1, 2, 10, 30))

    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 10, 31)) == []
    assert await daemon.run_due_jobs(now=_taipei(2024, 1, 2, 11, 45)) == []
    assert spy.calls == []
