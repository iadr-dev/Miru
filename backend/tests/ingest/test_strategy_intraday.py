"""`run_strategy_slot`: one 策略盯盤 slot against a stubbed whole-market snapshot, on a real
ParquetStore. No network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections.abc import Sequence
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
from sources.twse_mis import MisQuote, MisQuotes

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


class _Mis:
    """The free exchange feed stand-in: fixed quotes, records the codes asked for."""

    def __init__(
        self,
        quotes: list[MisQuote],
        index: MisQuote | None = None,
        status: HealthStatus = HealthStatus.OK,
    ) -> None:
        self.quotes, self.index, self.status = quotes, index, status
        self.calls: list[list[str]] = []

    async def __call__(self, symbols: Sequence[str], *, tls_policy: object) -> MisQuotes:
        del tls_policy
        self.calls.append(list(symbols))
        return MisQuotes(
            quotes=tuple(self.quotes),
            index=self.index,
            health=SourceHealth(status=self.status, detail="stub"),
        )


def _mis_quote(
    symbol: str = "2330",
    *,
    last: float | None = 872.0,
    prev_close: float = 860.0,
    volume_shares: int = 1_000_000,
    day: date = _RUN_DATE,
) -> MisQuote:
    return MisQuote(
        symbol=symbol,
        last=last,
        prev_close=prev_close,
        open=865.0,
        high=875.0,
        low=861.0,
        volume_shares=volume_shares,
        trade_date=day.isoformat(),
        observed_at="10:00:00",
    )


def _index(change_pct: float) -> MisQuote:
    return _mis_quote("t00", last=20000 * (1 + change_pct / 100), prev_close=20000.0)


def _ctx(root: Path, finmind: _Snapshot, mis: _Mis | None = None) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        intraday_quotes=mis,
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


# -- the free exchange feed (TWSE MIS), asked before FinMind ----------------------------------

_WATCH_WITH_AVERAGE = {**_WATCH, "avg_volume_20d": 1_000_000.0}


async def test_the_free_feed_is_asked_first_and_finmind_is_not(tmp_path: Path) -> None:
    finmind, mis = _Snapshot([]), _Mis([_mis_quote()], _index(0.5))
    ctx = _ctx(tmp_path, finmind, mis)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH_WITH_AVERAGE])

    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 1))

    assert run.status is RunStatus.SUCCESS
    assert "via twse_mis" in run.detail
    assert finmind.calls == []
    assert mis.calls == [["2330"]]
    (row,) = ctx.store.read_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
    assert row["all_met"] is True
    assert row["price"] == 872.0
    # +1.395% against the previous close; 1M shares in 60 minutes against a 1M/day average.
    assert abs(float(str(row["change_rate"])) - 1.395) < 0.01
    assert abs(float(str(row["volume_ratio"])) - 4.5) < 0.01


async def test_a_falling_index_blocks_the_alert_even_when_the_stock_confirms(
    tmp_path: Path,
) -> None:
    mis = _Mis([_mis_quote()], _index(-2.0))
    ctx = _ctx(tmp_path, _Snapshot([]), mis)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH_WITH_AVERAGE])

    await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 41))
    await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 52))

    by_slot = {
        r["slot"]: r for r in ctx.store.read_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
    }
    assert by_slot["09:50"]["confirmed"] is True
    assert by_slot["09:50"]["alertable"] is False


async def test_an_unreadable_free_feed_falls_back_to_finmind(tmp_path: Path) -> None:
    finmind = _Snapshot([_quote()])
    mis = _Mis([], status=HealthStatus.UNREACHABLE)
    ctx = _ctx(tmp_path, finmind, mis)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])

    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 1))

    assert run.status is RunStatus.SUCCESS
    assert "via finmind" in run.detail
    assert finmind.calls == [Capability.TICK_SNAPSHOT]


async def test_both_sources_down_is_a_failed_run_that_names_both(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _Snapshot([], status=HealthStatus.UNAVAILABLE_TIER), _Mis([], None))
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH])

    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(10, 1))

    assert run.status is not RunStatus.SUCCESS
    assert "free feed" in run.detail


async def test_no_trade_yet_today_is_a_skip_not_a_finmind_call(tmp_path: Path) -> None:
    finmind = _Snapshot([_quote()])
    mis = _Mis([_mis_quote(last=None)], _index(0.0))
    ctx = _ctx(tmp_path, finmind, mis)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH_WITH_AVERAGE])

    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 0))

    assert run.status is RunStatus.SKIPPED
    assert finmind.calls == []


async def test_quotes_from_another_day_are_not_compared(tmp_path: Path) -> None:
    mis = _Mis([_mis_quote(day=date(2026, 10, 15))], _index(0.0))
    ctx = _ctx(tmp_path, _Snapshot([]), mis)
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE, [_WATCH_WITH_AVERAGE])

    run = await run_strategy_slot(_RUN_DATE, ctx, at=time_(9, 10))

    assert run.status is RunStatus.SKIPPED
    assert not ctx.store.has_partition(STRATEGY_HITS_DATASET, "TW", _RUN_DATE)
