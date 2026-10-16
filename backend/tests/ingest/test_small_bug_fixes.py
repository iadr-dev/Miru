"""Smaller ingest fixes found in the ingest/cron/backfill review: a lease lost mid-pass, a roster
snapshot walked over history, a dataset fetched before its publish time, an unguarded ETF job,
torn partitions, and partition-path columns written back as data."""

from __future__ import annotations

import shutil
from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from config.settings import Settings
from ingest.backfill import detect_missing_trading_days
from ingest.context import IngestContext
from ingest.daemon import IngestDaemon, ScheduledIngestJob
from ingest.jobs import (
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    STOCK_INFO_JOB,
    job_etf_aum,
    risk_lists,
)
from ingest.journal import JobRun, RunStatus
from ingest.leader import AlwaysLeader
from ingest.preflight import last_available_trading_day
from ingest.rows import without_path_columns
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

_TAIPEI = ZoneInfo("Asia/Taipei")


def _ctx(root: object, *, leader: object | None = None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(
            settings=Settings(
                finmind_api_key="test-token",
                finmind_base_url="https://api.finmindtrade.com/api/v4",
            )
        ),
        store=ParquetStore(root=root),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


class _LeaseLostAfter:
    """A leader that is held for the first `held_calls` checks and then loses the lease."""

    def __init__(self, held_calls: int) -> None:
        self._left = held_calls

    def is_leader(self) -> bool:
        self._left -= 1
        return self._left >= 0


class _Spy:
    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        self.calls: list[date] = []

    async def __call__(self, run_date: date, ctx: IngestContext) -> JobRun:
        del ctx
        self.calls.append(run_date)
        return JobRun(
            dataset=self.dataset, run_date=run_date, rows_written=1, status=RunStatus.SUCCESS
        )


async def test_a_pass_stops_when_the_lease_is_lost_between_jobs(tmp_path: object) -> None:
    first, second = _Spy("a"), _Spy("b")
    jobs = (
        ScheduledIngestJob("a", time(10, 0), first),
        ScheduledIngestJob("b", time(10, 5), second),
    )
    # Held for the pass check (2 reads) and the first job's check; gone by the second job.
    daemon = IngestDaemon(_ctx(tmp_path, leader=_LeaseLostAfter(3)), jobs=jobs)

    ran = await daemon.run_due_jobs(now=datetime(2024, 1, 9, 11, 0, tzinfo=_TAIPEI))

    assert ran == ["a"]
    assert second.calls == []


def test_the_market_value_datasets_are_published_after_2330_taipei() -> None:
    assert MARKET_VALUE_JOB.publish_after == time(23, 30)
    assert MARKET_VALUE_WEIGHT_JOB.publish_after == time(23, 30)
    calendar = TradingDayCalendar()
    evening = datetime(2024, 1, 11, 21, 30, tzinfo=_TAIPEI)  # Thursday 21:30
    late = datetime(2024, 1, 11, 23, 45, tzinfo=_TAIPEI)
    # A 21:30 gap-fill must not target a day that is published at 23:30.
    assert last_available_trading_day(calendar, now=evening, publish_time=time(23, 30)) == date(
        2024, 1, 10
    )
    assert last_available_trading_day(calendar, now=late, publish_time=time(23, 30)) == date(
        2024, 1, 11
    )
    # The usual 17:30 post-close publish is unchanged.
    assert last_available_trading_day(calendar, now=evening) == date(2024, 1, 11)


def test_a_roster_snapshot_is_gap_filled_for_the_latest_day_only(tmp_path: object) -> None:
    ctx = _ctx(tmp_path)
    end = date(2024, 1, 11)

    missing = detect_missing_trading_days(STOCK_INFO_JOB, ctx, start=date(2024, 1, 1), end=end)

    assert STOCK_INFO_JOB.snapshot_only is True
    assert missing == (end,)  # not every weekday back to the 1st, each filed with today's roster
    ctx.store.write_partition(STOCK_INFO_JOB.dataset, "TW", end, [{"stock_id": "2330"}])
    assert detect_missing_trading_days(STOCK_INFO_JOB, ctx, start=date(2024, 1, 1), end=end) == ()


async def test_etf_aum_is_skipped_on_a_non_trading_day(tmp_path: object) -> None:
    run = await job_etf_aum(date(2024, 1, 13), _ctx(tmp_path))  # a Saturday

    assert run.status is RunStatus.SKIPPED
    assert "non-trading" in (run.detail or "")


def test_a_torn_partition_is_skipped_by_every_reader(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    day, other = date(2024, 1, 10), date(2024, 1, 11)
    store.write_partition("TaiwanStockPrice", "TW", day, [{"symbol": "2330", "close": 1.0}])
    store.write_partition("TaiwanStockPrice", "TW", other, [{"symbol": "2330", "close": 2.0}])
    # Tear the first one: the manifest stays, the version directory it names disappears.
    shutil.rmtree(store.partition_files("TaiwanStockPrice", "TW", day)[0].parent)

    assert store.read_partition("TaiwanStockPrice", "TW", day) == []
    assert [r["close"] for r in store.read_range("TaiwanStockPrice", "TW", [day, other])] == [2.0]
    filtered = store.read_range("TaiwanStockPrice", "TW", [day, other], symbols=["2330"])
    assert [r["close"] for r in filtered] == [2.0]
    frame = store.read_range_frame("TaiwanStockPrice", "TW", [day, other], columns=["close"])
    assert list(frame["close"]) == [2.0]


def test_without_path_columns_drops_only_the_partition_path_columns() -> None:
    row = {"symbol": "2330", "dataset": "x", "market": "TW", "date": "2024-01-10", "v": "abc"}

    assert without_path_columns([row]) == [{"symbol": "2330"}]
    assert without_path_columns([row], keep=frozenset({"market"})) == [
        {"symbol": "2330", "market": "TW"}
    ]


def test_a_carried_forward_row_is_not_written_back_with_the_path_columns(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path)
    day = date(2024, 1, 10)
    ctx.store.write_partition(
        "attention_stocks",
        "TW",
        day,
        [
            {"symbol": "1101", "market": "TW", "security_type": "tpex"},
            {"symbol": "2330", "market": "TW", "security_type": "twse"},
        ],
    )

    carried = risk_lists._settled_rows(ctx, "attention_stocks", day, [], {"tpex"})

    assert carried == [{"symbol": "1101", "market": "TW", "security_type": "tpex"}]


def test_a_utc_clock_still_reads_the_taipei_calendar_day() -> None:
    now = datetime(2024, 1, 10, 18, 0, tzinfo=UTC)  # 02:00 Thursday in Taipei
    assert now.astimezone(_TAIPEI).date() == date(2024, 1, 11)
