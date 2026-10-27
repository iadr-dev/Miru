"""Which date partition a period-keyed filing lands under when its nominal date is not a trading
day (monthly revenue is filed under the 1st of the month after, a statement under its quarter
end): a period is stored under the first trading day on or after its nominal date, the sources are
asked for the nominal date, and the rows keep FinMind's own date fields. A stub applies the real
period rules of `sources/company_filings` over a weekday calendar. No network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

import pytest

from analytics.fundamentals import compute_revenue_yoy, load_symbol_month_revenue
from ingest.backfill import backfill_range, detect_missing_trading_days
from ingest.context import IngestContext
from ingest.jobs import (
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    FINANCIALS_JOB,
    MONTH_REVENUE_JOB,
    PRICE_EOD_JOB,
    run_whole_market_job,
)
from ingest.jobs.period_dates import PeriodAnchor, filing_date_for, partition_date_for
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.company_filings import month_revenue as revenue
from sources.company_filings import statements
from tests.ingest.test_whole_market_runner import RecordingPublisher, StubSource

SUNDAY_FEB_1 = date(2026, 2, 1)  # January 2026's revenue is filed under this date
MONDAY_FEB_2 = date(2026, 2, 2)
SUNDAY_MAR_1 = date(2026, 3, 1)
MONDAY_MAR_2 = date(2026, 3, 2)
SUNDAY_MAR_31 = date(2024, 3, 31)  # end of 2024Q1
MONDAY_APR_1 = date(2024, 4, 1)
WEEKDAYS = TradingDayCalendar()


class PeriodRuleFilings:
    """Stands in for the filings adapter: the REAL period rules decide what a date holds (rows for
    the date a period is filed under, "nothing by definition" for any other), with no request."""

    def __init__(self) -> None:
        self.calls: list[tuple[Capability, str]] = []

    async def fetch_whole_market(
        self, cap: Capability, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        asked = date.fromisoformat(str(params["start_date"]))
        self.calls.append((cap, asked.isoformat()))
        rows: list[dict[str, object]] = []
        if cap is Capability.MONTH_REVENUE:
            period = revenue.period_for(asked)
            if period is not None:
                rows = [
                    {
                        "symbol": "1101",
                        "market": "TW",
                        "trade_date": asked.isoformat(),
                        "country": "Taiwan",
                        "revenue": 5,
                        "revenue_month": period.month,
                        "revenue_year": period.year,
                        "create_time": None,
                    }
                ]
        else:
            quarter = statements.period_for(asked)
            if quarter is not None:
                rows = [
                    {
                        "symbol": "1101",
                        "market": "TW",
                        "period_end": asked.isoformat(),
                        "period_type": "quarterly",
                        "statement_type": "income_statement",
                        "item": "Revenue",
                        "origin_name": "營業收入",
                        "value": 5.0,
                    }
                ]
        return SourceResult(
            source_id="OFFICIAL_FILINGS",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub",
            payload={"rows" if cap is Capability.MONTH_REVENUE else "lines": rows},
            health=SourceHealth(status=HealthStatus.OK, detail="stub"),
        )


def _ctx(
    tmp_path: object,
    filings: PeriodRuleFilings | None,
    *,
    calendar: TradingDayCalendar = WEEKDAYS,
    finmind: StubSource | None = None,
) -> IngestContext:
    return IngestContext(
        finmind=finmind,  # type: ignore[arg-type]
        filings=filings,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=calendar,
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=RecordingPublisher(),
    )


@pytest.mark.parametrize(
    ("anchor", "nominal", "partition"),
    [
        (PeriodAnchor.MONTH_START, date(2026, 4, 1), date(2026, 4, 1)),  # a Wednesday
        (PeriodAnchor.MONTH_START, SUNDAY_FEB_1, MONDAY_FEB_2),
        (PeriodAnchor.MONTH_START, SUNDAY_MAR_1, MONDAY_MAR_2),
        (PeriodAnchor.MONTH_START, date(2025, 2, 1), date(2025, 2, 3)),  # a Saturday
        (PeriodAnchor.QUARTER_END, date(2026, 6, 30), date(2026, 6, 30)),  # a Tuesday
        (PeriodAnchor.QUARTER_END, SUNDAY_MAR_31, MONDAY_APR_1),
        (PeriodAnchor.QUARTER_END, date(2023, 9, 30), date(2023, 10, 2)),  # a Saturday
        (PeriodAnchor.QUARTER_END, date(2023, 12, 31), date(2024, 1, 1)),  # a Sunday
    ],
)
def test_a_period_is_stored_under_the_first_trading_day_on_or_after_its_nominal_date(
    anchor: PeriodAnchor, nominal: date, partition: date
) -> None:
    assert partition_date_for(nominal, WEEKDAYS) == partition
    assert filing_date_for(anchor, partition, WEEKDAYS) == nominal


def test_a_holiday_first_moves_the_partition_past_the_holiday_and_the_weekend() -> None:
    labour_day = date(2026, 5, 1)  # a Friday the exchange is closed
    calendar = TradingDayCalendar(closure_dates=frozenset({labour_day}))

    assert partition_date_for(labour_day, calendar) == date(2026, 5, 4)
    assert filing_date_for(PeriodAnchor.MONTH_START, date(2026, 5, 4), calendar) == labour_day
    assert filing_date_for(PeriodAnchor.MONTH_START, date(2026, 5, 5), calendar) is None


@pytest.mark.parametrize("anchor", list(PeriodAnchor))
def test_every_other_trading_day_is_no_periods_partition(anchor: PeriodAnchor) -> None:
    owners = [
        d
        for d in WEEKDAYS.business_days_between(date(2024, 1, 2), date(2025, 12, 31))
        if filing_date_for(anchor, d, WEEKDAYS) is not None
    ]

    # one partition per period, whatever the weekday: Feb 2024..Dec 2025 and 8 quarters
    assert len(owners) == (23 if anchor is PeriodAnchor.MONTH_START else 8)


async def test_a_sunday_first_is_filed_under_the_monday_with_the_rows_keeping_the_first(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 1
    assert filings.calls == [(Capability.MONTH_REVENUE, "2026-02-01")]  # asked for the nominal date
    (row,) = ctx.store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)
    assert (row["trade_date"], row["revenue_month"], row["revenue_year"]) == ("2026-02-01", 1, 2026)
    assert not ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", SUNDAY_FEB_1)


async def test_a_normal_month_start_is_unchanged(tmp_path: object) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, date(2026, 4, 1), ctx)

    assert run.rows_written == 1
    assert filings.calls == [(Capability.MONTH_REVENUE, "2026-04-01")]
    assert ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", date(2026, 4, 1))


async def test_the_other_days_of_the_month_still_hold_nothing_by_definition(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    runs = [
        await run_whole_market_job(MONTH_REVENUE_JOB, d, ctx)
        for d in (date(2026, 2, 3), date(2026, 2, 4), date(2026, 3, 3), date(2026, 2, 27))
    ]

    assert [r.rows_written for r in runs] == [0, 0, 0, 0]
    assert {r.status for r in runs} == {RunStatus.SUCCESS}
    # asked as themselves (none is a 1st), so the adapter answers its own "nothing by definition"
    assert [asked for _, asked in filings.calls] == [
        "2026-02-03",
        "2026-02-04",
        "2026-03-03",
        "2026-02-27",
    ]


async def test_a_statement_with_a_sunday_quarter_end_is_filed_under_the_monday(
    tmp_path: object,
) -> None:
    for job in (FINANCIALS_JOB, BALANCE_SHEET_JOB, CASH_FLOW_JOB):
        filings = PeriodRuleFilings()
        ctx = _ctx(tmp_path, filings)

        skipped = await run_whole_market_job(job, SUNDAY_MAR_31, ctx)
        run = await run_whole_market_job(job, MONDAY_APR_1, ctx)

        assert skipped.status is RunStatus.SKIPPED
        assert run.rows_written == 1
        assert filings.calls == [(job.capability, "2024-03-31")]
        (line,) = ctx.store.read_partition(job.dataset, "TW", MONDAY_APR_1)
        assert line["period_end"] == "2024-03-31"


async def test_finmind_alone_is_asked_for_the_nominal_date_too(tmp_path: object) -> None:
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2026-02-01",
            "country": "Taiwan",
            "revenue": 9,
            "revenue_month": 1,
            "revenue_year": 2026,
            "create_time": None,
        }
    ]
    finmind = StubSource(
        SourceResult(
            source_id="FINMIND",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub",
            payload={"rows": rows},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )
    )
    ctx = _ctx(tmp_path, None, finmind=finmind)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert run.rows_written == 1
    assert finmind.calls[0][1]["start_date"] == "2026-02-01"
    assert ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)


def test_only_the_period_keyed_datasets_carry_an_anchor() -> None:
    assert PRICE_EOD_JOB.period_anchor is None
    assert all(
        spec.period_anchor is not None
        for spec in (MONTH_REVENUE_JOB, FINANCIALS_JOB, BALANCE_SHEET_JOB, CASH_FLOW_JOB)
    )


async def test_gap_detection_lists_the_month_start_partition_and_a_run_fills_it(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)
    window = (date(2026, 1, 30), date(2026, 2, 3))

    before = detect_missing_trading_days(MONTH_REVENUE_JOB, ctx, start=window[0], end=window[1])
    runs = await backfill_range(MONTH_REVENUE_JOB, ctx, start=window[0], end=window[1])
    after = detect_missing_trading_days(MONTH_REVENUE_JOB, ctx, start=window[0], end=window[1])

    assert MONDAY_FEB_2 in before
    assert {r.status for r in runs} == {RunStatus.SUCCESS}
    assert after == (date(2026, 1, 30), date(2026, 2, 3))  # only the empty by-definition days
    assert ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)
    assert (Capability.MONTH_REVENUE, "2026-02-01") in filings.calls


async def test_every_month_of_a_year_gets_exactly_one_partition_with_rows(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, PeriodRuleFilings())

    await backfill_range(MONTH_REVENUE_JOB, ctx, start=date(2025, 1, 1), end=date(2025, 12, 31))

    filed = [
        d
        for d in WEEKDAYS.business_days_between(date(2025, 1, 1), date(2025, 12, 31))
        if ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", d)
    ]
    months = sorted(
        (
            int(r["revenue_year"]),  # type: ignore[call-overload]
            int(r["revenue_month"]),  # type: ignore[call-overload]
        )
        for d in filed
        for r in ctx.store.read_partition(MONTH_REVENUE_JOB.dataset, "TW", d)
    )
    assert len(filed) == 12
    assert months == [(2024, 12)] + [(2025, m) for m in range(1, 12)]


async def test_the_readers_find_a_month_filed_under_a_shifted_partition(tmp_path: object) -> None:
    ctx = _ctx(tmp_path, PeriodRuleFilings())
    # January 2026 (partition 2026-02-02) and January 2025 (partition 2025-02-03, a Saturday 1st)
    for partition in (date(2025, 2, 3), MONDAY_FEB_2):
        await run_whole_market_job(MONTH_REVENUE_JOB, partition, ctx)
    window = ctx.calendar.business_days_between(date(2025, 1, 1), date(2026, 3, 1))

    rows = load_symbol_month_revenue(ctx.store, market=Market.TW, run_dates=window, symbol="1101")

    assert sorted((r.revenue_year, r.revenue_month) for r in rows) == [(2025, 1), (2026, 1)]
    assert compute_revenue_yoy(rows) == 0.0  # same stub revenue a year apart: found, not None
