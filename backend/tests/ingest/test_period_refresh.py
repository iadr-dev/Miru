"""The additive refresh of a period-keyed partition (`ingest/refresh.py`, `--fill-missing-companies`
on the backfill CLI): it adds the companies the stored partition lacks and never replaces or deletes
a stored row. Stub official source, frozen synthetic rows, no network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from ingest.backfill_cli import (
    ProductionHardStopError,
    parse_args,
    run_fill_missing_companies,
)
from ingest.context import IngestContext
from ingest.jobs import BALANCE_SHEET_JOB, FINANCIALS_JOB, MONTH_REVENUE_JOB, PRICE_EOD_JOB
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.refresh import fill_missing_companies, period_keyed_specs
from ingest.rows import rows_from_store, without_path_columns
from ingest.store import ParquetStore
from schemas.capabilities import MonthRevenueRow
from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.ingest.test_whole_market_runner import RecordingPublisher, StubSource

MONDAY_FEB_2 = date(2026, 2, 2)  # holds January 2026's revenue (filed under Sunday 2026-02-01)
JUNE_30 = date(2026, 6, 30)  # a Tuesday: 2026Q2
DETAIL = "MOPS t21sc03 2026-01: listed 3 + OTC 1 + emerging 0 rows"


def _revenue(symbol: str, revenue: int = 100, create_time: str | None = "2026-02-05") -> dict:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": "2026-02-01",
        "country": "Taiwan",
        "revenue": revenue,
        "revenue_month": 1,
        "revenue_year": 2026,
        "create_time": create_time,
    }


def _line(symbol: str, item: str, value: float = 1.0, kind: str = "balance_sheet") -> dict:
    return {
        "symbol": symbol,
        "market": "TW",
        "period_end": JUNE_30.isoformat(),
        "period_type": "quarterly",
        "statement_type": kind,
        "item": item,
        "origin_name": item,
        "value": value,
    }


def _result(
    rows: list[dict],
    status: HealthStatus = HealthStatus.OK,
    detail: str = DETAIL,
    key: str = "rows",
) -> SourceResult:
    return SourceResult(
        source_id="OFFICIAL_FILINGS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub",
        payload={key: rows},
        health=SourceHealth(status=status, detail=detail),
    )


def _ctx(
    tmp_path: object,
    official: StubSource | None,
    *,
    leader: object = None,
    publisher: RecordingPublisher | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]
        filings=official,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=publisher or RecordingPublisher(),
    )


def _data_rows(ctx: IngestContext) -> list[dict]:
    raw = ctx.store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)
    return without_path_columns(raw, keep=frozenset({"market"}))


def _stored_revenue(ctx: IngestContext) -> list[dict]:
    ctx.store.write_partition(
        MONTH_REVENUE_JOB.dataset,
        "TW",
        MONDAY_FEB_2,
        [_revenue("1101", 111), _revenue("2330", 222, create_time=None)],
    )
    return _data_rows(ctx)


def _by_symbol(ctx: IngestContext, spec=MONTH_REVENUE_JOB, run_date=MONDAY_FEB_2) -> dict:
    rows = ctx.store.read_partition(spec.dataset, "TW", run_date)
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(str(row["symbol"]), []).append(row)
    return out


async def test_it_adds_only_the_absent_companies_and_replaces_nothing(tmp_path: object) -> None:
    official = StubSource(
        _result([_revenue("1101", 999), _revenue("2330", 999), _revenue("2317", 333, None)])
    )
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, official, publisher=publisher)
    stored_before = _stored_revenue(ctx)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.SUCCESS
    assert (outcome.companies_before, outcome.companies_after) == (2, 3)
    assert outcome.run.rows_written == 1
    assert outcome.run.detail == (
        f"added 1 companies (1 rows), 2 -> 3; served by filings_official: {DETAIL}"
    )
    assert official.calls[0][1]["start_date"] == "2026-02-01"  # asked for the nominal date
    rows = _by_symbol(ctx)
    assert sorted(rows) == ["1101", "2317", "2330"]
    # the stored rows are the very same rows (FinMind's 111 / 222, not the official 999)
    stored_after = _data_rows(ctx)
    for kept in stored_before:
        assert kept in stored_after
    assert (
        len(
            rows_from_store(
                MonthRevenueRow,
                ctx.store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2),
            )
        )
        == 3
    )  # every merged row still validates
    assert rows["1101"][0]["revenue"] == 111
    assert rows["1101"][0]["create_time"] == "2026-02-05"
    assert rows["2317"][0]["revenue"] == 333
    assert publisher.signals == [(Capability.MONTH_REVENUE, "2026-02-02")]


async def test_a_second_run_adds_nothing_writes_nothing_and_signals_nothing(
    tmp_path: object,
) -> None:
    official = StubSource(_result([_revenue("1101"), _revenue("2317")]))
    publisher = RecordingPublisher()
    ctx = _ctx(tmp_path, official, publisher=publisher)
    _stored_revenue(ctx)
    await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)
    files = ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)

    again = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert again.run.status is RunStatus.SUCCESS
    assert again.run.rows_written == 0
    assert (again.companies_before, again.companies_after) == (3, 3)
    assert again.run.detail.startswith("added 0 companies (3 stored, 2 official)")
    assert ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2) == files
    assert len(publisher.signals) == 1


async def test_a_stored_company_the_official_source_lacks_is_kept(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([_revenue("2317")])))
    _stored_revenue(ctx)

    await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert sorted(_by_symbol(ctx)) == ["1101", "2317", "2330"]


async def test_statements_keep_the_finmind_detail_lines_and_add_whole_new_companies(
    tmp_path: object,
) -> None:
    detail = [_line("1101", f"Detail{i}", float(i)) for i in range(60)]
    official = StubSource(
        _result(
            [
                _line("1101", "TotalAssets", 5.0),
                _line("2317", "TotalAssets", 7.0),
                _line("2317", "Liabilities", 3.0),
            ],
            key="lines",
        )
    )
    ctx = _ctx(tmp_path, official)
    ctx.store.write_partition(BALANCE_SHEET_JOB.dataset, "TW", JUNE_30, detail)

    outcome = await fill_missing_companies(BALANCE_SHEET_JOB, JUNE_30, ctx)

    assert (outcome.companies_before, outcome.companies_after) == (1, 2)
    assert official.calls[0][1]["start_date"] == "2026-06-30"
    lines = _by_symbol(ctx, BALANCE_SHEET_JOB, JUNE_30)
    assert len(lines["1101"]) == 60  # the detail lines survive, the aggregate one is not added
    assert {r["item"] for r in lines["1101"]} == {f"Detail{i}" for i in range(60)}
    assert {r["item"] for r in lines["2317"]} == {"TotalAssets", "Liabilities"}  # all its lines
    assert outcome.run.rows_written == 2


async def test_a_company_is_absent_per_dataset_not_across_statements(tmp_path: object) -> None:
    ctx = _ctx(
        tmp_path, StubSource(_result([_line("1101", "Revenue", kind="income")], key="lines"))
    )
    ctx.store.write_partition(BALANCE_SHEET_JOB.dataset, "TW", JUNE_30, [_line("1101", "X")])
    ctx.store.write_partition(FINANCIALS_JOB.dataset, "TW", JUNE_30, [_line("2330", "Revenue")])

    balance = await fill_missing_companies(BALANCE_SHEET_JOB, JUNE_30, ctx)
    income = await fill_missing_companies(FINANCIALS_JOB, JUNE_30, ctx)

    assert balance.companies_added == 0  # 1101 already has its balance sheet
    assert income.companies_added == 1  # 1101 has no income statement yet


async def test_an_official_failure_leaves_the_partition_untouched(tmp_path: object) -> None:
    publisher = RecordingPublisher()
    failing = StubSource(_result([], HealthStatus.UNREACHABLE, "otc: HTTP 403"))
    ctx = _ctx(tmp_path, failing, publisher=publisher)
    _stored_revenue(ctx)
    files = ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.FAILED
    assert "partition left untouched: otc: HTTP 403" in outcome.run.detail
    assert (outcome.companies_before, outcome.companies_after) == (2, 2)
    assert ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2) == files
    assert publisher.signals == []


async def test_nothing_published_yet_is_a_skip_that_leaves_the_partition_alone(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([], HealthStatus.STALE, "not complete yet")))
    _stored_revenue(ctx)
    files = ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.SKIPPED
    assert ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2) == files


async def test_a_follower_does_nothing(tmp_path: object) -> None:
    official = StubSource(_result([_revenue("2317")]))
    ctx = _ctx(tmp_path, official, leader=NeverLeader())
    _stored_revenue(ctx)
    files = ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.SKIPPED
    assert outcome.run.detail == "not the leader - write skipped"
    assert official.calls == []
    assert ctx.store.partition_files(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2) == files


async def test_a_date_that_is_no_periods_partition_is_skipped_without_a_request(
    tmp_path: object,
) -> None:
    official = StubSource(_result([_revenue("2317")]))
    ctx = _ctx(tmp_path, official)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, date(2026, 2, 3), ctx)

    assert outcome.run.status is RunStatus.SKIPPED
    assert official.calls == []
    assert not ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", date(2026, 2, 3))


async def test_without_an_official_source_it_fails_and_writes_nothing(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, None)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.FAILED
    assert not ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)


async def test_an_empty_placeholder_partition_is_filled_with_the_official_companies(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([_revenue("1101"), _revenue("2317")])))
    ctx.store.write_partition(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2, [])

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert (outcome.companies_before, outcome.companies_after) == (0, 2)
    assert sorted(_by_symbol(ctx)) == ["1101", "2317"]


async def test_an_invalid_official_row_is_not_added(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([{**_revenue("2317"), "revenue": None}])))
    _stored_revenue(ctx)

    outcome = await fill_missing_companies(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert outcome.run.status is RunStatus.SKIPPED
    assert sorted(_by_symbol(ctx)) == ["1101", "2330"]


async def test_a_dataset_that_is_not_period_keyed_is_refused(tmp_path: object) -> None:
    with pytest.raises(ValueError, match="not a period-keyed dataset"):
        await fill_missing_companies(PRICE_EOD_JOB, MONDAY_FEB_2, _ctx(tmp_path, None))


def test_the_refreshable_datasets_are_the_four_period_keyed_ones() -> None:
    assert {s.dataset for s in period_keyed_specs()} == {
        "TaiwanStockMonthRevenue",
        "TaiwanStockFinancialStatements",
        "TaiwanStockBalanceSheet",
        "TaiwanStockCashFlowsStatement",
    }


def test_the_cli_takes_a_dataset_and_dates_together() -> None:
    args = parse_args(
        ["--yes", "--fill-missing-companies", "TaiwanStockMonthRevenue", "--dates", "2026-02-02"]
    )

    assert args.fill_missing_companies == "TaiwanStockMonthRevenue"
    assert args.dates == [MONDAY_FEB_2]
    for bad in (
        ["--fill-missing-companies", "TaiwanStockMonthRevenue"],
        ["--dates", "2026-02-02"],
        ["--fill-missing-companies", "TaiwanStockPrice", "--dates", "2026-02-02"],
        ["--fill-missing-companies", "TaiwanStockBalanceSheet", "--dates", "2026-6-30x"],
    ):
        with pytest.raises(SystemExit):
            parse_args(bad)


def _settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[arg-type]
        finmind_api_key="test-token",
        **overrides,  # type: ignore[arg-type]
    )


async def test_the_cli_prints_the_before_and_after_company_count(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path, StubSource(_result([_revenue("1101"), _revenue("2317")])))
    _stored_revenue(ctx)

    runs = await run_fill_missing_companies(
        _settings(),
        ctx,
        override=True,
        dataset="TaiwanStockMonthRevenue",
        dates=[MONDAY_FEB_2, date(2026, 2, 3)],
    )

    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("TaiwanStock")]
    assert out[0].startswith("TaiwanStockMonthRevenue 2026-02-02: companies 2 -> 3 (+1) [success]")
    assert "[skipped]" in out[1]
    assert [r.status for r in runs] == [RunStatus.SUCCESS, RunStatus.SKIPPED]


async def test_the_cli_needs_the_explicit_yes_and_never_runs_in_production(
    tmp_path: object,
) -> None:
    official = StubSource(_result([_revenue("2317")]))
    ctx = _ctx(tmp_path, official)
    _stored_revenue(ctx)

    refused = await run_fill_missing_companies(
        _settings(),
        ctx,
        override=False,
        dataset="TaiwanStockMonthRevenue",
        dates=[MONDAY_FEB_2],
    )

    assert refused == ()
    assert official.calls == []
    with pytest.raises(ProductionHardStopError):
        await run_fill_missing_companies(
            _settings(env="production"),
            ctx,
            override=True,
            dataset="TaiwanStockMonthRevenue",
            dates=[MONDAY_FEB_2],
        )
