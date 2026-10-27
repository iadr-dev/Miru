"""Which date partition a period-keyed filing lands under when its nominal date is not a trading
day (monthly revenue is filed under the 1st of the month after, a statement under its quarter
end): characterization of the behaviour before the fix, with a stub that applies the real period
rules of `sources/company_filings` and a plain weekday calendar. No network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, date, datetime

from ingest.backfill import backfill_range, detect_missing_trading_days
from ingest.context import IngestContext
from ingest.jobs import FINANCIALS_JOB, MONTH_REVENUE_JOB, run_whole_market_job
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.company_filings import month_revenue as revenue
from sources.company_filings import statements
from tests.ingest.test_whole_market_runner import RecordingPublisher

SUNDAY_FEB_1 = date(2026, 2, 1)  # January 2026's revenue is filed under this date
MONDAY_FEB_2 = date(2026, 2, 2)
SUNDAY_MAR_31 = date(2024, 3, 31)  # end of 2024Q1
MONDAY_APR_1 = date(2024, 4, 1)


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


def _ctx(tmp_path: object, filings: PeriodRuleFilings) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]
        filings=filings,
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        data_update_publisher=RecordingPublisher(),
    )


async def test_before_the_fix_a_sunday_first_is_skipped_and_nothing_is_stored(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    run = await run_whole_market_job(MONTH_REVENUE_JOB, SUNDAY_FEB_1, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "non-trading day (calendar-gated)"
    assert filings.calls == []
    assert not ctx.store.has_partition(MONTH_REVENUE_JOB.dataset, "TW", SUNDAY_FEB_1)


async def test_before_the_fix_the_monday_after_holds_only_an_empty_placeholder(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, PeriodRuleFilings())

    run = await run_whole_market_job(MONTH_REVENUE_JOB, MONDAY_FEB_2, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 0  # January's revenue was never asked for
    assert not ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", MONDAY_FEB_2)


async def test_before_the_fix_a_whole_backfill_around_the_month_start_never_asks_for_january(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    await backfill_range(
        MONTH_REVENUE_JOB, ctx, start=date(2026, 1, 26), end=date(2026, 2, 13), only_missing=True
    )

    assert all(asked != "2026-02-01" for _, asked in filings.calls)
    stored = [
        d
        for d in (date(2026, 2, 1), MONDAY_FEB_2)
        if ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", d)
    ]
    assert stored == []  # no January 2026 revenue anywhere


async def test_before_the_fix_a_normal_month_start_is_filed_under_the_first(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, PeriodRuleFilings())

    run = await run_whole_market_job(MONTH_REVENUE_JOB, date(2026, 4, 1), ctx)  # a Wednesday

    assert run.rows_written == 1
    assert ctx.store.has_rows(MONTH_REVENUE_JOB.dataset, "TW", date(2026, 4, 1))


async def test_before_the_fix_a_sunday_quarter_end_is_never_stored_either(
    tmp_path: object,
) -> None:
    filings = PeriodRuleFilings()
    ctx = _ctx(tmp_path, filings)

    skipped = await run_whole_market_job(FINANCIALS_JOB, SUNDAY_MAR_31, ctx)
    monday = await run_whole_market_job(FINANCIALS_JOB, MONDAY_APR_1, ctx)

    assert skipped.status is RunStatus.SKIPPED
    assert monday.rows_written == 0
    assert not ctx.store.has_rows(FINANCIALS_JOB.dataset, "TW", MONDAY_APR_1)


def test_before_the_fix_gap_detection_lists_the_monday_but_it_can_never_be_filled(
    tmp_path: object,
) -> None:
    ctx = _ctx(tmp_path, PeriodRuleFilings())

    missing = detect_missing_trading_days(
        MONTH_REVENUE_JOB, ctx, start=date(2026, 1, 30), end=date(2026, 2, 3)
    )

    assert missing == (date(2026, 1, 30), MONDAY_FEB_2, date(2026, 2, 3))
