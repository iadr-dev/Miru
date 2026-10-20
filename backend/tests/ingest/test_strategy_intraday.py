"""`run_strategy_slot`: one 策略盯盤 slot against a stubbed whole-market snapshot, on a real
ParquetStore. No network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime
from datetime import time as time_
from pathlib import Path

from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from analytics.strategy_watch.strategies import Strategy
from ingest.context import IngestContext
from ingest.jobs.strategy_intraday import (
    STRATEGY_HITS_DATASET,
    job_strategy_close_check,
    run_strategy_slot,
)
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult

_RUN_DATE = date(2026, 10, 16)  # a Friday
_WATCH = {
    "symbol": "2330",
    "strategy": Strategy.VOLATILITY_SQUEEZE.value,
    "upper_band": 868.0,
    "close_5d_ago": 851.0,
}


class _Snapshot:
    """A FinMind stand-in that returns one fixed snapshot and records what was asked for."""

    def __init__(self, rows: list[dict[str, object]], status: HealthStatus = HealthStatus.OK):
        self.rows = rows
        self.status = status
        self.calls: list[Capability] = []

    async def fetch_whole_market(self, cap: Capability, *, tls_policy: object) -> SourceResult:
        del tls_policy
        self.calls.append(cap)
        return SourceResult(
            source_id="FINMIND",
            data_class=DataClass.SPONSOR,
            timing_class=TimingClass.LIVE_ISH,
            fetched_at=datetime.now(UTC),
            currentness_caveat="test",
            payload={"rows": self.rows},
            health=SourceHealth(status=self.status, detail="stub"),
        )


def _quote(day: date = _RUN_DATE, close: float = 872.0) -> dict[str, object]:
    return {
        "symbol": "2330",
        "trade_date": day.isoformat(),
        "close": close,
        "volume_ratio": 2.4,
        "average_price": 865.0,
        "change_rate": 1.5,
    }


def _ctx(root: Path, finmind: _Snapshot) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


async def test_two_slots_confirm_a_hit(tmp_path: Path) -> None:
    finmind = _Snapshot([_quote()])
    ctx = _ctx(tmp_path, finmind)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])

    first = await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 41))
    second = await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 52))

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert finmind.calls == [Capability.TICK_SNAPSHOT, Capability.TICK_SNAPSHOT]
    rows = ctx.store.read_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
    by_slot = {r["slot"]: r for r in rows}
    assert by_slot["09:40"]["all_met"] is True
    assert by_slot["09:40"]["alertable"] is False
    assert by_slot["09:50"]["confirmed"] is True
    assert by_slot["09:50"]["alertable"] is True


async def test_rerunning_a_slot_replaces_it(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([_quote()]))
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])

    await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 1))
    await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 7))

    rows = ctx.store.read_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
    assert [r["slot"] for r in rows] == ["10:00"]


async def test_no_watchlist_means_no_snapshot_call(tmp_path: Path) -> None:
    finmind = _Snapshot([_quote()])
    run = await run_strategy_slot(_RUN_DATE, _ctx(tmp_path, finmind), at=time_(10, 0))
    assert run.status is RunStatus.SKIPPED
    assert finmind.calls == []


async def test_a_snapshot_from_another_day_is_not_compared(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([_quote(day=date(2026, 10, 15))]))
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])
    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 0))
    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)


async def test_a_refused_snapshot_is_a_failed_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([], status=HealthStatus.UNREACHABLE))
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])
    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 0))
    assert run.status is RunStatus.FAILED


async def test_close_check_writes_a_close_slot_next_to_the_intraday_slots(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([_quote()]))
    watch = {**_WATCH, "avg_volume_20d": 20_000_000.0}
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [watch])
    await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 0))
    ctx.store.write_partition(
        "TaiwanStockPrice",
        "TW",
        _RUN_DATE,
        [{"symbol": "2330", "trade_date": _RUN_DATE.isoformat(), "close": 875.0, "volume": 4e7}],
    )

    run = await job_strategy_close_check(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    rows = ctx.store.read_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
    assert sorted(r["slot"] for r in rows) == ["10:00", "close"]
    close = next(r for r in rows if r["slot"] == "close")
    assert close["alertable"] is True


async def test_close_check_waits_for_todays_prices(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([]))
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])
    run = await job_strategy_close_check(_RUN_DATE, ctx)
    assert run.status is RunStatus.SKIPPED
    assert "not landed" in run.detail
