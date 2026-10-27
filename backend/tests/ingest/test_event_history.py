"""`ingest.event_history` - past daily snapshots of the attention and disposition feeds rebuilt from
the exchanges' range reports (a stub source here; the real answers are in the source tests)."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest

import ingest.backfill_cli as backfill_cli_module
from analytics.event_study.events import EventDateFilter
from analytics.event_study.loader import load_snapshot_events
from analytics.event_study.models import EventType
from config.settings import Settings
from ingest.backfill_cli import ProductionHardStopError, parse_args, run_event_history_backfill
from ingest.context import IngestContext
from ingest.event_history import (
    attention_snapshot,
    backfill_event_history,
    disposition_snapshot,
    month_chunks,
)
from ingest.jobs import ATTENTION_STOCKS_DATASET, DISPOSITION_EVENTS_DATASET
from ingest.journal import JobRun, RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.exchange_event_history import EventHistory, ExchangeEventHistoryAdapter
from sources.exchange_whole_market._common import ExchangeFetchError
from sources.finmind import FinMindAdapter

MARKET = "TW"


def _disposition(
    symbol: str, announced: str, start: str, end: str, kind: str = "twse"
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": MARKET,
        "company_name": f"name {symbol}",
        "announced_date": announced,
        "reason": "r",
        "period_start": start,
        "period_end": end,
        "measure": "m",
        "detail": None,
        "security_type": kind,
    }


def _flag(symbol: str, flagged: str, kind: str = "tpex") -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": MARKET,
        "company_name": f"name {symbol}",
        "flagged_date": flagged,
        "reason": "r",
        "close": 10.5,
        "pe": None,
        "security_type": kind,
    }


class StubSource:
    """Stands in for `ExchangeEventHistoryAdapter`: answers a range with the rows of a ledger
    that touch it, as the exchanges' reports do, and records every request."""

    def __init__(
        self,
        dispositions: list[dict[str, object]] | None = None,
        flags: list[dict[str, object]] | None = None,
    ) -> None:
        self.dispositions = dispositions or []
        self.flags = flags or []
        self.calls: list[tuple[str, date, date]] = []
        self.failing_months: set[tuple[str, int]] = set()  # (kind, month) -> ExchangeFetchError

    def _check(self, kind: str, start: date, end: date) -> None:
        self.calls.append((kind, start, end))
        if (kind, end.month) in self.failing_months:
            raise ExchangeFetchError("TPEx: response is not JSON after 2 tries")

    async def fetch_disposition(
        self, start: date, end: date, *, tls_policy: TLSPolicy
    ) -> EventHistory:
        self._check("disposition", start, end)
        rows = [
            r
            for r in self.dispositions
            if str(r["period_start"]) <= end.isoformat()
            and str(r["period_end"]) >= start.isoformat()
        ]
        return EventHistory(rows, len(rows), 0, no_data=not rows)

    async def fetch_attention(
        self, start: date, end: date, *, tls_policy: TLSPolicy
    ) -> EventHistory:
        self._check("attention", start, end)
        rows = [
            r for r in self.flags if start.isoformat() <= str(r["flagged_date"]) <= end.isoformat()
        ]
        return EventHistory(rows, 0, len(rows), no_data=not rows)

    def as_adapter(self) -> ExchangeEventHistoryAdapter:
        return self  # type: ignore[return-value]


def _ctx(tmp_path: Path, *, leader: object = None) -> IngestContext:
    settings = Settings(_env_file=None, finmind_api_key="test-token")  # type: ignore[call-arg,arg-type]
    return IngestContext(
        finmind=FinMindAdapter(settings=settings),
        store=ParquetStore(root=tmp_path),
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
    )


def _read(ctx: IngestContext, dataset: str, day: date) -> list[dict[str, object]]:
    return [
        {k: v for k, v in row.items() if k not in {"dataset", "date", "v"}}
        for row in ctx.store.read_partition(dataset, MARKET, day)
    ]


# ------------------------------------------------------------------------- snapshot rules


def test_a_disposition_is_listed_from_its_announcement_through_its_last_day() -> None:
    row = _disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09")

    listed = {
        d: bool(disposition_snapshot([row], d))
        for d in map(
            date.fromisoformat,
            (
                "2026-09-01",
                "2026-09-02",
                "2026-09-03",
                "2026-09-09",
                "2026-09-10",
            ),
        )
    }

    assert listed == {
        date(2026, 9, 1): False,  # not announced yet
        date(2026, 9, 2): True,  # announced, period starts the next day
        date(2026, 9, 3): True,
        date(2026, 9, 9): True,  # the last day of the period
        date(2026, 9, 10): False,  # over
    }


def test_an_extension_is_a_second_row_and_both_are_listed_while_they_overlap() -> None:
    first = _disposition("6187", "2024-01-23", "2024-01-24", "2024-02-02")
    extension = _disposition("6187", "2024-02-01", "2024-02-05", "2024-02-15")

    rows = disposition_snapshot([first, extension], date(2024, 2, 2))

    assert [(r["period_start"], r["period_end"]) for r in rows] == [
        ("2024-01-24", "2024-02-02"),
        ("2024-02-05", "2024-02-15"),
    ]


def test_listed_and_otc_rows_of_one_day_are_merged_and_a_repeat_is_one_row() -> None:
    twse = _disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09", "twse")
    tpex = _disposition("6187", "2026-09-02", "2026-09-03", "2026-09-09", "tpex")

    rows = disposition_snapshot([tpex, twse, dict(twse)], date(2026, 9, 4))

    assert [(r["security_type"], r["symbol"]) for r in rows] == [("tpex", "6187"), ("twse", "2330")]


def test_the_attention_list_of_a_monday_holds_that_monday_and_the_friday_before() -> None:
    history = [
        _flag("3374", "2026-09-10"),
        _flag("3374", "2026-09-11"),
        _flag("3441", "2026-09-14"),
        _flag("6173", "2026-09-15"),
    ]

    rows = attention_snapshot(history, date(2026, 9, 14), date(2026, 9, 11))

    assert [(r["flagged_date"], r["symbol"]) for r in rows] == [
        ("2026-09-11", "3374"),
        ("2026-09-14", "3441"),
    ]


def test_days_group_by_calendar_month_oldest_first() -> None:
    days = [date(2024, 2, 1), date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 2)]

    assert month_chunks(days) == [
        [date(2024, 1, 30), date(2024, 1, 31)],
        [date(2024, 2, 1), date(2024, 2, 2)],
    ]


# ------------------------------------------------------------------------------- the backfill


FRI = date(2026, 9, 4)
MON = date(2026, 9, 7)
TUE = date(2026, 9, 8)


async def test_it_writes_one_partition_per_trading_day_in_the_stored_row_shape(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    disposition = _disposition("2330", "2026-09-04", "2026-09-07", "2026-09-08")
    flag = _flag("3374", "2026-09-04")
    source = StubSource([disposition], [flag])

    runs = await backfill_event_history(ctx, start=FRI, end=TUE, adapter=source.as_adapter())

    assert {(r.dataset, r.run_date) for r in runs} == {
        (ds, d)
        for ds in (ATTENTION_STOCKS_DATASET, DISPOSITION_EVENTS_DATASET)
        for d in (FRI, MON, TUE)
    }  # the weekend is no trading day
    assert all(r.status is RunStatus.SUCCESS for r in runs)
    assert _read(ctx, DISPOSITION_EVENTS_DATASET, FRI) == [disposition]
    assert _read(ctx, DISPOSITION_EVENTS_DATASET, TUE) == [disposition]
    assert _read(ctx, ATTENTION_STOCKS_DATASET, FRI) == [flag]
    assert _read(ctx, ATTENTION_STOCKS_DATASET, MON) == [flag]  # still the previous trading day
    assert ctx.store.list_partition_dates(ATTENTION_STOCKS_DATASET, MARKET) == (FRI, MON)
    assert ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, TUE)  # an empty day is a day


async def test_it_asks_once_per_dataset_and_month_and_reaches_back_a_day_for_attention(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    source = StubSource()

    await backfill_event_history(
        ctx, start=date(2026, 8, 31), end=date(2026, 9, 2), adapter=source.as_adapter()
    )

    assert sorted(source.calls) == [
        ("attention", date(2026, 8, 28), date(2026, 8, 31)),  # Friday before the first Monday
        ("attention", date(2026, 8, 31), date(2026, 9, 2)),  # reaches back into August
        ("disposition", date(2026, 8, 31), date(2026, 8, 31)),
        ("disposition", date(2026, 9, 1), date(2026, 9, 2)),
    ]


async def test_a_stored_snapshot_is_never_replaced_and_a_second_run_asks_nothing(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    real = _disposition("9999", "2026-09-04", "2026-09-07", "2026-09-08", "tpex")
    ctx.store.write_partition(DISPOSITION_EVENTS_DATASET, MARKET, MON, [real])
    source = StubSource([_disposition("2330", "2026-09-04", "2026-09-07", "2026-09-08")])

    await backfill_event_history(ctx, start=FRI, end=TUE, adapter=source.as_adapter())
    asked = len(source.calls)
    again = await backfill_event_history(ctx, start=FRI, end=TUE, adapter=source.as_adapter())

    assert _read(ctx, DISPOSITION_EVENTS_DATASET, MON) == [real]
    assert [r["symbol"] for r in _read(ctx, DISPOSITION_EVENTS_DATASET, FRI)] == ["2330"]
    assert again == ()
    assert len(source.calls) == asked


async def test_a_month_that_cannot_be_read_is_failed_and_written_by_the_next_run(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    flag_aug = _flag("3374", "2026-08-31")
    flag_sep = _flag("3441", "2026-09-01")
    source = StubSource(flags=[flag_aug, flag_sep])
    source.failing_months = {("attention", 8)}
    window = {"start": date(2026, 8, 31), "end": date(2026, 9, 1)}

    runs = await backfill_event_history(ctx, adapter=source.as_adapter(), **window)

    failed = [r for r in runs if r.status is RunStatus.FAILED]
    assert [(r.dataset, r.run_date) for r in failed] == [
        (ATTENTION_STOCKS_DATASET, date(2026, 8, 31))
    ]
    assert "not JSON" in failed[0].detail
    assert not ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, date(2026, 8, 31))
    assert ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, date(2026, 9, 1))

    source.failing_months = set()
    await backfill_event_history(ctx, adapter=source.as_adapter(), **window)

    assert _read(ctx, ATTENTION_STOCKS_DATASET, date(2026, 8, 31)) == [flag_aug]


async def test_a_month_the_exchanges_published_nothing_for_is_written_empty_and_said_so(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)

    runs = await backfill_event_history(ctx, start=FRI, end=MON, adapter=StubSource().as_adapter())

    assert all(r.status is RunStatus.SUCCESS and r.rows_written == 0 for r in runs)
    assert all("no data published" in r.detail for r in runs)
    assert ctx.store.has_partition(DISPOSITION_EVENTS_DATASET, MARKET, FRI)
    assert ctx.store.list_partition_dates(DISPOSITION_EVENTS_DATASET, MARKET) == ()


async def test_a_process_without_the_lease_asks_nothing_and_writes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, leader=NeverLeader())
    source = StubSource()

    runs = await backfill_event_history(ctx, start=FRI, end=TUE, adapter=source.as_adapter())

    assert [r.status for r in runs] == [RunStatus.SKIPPED]
    assert source.calls == []


async def test_a_row_the_stored_model_rejects_is_dropped_not_written(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    broken = _disposition("2330", "2026-09-04", "2026-09-07", "2026-09-08")
    broken["period_end"] = "not a date"
    source = StubSource([broken, _disposition("2317", "2026-09-04", "2026-09-07", "2026-09-08")])

    await backfill_event_history(ctx, start=MON, end=MON, adapter=source.as_adapter())

    assert [r["symbol"] for r in _read(ctx, DISPOSITION_EVENTS_DATASET, MON)] == ["2317"]


async def test_the_event_study_reads_the_rebuilt_series_with_no_change(tmp_path: Path) -> None:
    """The rebuilt snapshots give the loader the events the ledger holds: a disposition start,
    its release (an extension is not one) and every attention flag, once each."""
    ctx = _ctx(tmp_path)
    ledger = [
        _disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09"),
        _disposition("2317", "2026-09-02", "2026-09-03", "2026-09-08"),
        _disposition("2317", "2026-09-08", "2026-09-09", "2026-09-15"),  # extends 2317
        _disposition("03123P", "2026-09-02", "2026-09-03", "2026-09-09"),  # a warrant
    ]
    flags = [_flag("3374", "2026-09-03"), _flag("3374", "2026-09-08"), _flag("3441", "2026-09-04")]
    source = StubSource(ledger, flags)
    start, end = date(2026, 9, 2), date(2026, 9, 16)
    await backfill_event_history(ctx, start=start, end=end, adapter=source.as_adapter())
    calendar = list(ctx.calendar.business_days_between(date(2026, 9, 1), date(2026, 9, 18)))
    everything = EventDateFilter()

    starts, coverage = load_snapshot_events(
        ctx.store, EventType.DISPOSITION_START, everything, calendar
    )
    ends, _ = load_snapshot_events(ctx.store, EventType.DISPOSITION_END, everything, calendar)
    attention, att_coverage = load_snapshot_events(
        ctx.store, EventType.ATTENTION_LISTED, everything, calendar
    )

    assert [(e.symbol, e.event_date) for e in starts.events] == [
        ("2317", date(2026, 9, 3)),
        ("2330", date(2026, 9, 3)),
        ("2317", date(2026, 9, 9)),
    ]
    assert [(e.symbol, e.event_date) for e in ends.events] == [
        ("2330", date(2026, 9, 10)),
        ("2317", date(2026, 9, 16)),
    ]
    assert [(e.symbol, e.event_date) for e in attention.events] == [
        ("3374", date(2026, 9, 3)),
        ("3441", date(2026, 9, 4)),
        ("3374", date(2026, 9, 8)),
    ]
    assert starts.excluded_non_equity > 0
    assert coverage.first_snapshot == date(2026, 9, 2)
    assert att_coverage.n_snapshots > 1


# --------------------------------------------------------------------------------- the CLI


def _settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[arg-type]
        finmind_api_key="test-token",
        **overrides,  # type: ignore[arg-type]
    )


def test_the_event_history_mode_is_its_own_mode() -> None:
    assert parse_args(["--yes", "--event-history", "--years", "2"]).event_history is True
    assert parse_args(["--yes"]).event_history is False
    for bad in (
        ["--event-history", "--market", "us"],
        [
            "--event-history",
            "--fill-missing-companies",
            "TaiwanStockMonthRevenue",
            "--dates",
            "2026-02-02",
        ],
    ):
        with pytest.raises(SystemExit):
            parse_args(bad)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[date, date, bool]]:
    calls: list[tuple[date, date, bool]] = []

    async def fake(
        _ctx: IngestContext, *, start: date, end: date, only_missing: bool = True
    ) -> tuple[JobRun, ...]:
        calls.append((start, end, only_missing))
        return ()

    monkeypatch.setattr(backfill_cli_module, "backfill_event_history", fake)
    return calls


async def test_the_cli_refuses_without_yes_and_never_in_production(
    tmp_path: Path, seen: list[tuple[date, date, bool]]
) -> None:
    ctx = _ctx(tmp_path)

    assert await run_event_history_backfill(_settings(), ctx, override=False, years=1) == ()
    with pytest.raises(ProductionHardStopError):
        await run_event_history_backfill(_settings(env="production"), ctx, override=True, years=1)

    assert seen == []


async def test_the_cli_walks_the_window_of_years_ending_at_the_last_trading_day(
    tmp_path: Path, seen: list[tuple[date, date, bool]]
) -> None:
    ctx = _ctx(tmp_path)

    await run_event_history_backfill(_settings(), ctx, override=True, years=1)

    ((start, end, only_missing),) = seen
    assert only_missing is True
    assert end.weekday() < 5
    assert len(ctx.calendar.business_days_between(start, end)) == 251


# ------------------------------------------------------------------- days the reports trail


async def test_the_newest_days_stop_where_the_slower_exchange_stops(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    flags = [_flag("3374", "2026-09-08", "tpex"), _flag("2330", "2026-09-07", "twse")]
    source = StubSource(flags=flags)
    window = {"start": FRI, "end": date(2026, 9, 10)}

    runs = await backfill_event_history(
        ctx, adapter=source.as_adapter(), today=date(2026, 9, 11), **window
    )

    # (a recent month with no disposition row at all is skipped too: nothing proves it posted)
    skipped = [r for r in runs if r.status is RunStatus.SKIPPED]
    assert [(r.dataset, r.run_date) for r in skipped] == [
        (ATTENTION_STOCKS_DATASET, TUE),
        (DISPOSITION_EVENTS_DATASET, FRI),
    ]
    skipped = skipped[:1]
    assert "3 days from 2026-09-08 not posted yet" in skipped[0].detail
    assert "2026-09-07" in skipped[0].detail
    written = [
        d for d in (FRI, MON, TUE) if ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, d)
    ]
    assert written == [FRI, MON]

    source.flags.append(_flag("6173", "2026-09-10", "twse"))
    source.flags.append(_flag("6199", "2026-09-10", "tpex"))
    await backfill_event_history(
        ctx, adapter=source.as_adapter(), today=date(2026, 9, 11), **window
    )

    assert ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, TUE)
    assert ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, date(2026, 9, 10))


async def test_a_quiet_stretch_in_an_old_month_is_a_written_empty_list(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    source = StubSource(
        flags=[_flag("3374", "2026-09-04", "tpex"), _flag("2330", "2026-09-04", "twse")]
    )

    await backfill_event_history(
        ctx, start=FRI, end=date(2026, 9, 10), adapter=source.as_adapter(), today=date(2026, 12, 1)
    )

    assert ctx.store.has_partition(ATTENTION_STOCKS_DATASET, MARKET, date(2026, 9, 10))
    assert _read(ctx, ATTENTION_STOCKS_DATASET, date(2026, 9, 10)) == []
